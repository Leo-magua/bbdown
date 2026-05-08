#!/usr/bin/env python3
"""
获取 B 站 UP 主全部投稿视频示例

依赖：
    pip install requests

用法：
    # 先设置 Cookie（从浏览器 DevTools 复制）
    export BILI_COOKIE="SESSDATA=xxx; bili_jct=xxx; DedeUserID=xxx; buvid3=xxx; buvid4=xxx"

    # 运行
    python examples/fetch_up_videos.py 1023552416

说明：
    - B 站 space API 必须登录态，否则返回 412
    - 如果系统挂着代理，代码内部已设置 trust_env=False 规避
    - 详见 docs/bilibili-space-api-analysis.md
"""

import json
import os
import sys
import time

# 将项目根目录加入路径，以便导入 toolkit
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from toolkit.bili_user import get_up_videos


def fetch_all_up_videos(mid: int | str, page_size: int = 30) -> list[dict]:
    """分页获取指定 UP 主的全部投稿视频。"""
    all_videos = []
    page = 1

    while True:
        print(f"Fetching page {page}...", end=" ", flush=True)
        try:
            videos = get_up_videos(mid, page_size=page_size, page=page)
        except Exception as e:
            print(f"\nError on page {page}: {e}")
            break

        if not videos:
            print("no more videos")
            break

        all_videos.extend(videos)
        print(f"got {len(videos)} videos, total: {len(all_videos)}")

        if len(videos) < page_size:
            break

        page += 1
        time.sleep(1.5)

    return all_videos


def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_up_videos.py <mid> [output.json]")
        print("Example: python fetch_up_videos.py 1023552416")
        sys.exit(1)

    mid = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else f"up_{mid}_videos.json"

    if not os.environ.get("BILI_COOKIE") and not os.environ.get("BILI_COOKIES_TXT"):
        print("Warning: BILI_COOKIE or BILI_COOKIES_TXT not set.")
        print("B 站 space API 需要登录态 Cookie，否则大概率返回 412。")
        print("请从浏览器 DevTools 复制 document.cookie 后设置环境变量。\n")

    videos = fetch_all_up_videos(mid)

    result = {
        "mid": mid,
        "total": len(videos),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "videos": videos,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Done! Total {len(videos)} videos saved to {output_file}")
    for v in videos[:5]:
        pub = v.get("pubdate", "N/A")
        print(f"  [{pub}] {v['bvid']}: {v['title'][:50]}")


if __name__ == "__main__":
    main()
