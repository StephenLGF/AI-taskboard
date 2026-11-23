from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "tb"
DATA_DIR = Path(os.environ.get("TB_DATA_DIR") or (Path.home() / ".tb"))
CONFIG_PATH = DATA_DIR / "config.yaml"
QUEUES = ("high", "mid", "low")
STATUSES = ("pending", "running", "done", "failed", "canceled")


def ensure_layout(data_dir: Path = DATA_DIR) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    for queue in QUEUES:
        for status in ("pending", "running", "done", "failed", "canceled"):
            (data_dir / "queues" / queue / status).mkdir(parents=True, exist_ok=True)
    (data_dir / "workers").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (data_dir / "factories").mkdir(parents=True, exist_ok=True)
    return data_dir


def task_path(task_id: str, queue: str, status: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "queues" / queue / status / f"{task_id}.json"


def worker_state_path(queue: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "workers" / f"{queue}.state.json"


def worker_pid_path(queue: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "workers" / f"{queue}.pid"


def worker_log_path(queue: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "logs" / f"worker-{queue}.log"


def factories_dir(data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "factories"
