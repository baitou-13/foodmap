#!/usr/bin/env python3
"""用 QQ 官方 OpenAPI 补全 OCR 帖子队列的正文和图片。"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


API_BASE = "https://api.bot.qq.com"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def request_json(method: str, url: str, *, token=None, payload=None):
    headers = {}
    body = None
    if token:
        headers["Authorization"] = f"QQBot {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def get_token() -> str:
    app_id = os.environ.get("QQ_APP_ID", "").strip()
    secret = os.environ.get("QQ_APP_SECRET", "").strip()
    if not app_id or not secret:
        raise RuntimeError(".env 中缺少 QQ_APP_ID 或 QQ_APP_SECRET")
    data = request_json(
        "POST",
        f"{API_BASE}/app/getAppAccessToken",
        payload={"appId": app_id, "clientSecret": secret},
    )
    token = data.get("access_token")
    if not token:
        raise RuntimeError("QQ API 未返回 access_token")
    return token


def parse_content(raw: str):
    try:
        rich = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw or "", []
    texts, images = [], []

    def walk(node):
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, dict) and isinstance(text.get("text"), str):
                texts.append(text["text"])
            image = node.get("image")
            if isinstance(image, dict):
                plat = image.get("plat_image") or {}
                if plat.get("url"):
                    images.append(plat)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(rich)
    return "\n".join(part for part in texts if part), images


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        path.write_bytes(response.read())


def already_complete(directory: Path, post_id: str) -> bool:
    metadata = directory / "post.json"
    if not metadata.exists():
        return False
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return data.get("post_id") == post_id and all(
        (directory / name).exists() for name in data.get("images", [])
    )


def find_post_directory(output_root: Path, post_id: str):
    for metadata in output_root.glob("*/post.json"):
        try:
            data = json.loads(metadata.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("post_id") == post_id:
            return metadata.parent
    return None


def post_date(value: str) -> str:
    date_part = (value or "").split("T", 1)[0]
    parsed = datetime.strptime(date_part, "%Y-%m-%d")
    return f"{parsed.year}-{parsed.month}-{parsed.day}"


def organize_by_date(output_root: Path) -> None:
    """按日期命名；同一天多帖时，该日所有目录使用 .1、.2……。"""
    posts = []
    for metadata in output_root.glob("*/post.json"):
        try:
            data = json.loads(metadata.read_text(encoding="utf-8-sig"))
            date = post_date(data.get("posted_at", ""))
            index = int(data.get("index", 0))
            posts.append((index, data.get("post_id", ""), date, metadata.parent))
        except (ValueError, json.JSONDecodeError, OSError):
            continue
    posts.sort(key=lambda item: (item[2], item[0], item[1]))
    counts = Counter(item[2] for item in posts)
    positions = defaultdict(int)
    planned = []
    for _, _, date, source in posts:
        positions[date] += 1
        name = date if counts[date] == 1 else f"{date}.{positions[date]}"
        planned.append((source, output_root / name))

    sources = {source.resolve() for source, _ in planned}
    for source, target in planned:
        if target.exists() and target.resolve() not in sources:
            raise RuntimeError(f"目标目录已存在且不属于帖子归档：{target}")

    staged = []
    for number, (source, target) in enumerate(planned, 1):
        temporary = output_root / f".rename-{number:06d}"
        if source != temporary:
            source.rename(temporary)
        staged.append((temporary, target))
    for temporary, target in staged:
        temporary.rename(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default="post_queue.jsonl")
    parser.add_argument("--output", default="post")
    parser.add_argument("--channel", default="669874920")
    parser.add_argument("--count", type=int, default=0, help="0 表示处理队列中的全部帖子")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    load_env(root / ".env")
    queue_path = (root / args.queue).resolve()
    output_root = (root / args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.count > 0:
        rows = rows[: args.count]
    token = get_token()

    completed = 0
    failed = []
    for index, row in enumerate(rows, 1):
        post_id = row["post_id"]
        directory = find_post_directory(output_root, post_id) or output_root / f".pending-{index:06d}"
        directory.mkdir(parents=True, exist_ok=True)
        if already_complete(directory, post_id):
            print(f"[{index}/{len(rows)}] 已存在，跳过 {post_id}")
            completed += 1
            continue
        try:
            api = request_json(
                "GET",
                f"{API_BASE}/channels/{args.channel}/threads/{post_id}",
                token=token,
            )
            thread = api["thread"]
            info = thread["thread_info"]
            content, remote_images = parse_content(info.get("content", ""))
            local_images = []
            for image_index, image in enumerate(remote_images, 1):
                filename = f"image-{image_index:02d}.jpg"
                download(image["url"], directory / filename)
                local_images.append(filename)
            posted_at = info.get("date_time", "")
            record = {
                "index": index,
                "post_id": post_id,
                "url": row.get("url"),
                "author": row.get("author"),
                "content": content or info.get("title", ""),
                "title": info.get("title", ""),
                "posted_at": posted_at,
                "engagement": row.get("engagement", {}),
                "images": local_images,
                "source": "QQ OpenAPI + content management queue",
            }
            (directory / "post.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            engagement = record["engagement"]
            readme = (
                f"# 第 {index} 条帖子\n\n"
                f"- 正文：{record['content']}\n"
                f"- 发帖人：{record['author']}\n"
                f"- 发帖时间：{posted_at}\n"
                f"- 互动量：浏览 {engagement.get('views', 0)}、点赞 {engagement.get('likes', 0)}、"
                f"评论 {engagement.get('comments', 0)}、分享 {engagement.get('shares', 0)}\n"
                f"- 帖子 ID：`{post_id}`\n"
                f"- 图片：{', '.join(local_images) if local_images else '无'}\n"
            )
            (directory / "README.md").write_text(readme, encoding="utf-8")
            completed += 1
            print(f"[{index}/{len(rows)}] 完成 {post_id}，图片 {len(local_images)} 张")
        except Exception as exc:  # 单条失败不影响后续队列
            failed.append({"index": index, "post_id": post_id, "error": str(exc)})
            print(f"[{index}/{len(rows)}] 失败 {post_id}: {exc}")

    (output_root / "api_failures.json").write_text(
        json.dumps(failed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    organize_by_date(output_root)
    print(f"完成 {completed}/{len(rows)}，失败 {len(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
