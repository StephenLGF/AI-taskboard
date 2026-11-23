from __future__ import annotations

import json
import os
import signal
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from tb.config import load_config, resolve_api_key, worker_config
from tb.paths import (
    DATA_DIR,
    QUEUES,
    ensure_layout,
    worker_log_path,
    worker_pid_path,
    worker_state_path,
)
from tb.store import claim_next, fail_task, now_iso, pending_count


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def worker_status(queue: str, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    ensure_layout(data_dir)
    pid_path = worker_pid_path(queue, data_dir)
    state = read_json(worker_state_path(queue, data_dir)) or {}
    pid = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            pid = None
    running = bool(pid and _pid_alive(pid))
    if pid and not running:
        running = False
    state.update({"queue": queue, "pid": pid if running else pid, "running": running})
    if not running:
        state.setdefault("current_task", None)
    return state


def all_worker_statuses(data_dir: Path = DATA_DIR) -> dict[str, dict[str, Any]]:
    return {queue: worker_status(queue, data_dir) for queue in QUEUES}


def stop_worker(queue: str, data_dir: Path = DATA_DIR, timeout: float = 8.0) -> bool:
    status = worker_status(queue, data_dir)
    pid = status.get("pid")
    if not status.get("running") or not pid:
        _cleanup_pid(queue, data_dir)
        return False
    os.kill(int(pid), signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(int(pid)):
            _cleanup_pid(queue, data_dir)
            return True
        time.sleep(0.1)
    os.kill(int(pid), signal.SIGKILL)
    _cleanup_pid(queue, data_dir)
    return True


def _cleanup_pid(queue: str, data_dir: Path) -> None:
    pid_path = worker_pid_path(queue, data_dir)
    if pid_path.exists():
        pid_path.unlink()
    state_path = worker_state_path(queue, data_dir)
    state = read_json(state_path) or {"queue": queue}
    state["running"] = False
    state["pid"] = None
    state["current_task"] = None
    state["stopped_at"] = now_iso()
    write_json(state_path, state)


def start_worker(queue: str, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    if queue not in QUEUES:
        raise ValueError(f"unknown queue: {queue}")
    status = worker_status(queue, data_dir)
    if status.get("running"):
        return status
    ensure_layout(data_dir)
    log_path = worker_log_path(queue, data_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    package_root = str(Path(__file__).resolve().parents[1])
    env = os.environ.copy()
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    env["TB_DATA_DIR"] = str(data_dir)
    import subprocess
    log_fp = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [python, "-m", "tb", "worker", "run", queue],
        stdout=log_fp,
        stderr=log_fp,
        env=env,
        start_new_session=True,
        cwd=package_root,
    )
    log_fp.close()
    for _ in range(20):
        time.sleep(0.05)
        status = worker_status(queue, data_dir)
        if status.get("running"):
            return status
    return {"queue": queue, "pid": proc.pid, "running": _pid_alive(proc.pid), "starting": True}


def append_log(queue: str, message: str, data_dir: Path = DATA_DIR) -> None:
    path = worker_log_path(queue, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{now_iso()} {message}\n")


class StopRequested(Exception):
    pass


def run_worker_loop(queue: str, data_dir: Path | None = None) -> int:
    data_dir = data_dir or Path(os.environ.get("TB_DATA_DIR") or DATA_DIR)
    ensure_layout(data_dir)
    stop = {"flag": False}

    def _handle(signum: int, _frame: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    pid_path = worker_pid_path(queue, data_dir)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    cfg = load_config()
    wcfg = worker_config(cfg, queue)
    worker_name = f"{queue}-{os.getpid()}"
    idle_timeout = int(cfg.get("idle_timeout_seconds") or 120)
    poll_interval = float(cfg.get("poll_interval_seconds") or 2)
    idle_since = time.time()

    def write_state(**extra: Any) -> None:
        payload = {
            "queue": queue,
            "pid": os.getpid(),
            "running": True,
            "worker": worker_name,
            "model": wcfg.get("model") or "",
            "provider": wcfg.get("provider") or "stub",
            "heartbeat_at": now_iso(),
            "idle_since": None if extra.get("current_task") else idle_since and now_iso(),
        }
        payload.update(extra)
        write_json(worker_state_path(queue, data_dir), payload)

    write_state(current_task=None)
    append_log(queue, f"worker started pid={os.getpid()} provider={wcfg.get('provider')}", data_dir)
    try:
        while not stop["flag"]:
            write_state(current_task=None)
            task = claim_next(queue, worker_name, data_dir)
            if task is None:
                if time.time() - idle_since >= idle_timeout:
                    append_log(queue, f"idle for {idle_timeout}s, exiting", data_dir)
                    break
                time.sleep(poll_interval)
                continue
            idle_since = time.time()
            write_state(current_task=task["id"], current_title=task.get("title"))
            append_log(queue, f"claimed {task['id']} {task.get('title')}", data_dir)
            try:
                result = execute_task(task, wcfg)
                from tb.store import finish_task

                finish_task(task["id"], result, data_dir)
                append_log(queue, f"done {task['id']}", data_dir)
            except Exception as exc:  # noqa: BLE001
                fail_task(task["id"], f"{type(exc).__name__}: {exc}", data_dir)
                append_log(queue, f"failed {task['id']}: {exc}\n{traceback.format_exc()}", data_dir)
            idle_since = time.time()
    finally:
        if pid_path.exists():
            try:
                if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    pid_path.unlink()
            except OSError:
                pass
        write_json(
            worker_state_path(queue, data_dir),
            {
                "queue": queue,
                "pid": None,
                "running": False,
                "worker": worker_name,
                "current_task": None,
                "stopped_at": now_iso(),
            },
        )
        append_log(queue, "worker stopped", data_dir)
    return 0


def execute_task(task: dict[str, Any], wcfg: dict[str, Any]) -> str:
    provider = str(wcfg.get("provider") or "stub").strip().lower()
    model = str(wcfg.get("model") or "").strip()
    prompt = (
        f"You are a {task['queue']}-level worker.\n"
        f"Task: {task['title']}\n\n{task['prompt']}\n"
    )
    if provider in {"stub", "dry-run", "dryrun", ""}:
        return (
            f"[stub:{task['queue']}] model={model or 'unset'}\n"
            f"title: {task['title']}\n"
            "LLM is not connected yet. Configure workers.<queue>.provider/model/base_url "
            "with `tb config set`.\n"
        )
    if provider in {"openai", "openai_compatible", "openai-compatible"}:
        return _call_openai_compatible(prompt, wcfg, model)
    raise ValueError(f"unsupported provider: {provider}")


def _call_openai_compatible(prompt: str, wcfg: dict[str, Any], model: str) -> str:
    if not model:
        raise ValueError("workers.*.model is empty")
    base_url = str(wcfg.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("workers.*.base_url is empty")
    api_key = resolve_api_key(wcfg)
    if not api_key:
        raise ValueError(
            f"API key not found: set {wcfg.get('api_key_env')} or run tb setup"
        )
    url = base_url + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        raise RuntimeError("LLM returned empty content")
    return str(content)


def maybe_autostart(queue: str, data_dir: Path = DATA_DIR) -> dict[str, Any] | None:
    cfg = load_config()
    if not cfg.get("auto_start_workers", True):
        return None
    if pending_count(queue, data_dir) <= 0:
        return None
    status = worker_status(queue, data_dir)
    if status.get("running"):
        return None
    return start_worker(queue, data_dir)
