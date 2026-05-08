"""
B站视频下载封装（基于 you-get）

核心经验：
1. 必须使用 Homebrew 安装的 you-get: /opt/homebrew/bin/you-get
2. 不要使用 pip install you-get（Python 3.12 + dukpy 有兼容性问题）
3. 无需 Cookie 可下载 480P，有 Cookie 可下载 720P+
4. you-get 会自动下载弹幕文件（.cmt.xml）
5. 输出信息在 stderr，returncode == 0 表示成功
"""

import os
import re
import shutil
import subprocess
from typing import List, Dict, Optional

YOU_GET_PATH = "/opt/homebrew/bin/you-get"


def _find_you_get() -> str:
    """查找可用的 you-get 路径"""
    # 优先使用 Homebrew 版本
    if os.path.exists(YOU_GET_PATH):
        return YOU_GET_PATH
    # 回退到 PATH 搜索
    path = shutil.which("you-get")
    if path:
        return path
    raise FileNotFoundError("you-get 未找到，请运行: brew install you-get")


def download_video(
    bvid: str,
    output_dir: str,
    cookies_file: Optional[str] = None,
    timeout: int = 600,
) -> Dict:
    """
    使用 you-get 下载B站视频

    Args:
        bvid: BV号（如 BV1xxxxx）
        output_dir: 输出目录
        cookies_file: 可选的 Cookie 文件路径（Netscape 格式），用于下载高清
        timeout: 下载超时时间（秒）

    Returns:
        {
            "success": bool,
            "message": str,
            "files": [{"name": str, "size": int, "path": str}, ...],
            "output_dir": str,
        }
    """
    os.makedirs(output_dir, exist_ok=True)
    url = f"https://www.bilibili.com/video/{bvid}"
    you_get = _find_you_get()

    cmd = [you_get, "-o", output_dir]
    if cookies_file:
        cmd.extend(["-c", cookies_file])
    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        # you-get 输出到 stderr
        output = result.stderr or result.stdout or ""

        if result.returncode != 0:
            # 尝试提取错误信息
            error_lines = [l for l in output.split('\n') if 'error' in l.lower() or 'failed' in l.lower()]
            error_msg = error_lines[-1] if error_lines else output[:300]
            return {
                "success": False,
                "message": f"下载失败: {error_msg}",
                "files": [],
                "output_dir": output_dir,
            }

        # 扫描下载的文件
        files = []
        for f in os.listdir(output_dir):
            fpath = os.path.join(output_dir, f)
            if os.path.isfile(fpath):
                files.append({
                    "name": f,
                    "size": os.path.getsize(fpath),
                    "path": fpath,
                })

        return {
            "success": True,
            "message": f"下载完成，共 {len(files)} 个文件",
            "files": files,
            "output_dir": output_dir,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"下载超时（>{timeout}秒）",
            "files": [],
            "output_dir": output_dir,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"异常: {str(e)}",
            "files": [],
            "output_dir": output_dir,
        }


def download_videos(
    bvids: List[str],
    base_output_dir: str = "./downloads",
    cookies_file: Optional[str] = None,
    delay_range: tuple = (2.0, 4.0),
) -> List[Dict]:
    """
    批量下载多个视频

    Args:
        bvids: BV号列表
        base_output_dir: 基础输出目录，每个视频会在其下创建 {bvid}/ 子目录
        cookies_file: 可选的 Cookie 文件
        delay_range: 视频之间的随机延迟（秒）

    Returns:
        每个视频的下载结果列表
    """
    import time
    import random

    results = []
    for bvid in bvids:
        output_dir = os.path.join(base_output_dir, bvid)
        result = download_video(bvid, output_dir, cookies_file=cookies_file)
        results.append(result)
        print(f"[{bvid}] {result['message']}")

        if result["success"]:
            time.sleep(random.uniform(*delay_range))

    return results


def find_media_file(directory: str) -> Optional[str]:
    """
    在目录中查找第一个可用的音频/视频文件

    支持的格式: mp4, m4a, mp3, wav, webm, flv, aac
    """
    if not os.path.exists(directory):
        return None

    for f in os.listdir(directory):
        if f.lower().endswith(('.mp4', '.m4a', '.mp3', '.wav', '.webm', '.flv', '.aac')):
            return os.path.join(directory, f)
    return None


# ========== 命令行测试 ==========
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python bili_downloader.py <bvid> [output_dir]")
        print("Example: python bili_downloader.py BV1nPq2BoEf3 ./downloads")
        sys.exit(1)

    bvid = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else f"./downloads/{bvid}"

    result = download_video(bvid, out_dir)
    print(f"\n结果: {result['message']}")
    if result["files"]:
        for f in result["files"]:
            size_mb = f['size'] / (1024 * 1024)
            print(f"  📄 {f['name']} ({size_mb:.1f} MB)")
