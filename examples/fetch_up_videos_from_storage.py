#!/usr/bin/env python3
"""
从 Playwright 的 storage_state.json 读取 Cookie，获取 UP 主全部投稿视频

用法：
    python examples/fetch_up_videos_from_storage.py 1023552416

说明：
    - 自动从 .trash/storage_state.json 提取 Cookie 并设置到环境变量
    - 如果 storage_state.json 在其他位置，修改 STORAGE_STATE_PATH 即可
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from toolkit.bili_user import get_up_videos

# Playwright storage_state.json 的路径
STORAGE_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", ".trash", "storage_state.json"
)


def load_cookies_from_storage_state(path: str) -> str:
    """从 Playwright 的 storage_state.json 中提取 cookie 字符串。"""
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)

    cookies = state.get("cookies", [])
    # 只提取 bilibili.com 域下的 cookie
    parts = []
    for c in cookies:
        if ".bilibili.com" in c.get("domain", ""):
            parts.append(f"{c['name']}={c['value']}")

    return "; ".join(parts)


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
        print("Usage: python fetch_up_videos_from_storage.py <mid> [output.json]")
        print("Example: python fetch_up_videos_from_storage.py 1023552416")
        sys.exit(1)

    mid = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else f"up_{mid}_videos.json"

    if not os.path.isfile(STORAGE_STATE_PATH):
        print(f"Error: storage_state.json not found at {STORAGE_STATE_PATH}")
        print("请先生成登录态文件，或修改脚本中的 STORAGE_STATE_PATH 路径。")
        sys.exit(1)

    # 读取 Cookie 并设置环境变量
    cookie_str = load_cookies_from_storage_state(STORAGE_STATE_PATH)
    os.environ["BILI_COOKIE"] = cookie_str
    print(f"Loaded {len(cookie_str.split(';'))} cookies from {STORAGE_STATE_PATH}\n")

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
