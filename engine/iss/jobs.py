"""Background jobs (imports, analysis) with progress the UI can poll."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
# One heavy job at a time per kind keeps the PC responsive; music can run beside footage.
_pools = {"footage": ThreadPoolExecutor(max_workers=1), "music": ThreadPoolExecutor(max_workers=1)}


def submit(kind: str, title: str, fn: Callable[[Callable[[int, int, str], None]], dict], pool: str = "footage") -> dict:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "title": title, "status": "queued", "done": 0, "total": 0,
           "message": "Waiting to start", "result": None, "error": None, "started": time.time(), "finished": None}
    with _lock:
        _jobs[jid] = job

    def progress(done: int, total: int, msg: str) -> None:
        with _lock:
            job.update(done=done, total=total, message=msg, status="running")

    def run():
        with _lock:
            job["status"] = "running"
        try:
            res = fn(progress)
            with _lock:
                job.update(status="done", result=res, finished=time.time())
        except Exception as e:
            traceback.print_exc()
            with _lock:
                job.update(status="error", error=str(e), message=str(e), finished=time.time())

    _pools[pool].submit(run)
    return dict(job)


def get(jid: str) -> dict | None:
    with _lock:
        j = _jobs.get(jid)
        return dict(j) if j else None


def recent(limit: int = 20) -> list[dict]:
    with _lock:
        js = sorted(_jobs.values(), key=lambda j: -j["started"])[:limit]
        return [dict(j) for j in js]
