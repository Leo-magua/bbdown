"""交互式扫码登录 B 站,把 cookies + localStorage 保存为 storage_state.json。

运行:
  python tools/bili_login.py

会弹出一个带头的 chromium,导航到 bilibili.com。扫码登录,回头页面
显示头像后,在终端按回车,脚本关闭浏览器并把登录态存到
`storage_state.json`(或 `BILI_STATE_PATH` 指定的路径)。

之后 bili_space_browser 在 headless 模式下加载这个 state,就是已登录
的浏览器会话,风控几乎不触发。

重跑这个脚本会覆盖旧的 state。Cookie 过期后重跑一次即可。
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

STATE_PATH = os.environ.get("BILI_STATE_PATH", "storage_state.json")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.goto("https://www.bilibili.com/", wait_until="domcontentloaded")
        print(
            "浏览器已打开 bilibili.com。\n"
            "请点击右上角 '登录',用 B 站 App 扫码。\n"
            "登录完成后(看到头像),回到终端按回车保存登录态。",
            flush=True,
        )
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("取消", file=sys.stderr)
            browser.close()
            return 1
        context.storage_state(path=STATE_PATH)
        print(f"登录态已保存到 {os.path.abspath(STATE_PATH)}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
