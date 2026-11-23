from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tb.paths import DATA_DIR, QUEUES, ensure_layout, factories_dir
from tb.store import enqueue_task


def _safe_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    slug = slug.strip("-")
    if not slug:
        raise ValueError("factory id is required")
    return slug


def list_factories(data_dir: Path = DATA_DIR) -> list[dict[str, Any]]:
    ensure_layout(data_dir)
    items: list[dict[str, Any]] = []
    for path in sorted(factories_dir(data_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data.setdefault("id", path.stem)
        data["_path"] = str(path)
        items.append(data)
    return items


def get_factory(factory_id: str, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    path = factories_dir(data_dir) / f"{_safe_id(factory_id)}.json"
    if not path.exists():
        raise FileNotFoundError(f"factory not found: {factory_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("id", path.stem)
    data["_path"] = str(path)
    return data


def add_factory(
    factory_id: str,
    title: str,
    prompt: str,
    queue: str = "mid",
    name: str = "",
    data_dir: Path = DATA_DIR,
) -> dict[str, Any]:
    if queue not in QUEUES:
        raise ValueError(f"unknown queue: {queue}")
    title = title.strip()
    prompt = prompt.strip()
    if not title:
        raise ValueError("title is required")
    if not prompt:
        raise ValueError("prompt is required")
    ensure_layout(data_dir)
    fid = _safe_id(factory_id)
    path = factories_dir(data_dir) / f"{fid}.json"
    if path.exists():
        raise ValueError(f"factory already exists: {fid}")
    data = {
        "id": fid,
        "name": (name or title).strip(),
        "queue": queue,
        "title": title,
        "prompt": prompt,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    data["_path"] = str(path)
    return data


def run_factory(factory_id: str, data_dir: Path = DATA_DIR) -> dict[str, Any]:
    factory = get_factory(factory_id, data_dir)
    return enqueue_task(
        factory.get("queue") or "mid",
        factory.get("title") or factory["id"],
        factory.get("prompt") or "",
        data_dir,
    )
