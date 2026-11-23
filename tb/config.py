from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from tb.paths import CONFIG_PATH, DATA_DIR, QUEUES, ensure_layout

DEFAULT_CONFIG: dict[str, Any] = {
    "idle_timeout_seconds": 120,
    "poll_interval_seconds": 2,
    "auto_start_workers": True,
    "workers": {
        "high": {
            "model": "",
            "provider": "stub",
            "base_url": "",
            "api_key_env": "TB_HIGH_API_KEY",
            "concurrency": 1,
        },
        "mid": {
            "model": "",
            "provider": "stub",
            "base_url": "",
            "api_key_env": "TB_MID_API_KEY",
            "concurrency": 1,
        },
        "low": {
            "model": "",
            "provider": "stub",
            "base_url": "",
            "api_key_env": "TB_LOW_API_KEY",
            "concurrency": 1,
        },
    },
}


def _deep_update(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_update(out[key], value)
        else:
            out[key] = value
    return out


def _parse_scalar(text: str) -> Any:
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", ""}:
        return ""
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def load_simple_yaml(path: Path) -> dict[str, Any]:
    """Minimal YAML subset (mappings only) so the CLI has no third-party deps."""
    raw = path.read_text(encoding="utf-8")
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for lineno, original in enumerate(raw.splitlines(), 1):
        if not original.strip() or original.lstrip().startswith("#"):
            continue
        indent = len(original) - len(original.lstrip(" "))
        line = original.strip()
        if ":" not in line:
            raise ValueError(f"{path}:{lineno}: invalid line: {original}")
        key, _, rest = line.partition(":")
        key = key.strip()
        value_text = rest.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value_text == "":
            new_map: dict[str, Any] = {}
            parent[key] = new_map
            stack.append((indent, new_map))
        else:
            if (value_text.startswith('"') and value_text.endswith('"')) or (
                value_text.startswith("'") and value_text.endswith("'")
            ):
                parent[key] = value_text[1:-1]
            else:
                parent[key] = _parse_scalar(value_text)
    return root


def dump_simple_yaml(data: dict[str, Any], indent: int = 0) -> str:
    lines: list[str] = []
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(dump_simple_yaml(value, indent + 1))
        elif isinstance(value, bool):
            lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{prefix}{key}:")
        else:
            text = str(value)
            if any(ch in text for ch in [":", "#", " "]):
                text = '"' + text.replace('"', '\\"') + '"'
            lines.append(f"{prefix}{key}: {text}")
    return "\n".join(lines) + ("\n" if indent == 0 else "")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    ensure_layout(DATA_DIR)
    if not path.exists():
        path.write_text(dump_simple_yaml(DEFAULT_CONFIG), encoding="utf-8")
        return deepcopy(DEFAULT_CONFIG)
    loaded = load_simple_yaml(path)
    merged = _deep_update(DEFAULT_CONFIG, loaded)
    if merged != loaded:
        save_config(merged, path)
    return merged


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    ensure_layout(DATA_DIR)
    path.write_text(dump_simple_yaml(config), encoding="utf-8")


def set_config_value(key: str, value: str, path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = load_config(path)
    parts = key.split(".")
    if not parts or any(not p for p in parts):
        raise ValueError(f"invalid config key: {key}")
    cursor: Any = config
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = _parse_scalar(value)
    save_config(config, path)
    return config


def worker_config(config: dict[str, Any], queue: str) -> dict[str, Any]:
    if queue not in QUEUES:
        raise ValueError(f"unknown queue: {queue}")
    return config["workers"][queue]


def resolve_api_key(worker_cfg: dict[str, Any], data_dir: Path | None = None) -> str:
    env_name = str(worker_cfg.get("api_key_env") or "")
    if not env_name:
        return ""
    value = os.environ.get(env_name, "")
    if value:
        return value
    from tb.secrets import load_secrets

    return load_secrets(data_dir or DATA_DIR).get(env_name, "")
