"""业务对 db 的读写封装。

API 风格:dict in,dict out,字段名跟 cognihub 的 ORM model 对齐(`mid`/
`bvid` 用作主键,`is_active` 0/1)。不用 ORM,纯 sqlite3 + dict。

调度器和 HTTP 路由都通过这一层访问数据,worker 任务线程也通过它写状态。
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterable

from toolkit.db import get_conn

# ---- subscriptions ----

_SUB_FIELDS = (
    "mid",
    "name",
    "is_active",
    "scan_interval_hours",
    "last_checked_at",
    "last_attempt_at",
    "last_error",
    "last_video_bvid",
    "created_at",
    "updated_at",
)


def upsert_subscription(
    mid: str,
    name: str,
    *,
    scan_interval_hours: float | None = None,
    is_active: bool = True,
) -> dict:
    conn = get_conn()
    existing = conn.execute(
        "SELECT * FROM subscriptions WHERE mid = ?", (str(mid),)
    ).fetchone()
    if existing:
        sets = ["name = ?", "is_active = ?", "updated_at = datetime('now')"]
        params: list[Any] = [name, 1 if is_active else 0]
        if scan_interval_hours is not None:
            sets.append("scan_interval_hours = ?")
            params.append(scan_interval_hours)
        params.append(str(mid))
        conn.execute(
            f"UPDATE subscriptions SET {', '.join(sets)} WHERE mid = ?",
            params,
        )
    else:
        conn.execute(
            """
            INSERT INTO subscriptions (mid, name, is_active, scan_interval_hours)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(mid),
                name,
                1 if is_active else 0,
                scan_interval_hours if scan_interval_hours is not None else 12,
            ),
        )
    return get_subscription(mid)  # type: ignore[return-value]


def get_subscription(mid: str) -> dict | None:
    return get_conn().execute(
        "SELECT * FROM subscriptions WHERE mid = ?", (str(mid),)
    ).fetchone()


def list_subscriptions(active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM subscriptions"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY created_at"
    return list(get_conn().execute(sql).fetchall())


def delete_subscription(mid: str) -> bool:
    cur = get_conn().execute("DELETE FROM subscriptions WHERE mid = ?", (str(mid),))
    return cur.rowcount > 0


def update_subscription_scan_state(
    mid: str,
    *,
    ok: bool,
    error: str | None = None,
    last_video_bvid: str | None = None,
) -> None:
    conn = get_conn()
    if ok:
        conn.execute(
            """
            UPDATE subscriptions
               SET last_checked_at = datetime('now'),
                   last_attempt_at = datetime('now'),
                   last_error      = NULL,
                   last_video_bvid = COALESCE(?, last_video_bvid),
                   updated_at      = datetime('now')
             WHERE mid = ?
            """,
            (last_video_bvid, str(mid)),
        )
    else:
        conn.execute(
            """
            UPDATE subscriptions
               SET last_attempt_at = datetime('now'),
                   last_error      = ?,
                   updated_at      = datetime('now')
             WHERE mid = ?
            """,
            (error, str(mid)),
        )


# ---- videos ----


def upsert_video(
    bvid: str,
    mid: str,
    *,
    title: str | None = None,
    description: str | None = None,
    pubdate: str | None = None,
    pubdate_ts: int | None = None,
    duration: str | None = None,
    play_count: int | None = None,
    cover_url: str | None = None,
    video_url: str | None = None,
) -> dict:
    """Insert or update a video row from a UP scan result.

    Does NOT touch download/transcription status — those move through their
    own update_* funcs once a download/transcribe job runs.
    """
    conn = get_conn()
    existing = conn.execute("SELECT bvid FROM videos WHERE bvid = ?", (bvid,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE videos
               SET mid = ?, title = COALESCE(?, title), description = COALESCE(?, description),
                   pubdate = COALESCE(?, pubdate), pubdate_ts = COALESCE(?, pubdate_ts),
                   duration = COALESCE(?, duration), play_count = COALESCE(?, play_count),
                   cover_url = COALESCE(?, cover_url), video_url = COALESCE(?, video_url),
                   updated_at = datetime('now')
             WHERE bvid = ?
            """,
            (
                str(mid), title, description, pubdate, pubdate_ts, duration,
                play_count, cover_url, video_url, bvid,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO videos (
                bvid, mid, title, description, pubdate, pubdate_ts,
                duration, play_count, cover_url, video_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bvid, str(mid), title, description, pubdate, pubdate_ts,
                duration, play_count, cover_url, video_url,
            ),
        )
    return get_video(bvid)  # type: ignore[return-value]


def get_video(bvid: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM videos WHERE bvid = ?", (bvid,)).fetchone()
    if row and row.get("segments_json"):
        try:
            row["segments"] = json.loads(row["segments_json"])
        except Exception:
            row["segments"] = []
    return row


def list_videos(
    *,
    mid: str | None = None,
    download_status: str | None = None,
    transcription_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    sql = "SELECT * FROM videos WHERE 1=1"
    params: list[Any] = []
    if mid:
        sql += " AND mid = ?"
        params.append(str(mid))
    if download_status:
        sql += " AND download_status = ?"
        params.append(download_status)
    if transcription_status:
        sql += " AND transcription_status = ?"
        params.append(transcription_status)
    sql += " ORDER BY pubdate_ts DESC NULLS LAST, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = list(get_conn().execute(sql, params).fetchall())
    return rows


def videos_pending_download(limit: int = 10) -> list[dict]:
    return list(get_conn().execute(
        "SELECT * FROM videos WHERE download_status = 'pending' "
        "ORDER BY pubdate_ts DESC NULLS LAST, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall())


def videos_pending_transcribe(limit: int = 10) -> list[dict]:
    """媒体已下载、转写未开始的视频。

    只取 transcription_status='pending' — 'queued'/'running' 是其他 worker
    线程已认领的,不要重复抓。
    """
    return list(get_conn().execute(
        """
        SELECT * FROM videos
         WHERE download_status = 'done'
           AND transcription_status = 'pending'
           AND media_path IS NOT NULL
         ORDER BY pubdate_ts DESC NULLS LAST, created_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall())


def reset_stale_active_states() -> tuple[int, int]:
    """启动时把 'queued'/'running' 残留状态回滚到 'pending'。

    上次进程崩溃/被 kill 时正在跑的任务,sqlite 状态还停在 queued/running,
    没人会再去推动它。重启后必须把它们刷回 pending,scheduler 下一轮才能
    重新认领。

    返回 (download_reset, transcribe_reset) 计数。
    """
    conn = get_conn()
    cur1 = conn.execute(
        "UPDATE videos SET download_status='pending', updated_at=datetime('now') "
        "WHERE download_status IN ('queued','running')"
    )
    cur2 = conn.execute(
        "UPDATE videos SET transcription_status='pending', updated_at=datetime('now') "
        "WHERE transcription_status IN ('queued','running')"
    )
    return cur1.rowcount, cur2.rowcount


def claim_video_for_download(bvid: str) -> bool:
    """乐观锁认领下载任务:状态 pending → queued。返回 True 表示这次调用
    成功认领了任务,False 表示别的线程已经先认领了(状态已经不是 pending)。
    """
    cur = get_conn().execute(
        "UPDATE videos SET download_status='queued', updated_at=datetime('now') "
        "WHERE bvid = ? AND download_status IN ('pending','error')",
        (bvid,),
    )
    return cur.rowcount > 0


def claim_video_for_transcribe(bvid: str) -> bool:
    """同 claim_video_for_download,转写版。"""
    cur = get_conn().execute(
        "UPDATE videos SET transcription_status='queued', updated_at=datetime('now') "
        "WHERE bvid = ? AND transcription_status IN ('pending','error')",
        (bvid,),
    )
    return cur.rowcount > 0


def update_video_download(
    bvid: str,
    *,
    status: str,
    media_path: str | None = None,
    error: str | None = None,
) -> None:
    get_conn().execute(
        """
        UPDATE videos
           SET download_status = ?,
               media_path      = COALESCE(?, media_path),
               download_error  = ?,
               updated_at      = datetime('now')
         WHERE bvid = ?
        """,
        (status, media_path, error, bvid),
    )


def update_video_transcription(
    bvid: str,
    *,
    status: str,
    transcription: str | None = None,
    timestamped_text: str | None = None,
    segments: list[dict] | None = None,
    language: str | None = None,
    audio_duration: float | None = None,
    error: str | None = None,
) -> None:
    seg_json = json.dumps(segments, ensure_ascii=False) if segments is not None else None
    get_conn().execute(
        """
        UPDATE videos
           SET transcription_status = ?,
               transcription        = COALESCE(?, transcription),
               timestamped_text     = COALESCE(?, timestamped_text),
               segments_json        = COALESCE(?, segments_json),
               language             = COALESCE(?, language),
               audio_duration       = COALESCE(?, audio_duration),
               transcription_error  = ?,
               updated_at           = datetime('now')
         WHERE bvid = ?
        """,
        (
            status,
            transcription,
            timestamped_text,
            seg_json,
            language,
            audio_duration,
            error,
            bvid,
        ),
    )


def ensure_video_row(bvid: str, mid: str = "manual") -> dict:
    """Best-effort:确保 bvid 在 videos 表里有一行(给手工 bvid 触发的下载用)。"""
    existing = get_video(bvid)
    if existing:
        return existing
    return upsert_video(bvid, mid)
