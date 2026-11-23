from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tb.paths import DATA_DIR, QUEUES, STATUSES, ensure_layout, task_path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_task_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"tb-{stamp}-{uuid.uuid4().hex[:6]}"


def _lock_path(data_dir: Path) -> Path:
    return data_dir / ".lock"


class FileLock:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.path = _lock_path(data_dir)
        self.fd: int | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        if fcntl is not None:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def read_task_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_task_file(path: Path, task: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def find_task(task_id: str, data_dir: Path = DATA_DIR) -> dict[str, Any] | None:
    ensure_layout(data_dir)
    for queue in QUEUES:
        for status in STATUSES:
            path = task_path(task_id, queue, status, data_dir)
            if path.exists():
                task = read_task_file(path)
                task["_path"] = str(path)
                return task
    return None


def iter_tasks(data_dir: Path = DATA_DIR, queue: str | None = None, status: str | None = None) -> Iterable[dict[str, Any]]:
    ensure_layout(data_dir)
    queues = (queue,) if queue else QUEUES
    statuses = (status,) if status else STATUSES
    for q in queues:
        for st in statuses:
            folder = data_dir / "queues" / q / st
            if not folder.exists():
                continue
            for path in sorted(folder.glob("*.json")):
                try:
                    task = read_task_file(path)
                except (OSError, json.JSONDecodeError):
                    continue
                task["_path"] = str(path)
                yield task


def enqueue_task(queue: str, title: str, prompt: str, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    if queue not in QUEUES:
        raise ValueError(f"unknown queue: {queue}")
    title = title.strip()
    prompt = prompt.strip()
    if not title:
        raise ValueError("title is required")
    if not prompt:
        raise ValueError("prompt is required")
    ensure_layout(data_dir)
    task = {
        "id": new_task_id(),
        "queue": queue,
        "title": title,
        "prompt": prompt,
        "status": "pending",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "worker": None,
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }
    with FileLock(data_dir):
        write_task_file(task_path(task["id"], queue, "pending", data_dir), task)
    return task


def _move_task(task: dict[str, Any], new_status: str, data_dir: Path) -> dict[str, Any]:
    old_path = Path(task["_path"])
    task["status"] = new_status
    task["updated_at"] = now_iso()
    new_path = task_path(task["id"], task["queue"], new_status, data_dir)
    write_task_file(new_path, {k: v for k, v in task.items() if not k.startswith("_")})
    if old_path.exists() and old_path.resolve() != new_path.resolve():
        old_path.unlink()
    task["_path"] = str(new_path)
    return task


def claim_next(queue: str, worker_name: str, data_dir: Path = DATA_DIR) -> dict[str, Any] | None:
    ensure_layout(data_dir)
    with FileLock(data_dir):
        pending = list(iter_tasks(data_dir, queue=queue, status="pending"))
        if not pending:
            return None
        task = pending[0]
        task["worker"] = worker_name
        task["started_at"] = now_iso()
        return _move_task(task, "running", data_dir)


def finish_task(task_id: str, result: str, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    with FileLock(data_dir):
        task = find_task(task_id, data_dir)
        if not task:
            raise FileNotFoundError(task_id)
        task["result"] = result
        task["error"] = None
        task["finished_at"] = now_iso()
        return _move_task(task, "done", data_dir)


def fail_task(task_id: str, error: str, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    with FileLock(data_dir):
        task = find_task(task_id, data_dir)
        if not task:
            raise FileNotFoundError(task_id)
        task["error"] = error
        task["finished_at"] = now_iso()
        return _move_task(task, "failed", data_dir)


def cancel_task(task_id: str, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    with FileLock(data_dir):
        task = find_task(task_id, data_dir)
        if not task:
            raise FileNotFoundError(task_id)
        if task["status"] in {"done", "failed", "canceled"}:
            raise ValueError(f"task {task_id} is already {task['status']}")
        task["finished_at"] = now_iso()
        return _move_task(task, "canceled", data_dir)


def counts(data_dir: Path = DATA_DIR) -> dict[str, dict[str, int]]:
    ensure_layout(data_dir)
    result: dict[str, dict[str, int]] = {}
    for queue in QUEUES:
        result[queue] = {status: 0 for status in STATUSES}
        for status in STATUSES:
            folder = data_dir / "queues" / queue / status
            result[queue][status] = len(list(folder.glob("*.json")))
    return result


def pending_count(queue: str, data_dir: Path = DATA_DIR) -> int:
    return counts(data_dir)[queue]["pending"]
