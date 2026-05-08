"""SQLite 连接管理 + 建表。

设计要点:
- 每线程一条连接(threading.local),sqlite3 连接对象不能跨线程共享
- WAL 模式,允许读写并发(scheduler 写、HTTP 路由读)
- dict-style row factory,业务层不操作 tuple
- init_db() 幂等,启动时调一次即可
"""

from __future__ import annotations

import os
import sqlite3
import threading

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "bili-worker.db",
)

_local = threading.local()


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = _row_to_dict
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            mid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            scan_interval_hours REAL NOT NULL DEFAULT 12,
            last_checked_at TEXT,
            last_attempt_at TEXT,
            last_error TEXT,
            last_video_bvid TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS videos (
            bvid TEXT PRIMARY KEY,
            mid TEXT NOT NULL,
            title TEXT,
            description TEXT,
            pubdate TEXT,
            pubdate_ts INTEGER,
            duration TEXT,
            play_count INTEGER,
            cover_url TEXT,
            video_url TEXT,
            media_path TEXT,
            download_status TEXT NOT NULL DEFAULT 'pending',
            download_error TEXT,
            transcription_status TEXT NOT NULL DEFAULT 'pending',
            transcription_error TEXT,
            transcription TEXT,
            timestamped_text TEXT,
            segments_json TEXT,
            language TEXT,
            audio_duration REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_videos_mid_pubdate ON videos(mid, pubdate_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_videos_download_status ON videos(download_status);
        CREATE INDEX IF NOT EXISTS idx_videos_transcription_status ON videos(transcription_status);
        """
    )
