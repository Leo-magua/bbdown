"""一次性脚本:从 cognihub 的 sqlite 拷贝订阅到本地 bili-worker.db。

用法:
    # 1) 直接给路径(本机已有 cognihub.db 副本)
    python tools/bootstrap_subscriptions.py /path/to/cognihub.db

    # 2) 从远端 ssh 拷过来再读
    python tools/bootstrap_subscriptions.py --ssh macmini --remote-path \\
        /Users/wendy/AllProject/cognihub/backend/cognihub.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toolkit import store
from toolkit.db import init_db


def fetch_remote_db(host: str, remote_path: str) -> str:
    tmp = tempfile.NamedTemporaryFile(prefix="cognihub-", suffix=".db", delete=False)
    tmp.close()
    print(f"scp {host}:{remote_path} → {tmp.name}")
    subprocess.run(
        ["scp", "-q", f"{host}:{remote_path}", tmp.name],
        check=True,
    )
    return tmp.name


def import_subs(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT bilibili_uid, name, is_active FROM up_subscriptions"
    ).fetchall()
    conn.close()
    print(f"found {len(rows)} subscriptions in cognihub")

    for r in rows:
        sub = store.upsert_subscription(
            mid=str(r["bilibili_uid"]),
            name=r["name"],
            is_active=bool(r["is_active"]),
        )
        print(f"  ✓ mid={sub['mid']} name={sub['name']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path", nargs="?", help="本地 cognihub.db 路径")
    parser.add_argument("--ssh", help="ssh host(配合 --remote-path)")
    parser.add_argument("--remote-path", help="远端 cognihub.db 路径")
    args = parser.parse_args()

    if args.ssh:
        if not args.remote_path:
            parser.error("--ssh 需要配合 --remote-path")
        path = fetch_remote_db(args.ssh, args.remote_path)
    elif args.db_path:
        path = args.db_path
    else:
        parser.error("需要 db_path 或 --ssh")

    init_db()
    import_subs(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
