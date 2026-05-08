"""bili-worker Flask 服务入口。

对外暴露 B 站下载、转写、UP 主视频列表 HTTP API,以及订阅管理 + 数据库
读取的端点。启动时自动建表 + 启动后台调度器。
"""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, request
from flask_cors import CORS

from toolkit import scheduler, store
from toolkit.bili_user import get_up_videos, get_video_info
from toolkit.db import init_db
from worker import get_task, submit_download, submit_transcribe, to_status_dict

logger = logging.getLogger(__name__)


def _public_video(row: dict) -> dict:
    """把 DB 行转成给前端用的形状(把 segments_json 解析出来)。"""
    if not row:
        return row
    out = dict(row)
    raw_segments = out.pop("segments_json", None)
    if raw_segments:
        try:
            import json
            out["segments"] = json.loads(raw_segments)
        except Exception:
            out["segments"] = []
    else:
        out["segments"] = []
    return out


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)

    init_db()
    reset_d, reset_t = store.reset_stale_active_states()
    if reset_d or reset_t:
        logger.info(
            "reset stale states: %d downloads, %d transcribes back to pending",
            reset_d, reset_t,
        )

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "bili-worker"})

    @app.post("/api/download")
    def download():
        data = request.get_json(silent=True) or {}
        bvid = (data.get("bvid") or "").strip()
        if not bvid:
            # 兼容老的 bilibili-summarizer 字段 bvids=[...]
            bvids = data.get("bvids") or []
            if isinstance(bvids, list) and bvids:
                bvid = str(bvids[0]).strip()
        if not bvid:
            return jsonify({"error": "bvid required"}), 400
        media_type = data.get("type", "audio")
        if media_type not in ("audio", "video", "merged"):
            media_type = "audio"
        task = submit_download(bvid, media_type)
        return jsonify(
            {"task_id": task.task_id, "task_ids": [task.task_id], "status": task.status}
        )

    @app.get("/api/status/<task_id>")
    def download_status(task_id: str):
        task = get_task(task_id)
        if not task:
            return jsonify({"error": "task not found", "status": "unknown"}), 404
        return jsonify(to_status_dict(task))

    @app.post("/api/transcribe")
    def transcribe():
        data = request.get_json(silent=True) or {}
        bvid = (data.get("bvid") or "").strip()
        if not bvid:
            return jsonify({"error": "bvid required"}), 400
        language = data.get("language", "zh")
        task = submit_transcribe(bvid, language=language)
        return jsonify({"task_id": task.task_id, "status": task.status})

    @app.get("/api/transcribe/status/<task_id>")
    def transcribe_status(task_id: str):
        task = get_task(task_id)
        if not task:
            return jsonify({"error": "task not found", "status": "unknown"}), 404
        return jsonify(to_status_dict(task))

    @app.get("/api/up/<mid>/videos")
    def up_videos(mid: str):
        """实时打 B 站,旁路 db。"""
        limit = int(request.args.get("limit") or 30)
        try:
            videos = get_up_videos(mid, page_size=min(max(limit, 1), 50), page=1)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502
        return jsonify({"videos": videos, "count": len(videos)})

    @app.get("/api/bvid/<bvid>/info")
    def video_info(bvid: str):
        try:
            return jsonify(get_video_info(bvid))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502

    # ---- 订阅管理 ----

    @app.get("/api/subscriptions")
    def list_subscriptions_route():
        return jsonify({"subscriptions": store.list_subscriptions()})

    @app.post("/api/subscriptions")
    def add_subscription():
        data = request.get_json(silent=True) or {}
        mid = str(data.get("mid") or "").strip()
        name = (data.get("name") or "").strip()
        if not mid or not name:
            return jsonify({"error": "mid and name required"}), 400
        scan_interval = data.get("scan_interval_hours")
        sub = store.upsert_subscription(
            mid, name,
            scan_interval_hours=float(scan_interval) if scan_interval is not None else None,
            is_active=bool(data.get("is_active", True)),
        )
        return jsonify({"subscription": sub})

    @app.delete("/api/subscriptions/<mid>")
    def delete_subscription_route(mid: str):
        ok = store.delete_subscription(mid)
        if not ok:
            return jsonify({"error": "subscription not found"}), 404
        return jsonify({"ok": True})

    @app.post("/api/subscriptions/<mid>/scan-now")
    def scan_now(mid: str):
        sub = store.get_subscription(mid)
        if not sub:
            return jsonify({"error": "subscription not found"}), 404
        new_count = scheduler.scan_subscription(sub)
        if new_count is None:
            sub2 = store.get_subscription(mid)
            return jsonify({"error": (sub2 or {}).get("last_error", "scan failed")}), 502
        return jsonify({"ok": True, "new": new_count})

    # ---- 视频读取(给 cognihub) ----

    @app.get("/api/videos")
    def list_videos_route():
        mid = request.args.get("mid")
        download_status = request.args.get("download_status")
        transcription_status = request.args.get("transcription_status")
        try:
            limit = min(int(request.args.get("limit") or 50), 200)
            offset = max(int(request.args.get("offset") or 0), 0)
        except ValueError:
            return jsonify({"error": "invalid limit/offset"}), 400
        rows = store.list_videos(
            mid=mid,
            download_status=download_status,
            transcription_status=transcription_status,
            limit=limit,
            offset=offset,
        )
        # 列表里把 segments_json 干掉,免得 payload 太大;明细才返回 segments
        for r in rows:
            r.pop("segments_json", None)
        return jsonify({"videos": rows, "count": len(rows)})

    @app.get("/api/videos/<bvid>")
    def get_video_route(bvid: str):
        row = store.get_video(bvid)
        if not row:
            return jsonify({"error": "video not found"}), 404
        return jsonify(_public_video(row))

    # ---- 调度状态 ----

    @app.get("/api/scheduler/tick")
    def scheduler_tick_route():
        """立刻跑一轮调度,返回统计。给运维/测试用。"""
        return jsonify(scheduler.tick())

    return app


app = create_app()
scheduler.start()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    port = int(os.getenv("PORT", "5070"))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"bili-worker listening on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
