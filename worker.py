"""后台任务队列：下载 / 转写。

- 线程池执行,主线程同步返回 task_id
- 任务状态双写:内存 dict(给老 HTTP 轮询接口) + sqlite videos 表(持久化)
- 同一 (bvid, kind) 已在运行 / 刚完成 → 幂等返回旧 task_id
- 下载完成自动触发转写(scheduler 也会扫盘补漏)
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from toolkit import store
from toolkit.bili_downloader import download_video, find_media_file
from toolkit.bili_transcriber import WhisperTranscriber


DOWNLOAD_ROOT = os.path.join(os.path.dirname(__file__), "data", "downloads")
TASK_TTL_SECONDS = 24 * 3600

TaskKind = Literal["download", "transcribe"]


@dataclass
class Task:
    task_id: str
    bvid: str
    kind: TaskKind
    status: str = "pending"  # pending|running|completed|error
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None


_tasks: dict[str, Task] = {}
_lock = threading.Lock()
_download_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dl")
# 转写池容量:本地 1 + 远程 1。BILI_REMOTE_TRANSCRIBE 没设时多余 worker 也只是
# 排队等本地 Whisper 锁,无害。
_transcribe_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tx")
_transcriber_lock = threading.Lock()
_transcriber: Optional[WhisperTranscriber] = None


def _get_transcriber() -> WhisperTranscriber:
    global _transcriber
    with _transcriber_lock:
        if _transcriber is None:
            _transcriber = WhisperTranscriber(model_size="medium")
            _transcriber.load_model()
    return _transcriber


def _gc_tasks() -> None:
    now = time.time()
    with _lock:
        stale = [
            tid
            for tid, t in _tasks.items()
            if t.finished_at and (now - t.finished_at) > TASK_TTL_SECONDS
        ]
        for tid in stale:
            _tasks.pop(tid, None)


def _find_existing(bvid: str, kind: TaskKind) -> Optional[Task]:
    for t in _tasks.values():
        if t.bvid == bvid and t.kind == kind and t.status in {"pending", "running"}:
            return t
        # 也复用最近完成的（5 分钟内）
        if (
            t.bvid == bvid
            and t.kind == kind
            and t.status == "completed"
            and t.finished_at
            and (time.time() - t.finished_at) < 300
        ):
            return t
    return None


def _download_job(task: Task, want_audio_only: bool) -> None:
    out_dir = os.path.join(DOWNLOAD_ROOT, task.bvid)
    os.makedirs(out_dir, exist_ok=True)
    task.status = "running"
    task.message = "downloading"
    try:
        store.ensure_video_row(task.bvid)
        store.update_video_download(task.bvid, status="running")
    except Exception:
        pass

    result = download_video(task.bvid, out_dir)

    if not result.get("success") and want_audio_only:
        # you-get 默认会下视频(含音轨),audio 模式失败也可用视频凑合。
        task.message = "audio-only unavailable, fallback to video"
        result = download_video(task.bvid, out_dir)

    if not result.get("success"):
        task.status = "error"
        task.message = result.get("message", "download failed")
        task.finished_at = time.time()
        try:
            store.update_video_download(
                task.bvid, status="error", error=task.message,
            )
        except Exception:
            pass
        return

    media = find_media_file(out_dir)
    task.result = {
        "output_dir": out_dir,
        "files": result.get("files", []),
        "media_path": media,
    }
    task.status = "completed"
    task.message = result.get("message", "ok")
    task.finished_at = time.time()
    try:
        store.update_video_download(task.bvid, status="done", media_path=media)
    except Exception:
        pass

    # 下完立刻入转写队列
    if media:
        try:
            submit_transcribe(task.bvid)
        except Exception:
            pass


def _transcribe_job(task: Task, language: str) -> None:
    out_dir = os.path.join(DOWNLOAD_ROOT, task.bvid)
    media = find_media_file(out_dir)
    if not media:
        task.status = "error"
        task.message = f"no media file in {out_dir}; download first"
        task.finished_at = time.time()
        try:
            store.update_video_transcription(
                task.bvid, status="error", error=task.message,
            )
        except Exception:
            pass
        return

    task.status = "running"
    task.message = "transcribing"
    try:
        store.ensure_video_row(task.bvid)
        store.update_video_transcription(task.bvid, status="running")
    except Exception:
        pass

    payload: Optional[dict[str, Any]] = None
    try:
        remote_node = os.environ.get("BILI_REMOTE_TRANSCRIBE")
        if remote_node:
            try:
                task.message = f"remote transcribing on {remote_node}"
                from toolkit.remote_transcriber import transcribe_remote

                payload = transcribe_remote(media, language=language)
                task.message = "remote transcribed"
                print(f"[worker] remote transcription succeeded for {task.bvid} on {remote_node}")
            except Exception as remote_exc:  # noqa: BLE001
                print(
                    f"[worker] remote transcription failed for {task.bvid} "
                    f"on {remote_node}; falling back to local Whisper: {remote_exc}"
                )
                task.message = "remote failed; transcribing locally"

        if payload is None:
            transcriber = _get_transcriber()
            result = transcriber.transcribe(
                media,
                language=language,
                use_simplified_chinese=(language == "zh"),
            )
            # 短段合并,提升可读性
            result = result.merged(min_duration=3.0)
            payload = result.to_payload()
    except Exception as exc:  # noqa: BLE001
        task.status = "error"
        task.message = f"whisper error: {exc}\n{traceback.format_exc()}"
        task.finished_at = time.time()
        try:
            store.update_video_transcription(task.bvid, status="error", error=str(exc))
        except Exception:
            pass
        return

    task.result = {
        "text": payload["transcription"],
        "timestamped_text": payload["timestamped_text"],
        "segments": payload["segments"],
        "duration": payload["audio_duration"],
        "language": payload["language"],
        "media_path": media,
    }
    task.status = "completed"
    task.message = "ok"
    task.finished_at = time.time()
    try:
        store.update_video_transcription(
            task.bvid,
            status="done",
            transcription=payload["transcription"],
            timestamped_text=payload["timestamped_text"],
            segments=payload["segments"],
            language=payload["language"],
            audio_duration=payload["audio_duration"],
        )
    except Exception:
        pass


def submit_download(bvid: str, media_type: str = "audio") -> Task:
    """认领下载任务并提交线程池。

    幂等:若 sqlite 里 download_status 已是 'queued'/'running'/'done',
    抢锁失败则返回内存里现有的 Task(可能是同进程别的 submit 残留),
    或返回一个空壳 Task 给 HTTP 调用方查状态。
    """
    _gc_tasks()
    store.ensure_video_row(bvid)
    with _lock:
        existing = _find_existing(bvid, "download")
        if existing:
            return existing
        tid = f"{bvid}_{media_type}"
    if not store.claim_video_for_download(bvid):
        # 别人已经认领过了(可能是别的进程,或刚刚我们自己 done 了)
        task = Task(task_id=tid, bvid=bvid, kind="download")
        task.status = "skipped"
        task.message = "already claimed elsewhere"
        task.finished_at = time.time()
        return task
    with _lock:
        task = Task(task_id=tid, bvid=bvid, kind="download")
        _tasks[tid] = task
    _download_pool.submit(_download_job, task, media_type == "audio")
    return task


def submit_transcribe(bvid: str, language: str = "zh") -> Task:
    """认领转写任务并提交线程池。"""
    _gc_tasks()
    store.ensure_video_row(bvid)
    with _lock:
        existing = _find_existing(bvid, "transcribe")
        if existing:
            return existing
        tid = f"{bvid}_transcribe"
    if not store.claim_video_for_transcribe(bvid):
        task = Task(task_id=tid, bvid=bvid, kind="transcribe")
        task.status = "skipped"
        task.message = "already claimed elsewhere"
        task.finished_at = time.time()
        return task
    with _lock:
        task = Task(task_id=tid, bvid=bvid, kind="transcribe")
        _tasks[tid] = task
    _transcribe_pool.submit(_transcribe_job, task, language)
    return task


def get_task(task_id: str) -> Optional[Task]:
    return _tasks.get(task_id)


def to_status_dict(task: Task) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": task.task_id,
        "bvid": task.bvid,
        "kind": task.kind,
        "status": task.status,
        "message": task.message,
        "created_at": task.created_at,
        "finished_at": task.finished_at,
    }
    if task.status == "completed":
        payload.update(task.result)
    return payload
