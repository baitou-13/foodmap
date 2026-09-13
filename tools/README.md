# 腾讯频道帖子归档脚本

脚本通过 OCR 识别当前列表中的“详情”按钮，并用页面结构提取精确字段和图片。它保留浏览器中的发帖人筛选，按页面从上到下、时间由近到远归档；已有 `post.json` 会用于断点续跑和去重。

## 安装

```powershell
python -m pip install -r requirements.txt
python -m playwright install msedge
```

项目固定使用体积更小的 OpenCV 4.10 wheel，以规避部分网络代理对 44 MB 新版 wheel 的截断问题。

## 运行

默认不限制帖子数量，持续滚动到连续 8 次没有发现新帖子：

```powershell
python ocr_scraper.py --collect-only --queue post_queue.jsonl
```

推荐使用“脚本收集、Codex 下载”的分工模式：

如需限制总数，可传入 `--count 100`；省略 `--count` 或传入 `--count 0` 均表示不限数量。

此模式由 OCR 识别并点击每个可见的“详情”，将真实帖子 URL、帖子 ID、列表互动量等写入 `post_queue.jsonl`，不进入详情解析流程，也不下载图片。队列完成后，运行 `python qq_queue_downloader.py`，通过 QQ 官方 OpenAPI 补齐正文、精确时间和原图。下载器默认处理队列中的全部帖子，也可用 `--count 20` 限制数量。

脚本会打开独立的 Edge 用户目录 `.browser-profile`。首次运行时，在打开的浏览器内登录腾讯频道并设置发帖人为“今天也不想学习”；列表出现后回到终端按 Enter。后续运行会复用登录状态。

腾讯管理页可能长期保持网络请求；脚本只等待导航建立，最终以帖子列表出现作为加载成功标准，不再因为 30 秒内没有触发 `DOMContentLoaded` 而退出。

每条帖子保存为：

```text
post/2026-9-8/post.json
post/2026-9-8/image-01.jpg
post/2026-9-8/image-02.jpg
...
```

若同一天只有一条帖子，目录名为 `2026-9-8`；若同一天有多条，则该日目录依次为 `2026-9-8.1`、`2026-9-8.2`……。

所有图片都会转换成 JPG，避免 AVIF 无法直接预览。连续 8 次滚动没有新帖子时脚本会安全停止。

图片下载会自动携带详情页来源信息并校验文件；如果腾讯 CDN 拒绝或截断响应，脚本会回退为保存浏览器已经渲染出的图片。单张图片失败会记录在 `post.json` 的 `image_download_notes` 中，不再导致整条帖子中断。

详情页优先通过管理列表中对应的“详情”按钮打开，不再只依赖拼接 URL。腾讯页面加载较慢时，正文容器最多等待 4 分钟。若详情结构仍无法识别，`post/_failures/<帖子ID>/` 会保留 `failure.png` 和 `failure.html` 供诊断，多个失败不会互相覆盖。
