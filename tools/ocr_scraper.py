"""OCR-assisted Tencent Channel post scraper.

OCR is used only to locate visible ``详情`` buttons.  Once a detail page opens,
structured text and image URLs are read from the page so the archived result is
not limited by OCR accuracy.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.sync_api import BrowserContext, Page, TimeoutError, sync_playwright
from rapidocr_onnxruntime import RapidOCR


MANAGE_URL = "https://pd.qq.com/admin/90606771657298511/content-manage"
POST_URL_PREFIX = "https://pd.qq.com/g/SHOUSHOU1912/post/"
SCROLLER = "div.virtual-waterfall-container"
DETAIL_PARAM = 'a[dt-params*="sgrp_feed_id="]'


@dataclass(frozen=True)
class VisiblePost:
    post_id: str
    button_x: float
    button_y: float
    content_preview: str
    author: str
    posted_at_display: str
    engagement: dict[str, int | None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR-assisted post archiver")
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="maximum posts to process; 0 means unlimited (default)",
    )
    parser.add_argument("--output", type=Path, default=Path("post"))
    parser.add_argument("--profile", type=Path, default=Path(".browser-profile"))
    parser.add_argument("--headless", action="store_true", help="not recommended for first run")
    parser.add_argument("--start-url", default=MANAGE_URL)
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="OCR click Detail links and write a queue for Codex; do not scrape details",
    )
    parser.add_argument("--queue", type=Path, default=Path("post_queue.jsonl"))
    return parser.parse_args()


def wait_for_user(page: Page, start_url: str = MANAGE_URL) -> None:
    # Tencent keeps several requests open and can delay DOMContentLoaded for a
    # long time.  Reaching the requested URL is enough; the selector wait below
    # is the authoritative signal that the management app is ready.
    try:
        page.goto(start_url, wait_until="commit", timeout=120_000)
    except TimeoutError as exc:
        if "pd.qq.com/admin/" not in page.url:
            raise RuntimeError(f"管理页面导航失败，当前地址：{page.url}") from exc
        print("管理页面仍在后台加载，将继续等待列表出现。", file=sys.stderr)
    print("请在浏览器中登录并设置发帖人筛选；列表显示后回到终端按 Enter。")
    input()
    page.wait_for_selector(SCROLLER, timeout=120_000)


def integer_after(label: str, text: str) -> int | None:
    match = re.search(rf"{re.escape(label)}\s*(\d+)", text)
    return int(match.group(1)) if match else None


def visible_rows(page: Page) -> list[dict[str, Any]]:
    return page.locator(DETAIL_PARAM).evaluate_all(
        """links => links.map(a => {
          let row = a;
          for (let i = 0; i < 4 && row; i++) row = row.parentElement;
          const param = a.getAttribute('dt-params') || '';
          const id = (param.match(/sgrp_feed_id=([^&]+)/) || [])[1];
          const box = a.getBoundingClientRect();
          return {
            post_id: id,
            button_x: box.left + box.width / 2,
            button_y: box.top + box.height / 2,
            content_preview: row?.querySelector('.feed-detail-text')?.textContent?.trim() || '',
            author: row?.querySelector('.nick')?.textContent?.trim() || '',
            posted_at_display: row?.querySelector('.edit-time')?.textContent?.trim() || '',
            row_text: row?.innerText || ''
          };
        }).filter(x => x.post_id && x.button_x > 0 && x.button_y > 0)"""
    )


def ocr_detail_centers(page: Page, ocr: RapidOCR) -> list[tuple[float, float]]:
    image = Image.open(io.BytesIO(page.screenshot(type="png"))).convert("RGB")
    result, _ = ocr(image)
    centers: list[tuple[float, float]] = []
    for box, text, confidence in result or []:
        normalized = re.sub(r"\s+", "", text)
        if "详情" not in normalized or confidence < 0.55:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        centers.append((sum(xs) / len(xs), sum(ys) / len(ys)))
    return centers


def pair_rows_with_ocr(page: Page, ocr: RapidOCR) -> list[VisiblePost]:
    rows = visible_rows(page)
    centers = ocr_detail_centers(page, ocr)
    paired: list[VisiblePost] = []
    for row in rows:
        nearest = min(centers, key=lambda p: abs(p[1] - row["button_y"]), default=None)
        if nearest is None or abs(nearest[1] - row["button_y"]) > 32:
            continue
        text = row.pop("row_text")
        paired.append(
            VisiblePost(
                **row,
                engagement={
                    "views": integer_after("浏览", text),
                    "likes": integer_after("点赞", text),
                    "comments": integer_after("评论", text),
                    "shares": integer_after("分享", text),
                },
            )
        )
    return sorted(paired, key=lambda item: item.button_y)


def find_detail_root(page: Page, post_id: str):
    """Find the post body across both current and older Tencent layouts."""
    # Use one combined wait.  Waiting for three selectors sequentially caused a
    # race where Tencent inserted the correct node just after the final timeout.
    selector = ", ".join(
        [
            f".feed-content-{post_id}",
            f'[class*="feed-content-{post_id}"]',
            "[class*='feed-content-']",
        ]
    )
    root = page.locator(selector).first
    try:
        root.wait_for(state="attached", timeout=240_000)
        return root
    except TimeoutError as exc:
        # The title and OpenGraph metadata often arrive before the Vue body.
        # Give the final hydration task one short chance, then query again.
        page.wait_for_timeout(5_000)
        if root.count() > 0:
            return root
        raise RuntimeError(
            f"详情正文容器未出现；url={page.url!r}, title={page.title()!r}"
        ) from exc


def detail_data(page: Page, fallback: VisiblePost) -> dict[str, Any]:
    root = find_detail_root(page, fallback.post_id)
    text = root.inner_text()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    date = next((x for x in lines if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x)), fallback.posted_at_display)
    views = integer_after("浏览", text)
    # Post media use IDs ending in -1, -2, etc.; avatars are deliberately excluded.
    images = page.locator('img[id^="GuildNativeSon-"]').evaluate_all(
        "imgs => imgs.map(img => img.currentSrc || img.src).filter(Boolean)"
    )
    images = list(dict.fromkeys(images))
    author = page.locator("text=今天也不想学习").first.text_content() or fallback.author
    return {
        "post_id": fallback.post_id,
        "url": page.url,
        "author": author.strip(),
        "content": lines[0] if lines else fallback.content_preview,
        "posted_at": date,
        "engagement": {**fallback.engagement, "views": views or fallback.engagement["views"]},
        "image_urls": images,
    }


def download_images(
    context: BrowserContext,
    detail: Page,
    urls: list[str],
    directory: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    """Download media with an authenticated request and a rendered fallback.

    QQ's image CDN sometimes rejects a request without a Referer, returns a
    partially transferred AVIF, or serves an encoding that Pillow cannot open.
    A failure must not abort the entire post: as a last resort Playwright asks
    the browser to render that exact ``img`` node and saves a JPEG screenshot.
    """
    names: list[str] = []
    errors: list[dict[str, str]] = []
    media = detail.locator('img[id^="GuildNativeSon-"]')
    for number, url in enumerate(urls, 1):
        name = f"image-{number:02d}.jpg"
        target = directory / name
        request_error: Exception | None = None
        try:
            response = context.request.get(
                url,
                headers={
                    "Referer": detail.url,
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
                timeout=120_000,
                fail_on_status_code=False,
            )
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status}")
            body = response.body()
            content_type = response.headers.get("content-type", "")
            if not body or content_type.startswith(("text/", "application/json")):
                raise RuntimeError(f"unexpected content-type {content_type!r}")
            with Image.open(io.BytesIO(body)) as source:
                source.load()  # detect truncated/corrupt responses before writing
                source.convert("RGB").save(target, "JPEG", quality=95, subsampling=0)
        except Exception as exc:
            request_error = exc

        if request_error is not None:
            try:
                node = media.nth(number - 1)
                node.wait_for(state="visible", timeout=20_000)
                node.scroll_into_view_if_needed(timeout=20_000)
                # Wait until the browser has decoded the image, not just added the tag.
                for _ in range(40):
                    if node.evaluate("img => img.complete && img.naturalWidth > 0"):
                        break
                    detail.wait_for_timeout(250)
                else:
                    raise RuntimeError("browser did not decode the image")
                rendered = node.screenshot(type="jpeg", quality=95, timeout=30_000)
                target.write_bytes(rendered)
                errors.append({
                    "image": name,
                    "warning": f"CDN download failed ({request_error}); saved rendered fallback",
                })
            except Exception as fallback_error:
                errors.append({
                    "image": name,
                    "error": f"download: {request_error}; rendered fallback: {fallback_error}",
                    "url": url,
                })
                print(f"  图片 {number} 下载失败: {fallback_error}", file=sys.stderr)
                continue

        # Re-open every written JPEG so zero-byte/corrupt files never enter JSON.
        try:
            with Image.open(target) as check:
                check.verify()
            names.append(name)
        except Exception as exc:
            target.unlink(missing_ok=True)
            errors.append({"image": name, "error": f"saved file verification failed: {exc}", "url": url})
    return names, errors


def open_detail(context: BrowserContext, manage: Page, item: VisiblePost) -> Page:
    """Open Detail through the management UI, with direct URL as fallback."""
    detail: Page | None = None
    try:
        with context.expect_page(timeout=20_000) as opened:
            manage.mouse.click(item.button_x, item.button_y)
        detail = opened.value
        detail.wait_for_load_state("domcontentloaded", timeout=90_000)
        if item.post_id not in detail.url:
            raise RuntimeError(f"详情按钮打开了非预期页面: {detail.url}")
        return detail
    except Exception as click_error:
        if detail is not None:
            detail.close()
        print(f"  点击详情失败，尝试直接地址: {click_error}", file=sys.stderr)
        detail = context.new_page()
        detail.goto(
            POST_URL_PREFIX + item.post_id,
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        return detail


def archive_post(context: BrowserContext, manage: Page, item: VisiblePost, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    detail = open_detail(context, manage, item)
    try:
        try:
            data = detail_data(detail, item)
        except Exception:
            # Preserve enough evidence to diagnose login, routing, or site changes.
            failure_dir = directory.parent / "_failures" / item.post_id
            failure_dir.mkdir(parents=True, exist_ok=True)
            (failure_dir / "failure.html").write_text(detail.content(), encoding="utf-8")
            detail.screenshot(path=str(failure_dir / "failure.png"), full_page=True)
            raise
        image_names, image_errors = download_images(
            context,
            detail,
            data.pop("image_urls"),
            directory,
        )
        data["images"] = image_names
        if image_errors:
            data["image_download_notes"] = image_errors
        (directory / "post.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        detail.close()


def completed_ids(output: Path) -> set[str]:
    result: set[str] = set()
    for path in output.glob("*/post.json"):
        try:
            result.add(json.loads(path.read_text(encoding="utf-8-sig"))["post_id"])
        except (OSError, KeyError, json.JSONDecodeError):
            pass
    return result


def queued_ids(path: Path) -> set[str]:
    result: set[str] = set()
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            result.add(json.loads(line)["post_id"])
        except (KeyError, json.JSONDecodeError):
            pass
    return result


def append_queue(path: Path, item: VisiblePost, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "post_id": item.post_id,
        "url": url,
        "author": item.author,
        "content_preview": item.content_preview,
        "posted_at_display": item.posted_at_display,
        "engagement": item.engagement,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def collect_queue(context: BrowserContext, manage: Page, args: argparse.Namespace) -> None:
    """Use OCR coordinates to click Detail and persist the resulting URLs."""
    ocr = RapidOCR()
    scroller = manage.locator(SCROLLER).first
    scroller.press("Home")
    queued = queued_ids(args.queue)
    seen: set[str] = set()
    no_progress = 0

    def below_limit() -> bool:
        return args.count <= 0 or len(queued) < args.count

    while below_limit() and no_progress < 8:
        items = pair_rows_with_ocr(manage, ocr)
        new_items = [x for x in items if x.post_id not in queued and x.post_id not in seen]
        for item in new_items:
            seen.add(item.post_id)
            try:
                with context.expect_page(timeout=15_000) as opened:
                    manage.mouse.click(item.button_x, item.button_y)
                detail = opened.value
                try:
                    detail.wait_for_load_state("domcontentloaded", timeout=60_000)
                    url = detail.url
                    if item.post_id not in url:
                        raise RuntimeError(f"打开了非预期页面: {url}")
                    append_queue(args.queue, item, url)
                    queued.add(item.post_id)
                    limit = str(args.count) if args.count > 0 else "∞"
                    print(f"[队列 {len(queued)}/{limit}] {item.posted_at_display} {item.content_preview[:40]}")
                finally:
                    detail.close()
            except Exception as exc:
                print(f"  无法记录 {item.post_id}: {exc}", file=sys.stderr)

        before = {row["post_id"] for row in visible_rows(manage)}
        scroller.press("PageDown")
        time.sleep(1.2)
        after = {row["post_id"] for row in visible_rows(manage)}
        no_progress = no_progress + 1 if after == before else 0

    print(f"队列收集完成：{len(queued)} 条，文件：{args.queue.resolve()}")


def run(args: argparse.Namespace) -> None:
    args.output.mkdir(parents=True, exist_ok=True)
    done = completed_ids(args.output)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.profile.resolve()),
            channel="msedge",
            headless=args.headless,
            viewport={"width": 1500, "height": 950},
        )
        manage = context.pages[0] if context.pages else context.new_page()
        wait_for_user(manage, args.start_url)
        if args.collect_only:
            collect_queue(context, manage, args)
            context.close()
            return
        ocr = RapidOCR()
        scroller = manage.locator(SCROLLER).first
        scroller.press("Home")
        archived = len(done)
        no_progress = 0
        seen_this_run: set[str] = set()

        def below_limit() -> bool:
            return args.count <= 0 or archived < args.count

        while below_limit() and no_progress < 8:
            items = pair_rows_with_ocr(manage, ocr)
            new_items = [x for x in items if x.post_id not in done and x.post_id not in seen_this_run]
            for item in new_items:
                seen_this_run.add(item.post_id)
                index = archived + 1
                target = args.output / f"post-{index:03d}"
                limit = str(args.count) if args.count > 0 else "∞"
                print(f"[{index}/{limit}] {item.posted_at_display} {item.content_preview[:40]}")
                try:
                    archive_post(context, manage, item, target)
                    done.add(item.post_id)
                    archived += 1
                except Exception as exc:  # preserve progress and continue to the next row
                    print(f"  失败: {exc}", file=sys.stderr)
                if args.count > 0 and archived >= args.count:
                    break

            previous_count = len(seen_this_run)
            scroller.press("PageDown")
            time.sleep(1.2)
            current_ids = {row["post_id"] for row in visible_rows(manage)}
            no_progress = no_progress + 1 if current_ids.issubset(seen_this_run) else 0
            if len(seen_this_run) == previous_count and no_progress >= 8:
                break

        context.close()
        print(f"完成：本次发现 {len(seen_this_run)} 条，目录中已归档 {archived} 条。")


if __name__ == "__main__":
    run(parse_args())
