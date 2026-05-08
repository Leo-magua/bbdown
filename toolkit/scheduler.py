"""后台调度器:周期性扫订阅 + 扫盘补漏。

设计:
- 单 daemon 线程 60 秒一个 tick
- 主扫:每个 active 订阅看 last_attempt_at + scan_interval_hours,到点的拉投稿
  - 拉到列表 → 新 bvid 入 videos 表(download_status='pending')
  - 立即 submit_download(同 mid 一次最多 3 个)
  - 同 mid 之间 sleep 60-180s 随机,避免风控
- 扫盘补漏:扫 download_status='done' 但 transcription 未做的,入转写队列
- 启动时:先做一次 tick(给运维一个反馈),否则要等 60s 才动

启动:
    from toolkit.scheduler import start
    start()
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta

from toolkit import store
from toolkit.bili_downloader import find_media_file
from toolkit.bili_user import get_up_videos
from worker import DOWNLOAD_ROOT, submit_download, submit_transcribe

logger = logging.getLogger(__name__)

_TICK_SECONDS = 60
_SCAN_PER_MID_LIMIT = 3      # 每次发现的新 bvid,最多入下载队列前 N 个
_BACKLOG_DOWNLOAD = 5        # 扫盘补漏:每 tick 最多重新触发 N 个 pending 下载
_BACKLOG_TRANSCRIBE = 5      # 同上,转写
_INTER_MID_SLEEP_RANGE = (60, 180)  # 同 tick 内不同 mid 扫描间隔(秒)

_started = False
_lock = threading.Lock()


def start() -> None:
    """幂等启动后台调度线程。"""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    t = threading.Thread(target=_loop, name="scheduler", daemon=True)
    t.start()
    logger.info("scheduler started")


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:
            logger.exception("scheduler tick crashed")
        time.sleep(_TICK_SECONDS)


def tick() -> dict:
    """单轮扫描,返回简短统计供调试/HTTP 接口用。"""
    stats = {
        "subs_due": 0,
        "subs_scanned_ok": 0,
        "subs_scanned_fail": 0,
        "new_videos": 0,
        "backlog_downloads": 0,
        "backlog_transcribes": 0,
    }

    # 1. 主扫
    due_subs = []
    for sub in store.list_subscriptions(active_only=True):
        if _is_due(sub):
            due_subs.append(sub)
    stats["subs_due"] = len(due_subs)
    for idx, sub in enumerate(due_subs):
        new = scan_subscription(sub)
        if new is None:
            stats["subs_scanned_fail"] += 1
        else:
            stats["subs_scanned_ok"] += 1
            stats["new_videos"] += new
        # 同 tick 内多个 mid 之间错峰
        if idx < len(due_subs) - 1:
            time.sleep(random.uniform(*_INTER_MID_SLEEP_RANGE))

    # 2. 扫盘补漏:download
    for v in store.videos_pending_download(limit=_BACKLOG_DOWNLOAD):
        try:
            submit_download(v["bvid"])
            stats["backlog_downloads"] += 1
        except Exception:
            logger.exception("submit_download failed for %s", v["bvid"])

    # 3. 扫盘补漏:transcribe
    for v in store.videos_pending_transcribe(limit=_BACKLOG_TRANSCRIBE):
        media = v.get("media_path")
        if not media or not os.path.isfile(media):
            # media_path 失效或被删,降级:看默认目录
            out_dir = os.path.join(DOWNLOAD_ROOT, v["bvid"])
            media = find_media_file(out_dir)
            if not media:
                continue
            store.update_video_download(v["bvid"], status="done", media_path=media)
        try:
            submit_transcribe(v["bvid"])
            stats["backlog_transcribes"] += 1
        except Exception:
            logger.exception("submit_transcribe failed for %s", v["bvid"])

    return stats


def _is_due(sub: dict) -> bool:
    last = sub.get("last_attempt_at")
    if not last:
        return True
    try:
        # SQLite datetime('now') 输出 "YYYY-MM-DD HH:MM:SS" UTC
        last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    interval_h = sub.get("scan_interval_hours") or 12
    return datetime.utcnow() - last_dt >= timedelta(hours=interval_h)


def scan_subscription(sub: dict) -> int | None:
    """拉一次 UP 主投稿,把新 bvid 入库 + 触发下载。返回新增条数,失败返回 None。"""
    mid = sub["mid"]
    try:
        items = get_up_videos(mid, page_size=30)
    except Exception as exc:
        logger.warning("scan mid=%s failed: %s", mid, exc)
        store.update_subscription_scan_state(mid, ok=False, error=str(exc))
        return None

    new_bvids: list[str] = []
    latest_bvid: str | None = None
    for item in items:
        bvid = item.get("bvid")
        if not bvid:
            continue
        latest_bvid = latest_bvid or bvid
        existing = store.get_video(bvid)
        store.upsert_video(
            bvid,
            mid,
            title=item.get("title"),
            description=item.get("description"),
            pubdate=item.get("pubdate"),
            pubdate_ts=item.get("pubdate_ts"),
            duration=item.get("duration"),
            play_count=item.get("play_count"),
            cover_url=item.get("cover_url"),
            video_url=item.get("video_url"),
        )
        if not existing:
            new_bvids.append(bvid)

    store.update_subscription_scan_state(mid, ok=True, last_video_bvid=latest_bvid)
    logger.info("scan mid=%s ok, %d items, %d new", mid, len(items), len(new_bvids))

    # 立即触发前 N 个新 bvid 的下载;剩余等扫盘补漏轮到
    for bvid in new_bvids[:_SCAN_PER_MID_LIMIT]:
        try:
            submit_download(bvid)
        except Exception:
            logger.exception("submit_download for new bvid=%s failed", bvid)

    return len(new_bvids)
