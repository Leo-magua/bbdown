"""SSH/scp backed remote transcriber client for toolkit/.

Copy this file into /Users/zhang.longqiang/Apps/bili-worker/toolkit/.
"""

from __future__ import annotations

import json
import os
import posixpath
import shlex
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any


REMOTE_NODE_DIR = "~/Apps/bili-transcriber-node"
REMOTE_TMP_DIR = "/tmp/bili-transcribe"
# Whisper medium 在 CPU 上约 2x 实时(74min 视频 ≈ 35-40min)。
# 直播回放经常 1-2 小时,留 2 小时上限。可用 BILI_REMOTE_TIMEOUT 覆盖。
REMOTE_TIMEOUT_SECONDS = int(os.environ.get("BILI_REMOTE_TIMEOUT", "7200"))

_remote_lock = threading.Lock()


def _run(args: list[str], timeout: int = REMOTE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _remote_host() -> str:
    return os.environ.get("BILI_REMOTE_TRANSCRIBE") or "mac-rui"


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "remote transcription failed")

    required = ("transcription", "timestamped_text", "segments", "language", "audio_duration")
    missing = [key for key in required if key not in payload]
    if missing:
        raise RuntimeError(f"remote payload missing keys: {', '.join(missing)}")

    return {
        "transcription": payload["transcription"],
        "timestamped_text": payload["timestamped_text"],
        "segments": payload["segments"],
        "language": payload["language"],
        "audio_duration": payload["audio_duration"],
    }


def transcribe_remote(media_path: str, language: str = "zh") -> dict[str, Any]:
    """Transcribe a local media file on mac-rui through SSH and return store-ready payload.

    远程节点一次只允许跑一个任务(`_remote_lock`)。如果锁已被别的线程占住,
    本调用立刻抛 RuntimeError("remote busy"),让上层降级跑本地 Whisper —
    避免本地 + 远程两个 worker 都堵在锁上等。
    """

    local_path = Path(media_path)
    if not local_path.exists():
        raise RuntimeError(f"media file does not exist: {media_path}")

    host = _remote_host()
    remote_name = f"{uuid.uuid4().hex}_{local_path.name}"
    remote_path = posixpath.join(REMOTE_TMP_DIR, remote_name)
    quoted_remote_path = shlex.quote(remote_path)
    quoted_language = shlex.quote(language)

    if not _remote_lock.acquire(blocking=False):
        raise RuntimeError("remote busy: another task is using the remote node")
    try:
        try:
            mkdir = _run(["ssh", host, f"mkdir -p {shlex.quote(REMOTE_TMP_DIR)}"], timeout=30)
            if mkdir.returncode != 0:
                raise RuntimeError(f"remote mkdir failed: {mkdir.stderr.strip() or mkdir.stdout.strip()}")

            scp = _run(["scp", str(local_path), f"{host}:{remote_path}"], timeout=120)
            if scp.returncode != 0:
                raise RuntimeError(f"scp failed: {scp.stderr.strip() or scp.stdout.strip()}")

            command = (
                f"cd {REMOTE_NODE_DIR} && "
                f".venv/bin/python transcribe_one.py {quoted_remote_path} --language {quoted_language}"
            )
            run = _run(["ssh", host, command], timeout=REMOTE_TIMEOUT_SECONDS)
            if run.returncode != 0:
                raise RuntimeError(f"remote command failed: {run.stderr.strip() or run.stdout.strip()}")

            try:
                payload = json.loads(run.stdout)
            except json.JSONDecodeError as exc:
                snippet = run.stdout[:500].replace("\n", "\\n")
                raise RuntimeError(f"remote returned invalid JSON: {exc}; stdout={snippet}") from exc

            return _validate_payload(payload)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"remote transcription timed out after {exc.timeout}s") from exc
        finally:
            cleanup = f"rm -f {quoted_remote_path}"
            _run(["ssh", host, cleanup], timeout=30)
    finally:
        _remote_lock.release()
