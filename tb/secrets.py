"""Store API keys outside config.yaml, with file mode 0600."""

from __future__ import annotations

import os
from pathlib import Path

from tb.paths import DATA_DIR


def secrets_path(data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / "secrets.env"



def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        inner = value[1:-1]
        out: list[str] = []
        i = 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    return value


def load_secrets(data_dir: Path | None = None) -> dict[str, str]:
    path = secrets_path(data_dir)
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for original in path.read_text(encoding="utf-8").splitlines():
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            out[key] = _unquote(value)
    return out


def set_secret(name: str, value: str, data_dir: Path | None = None) -> Path:
    data = load_secrets(data_dir)
    data[name] = value
    return save_secrets(data, data_dir)


def save_secrets(data: dict[str, str], data_dir: Path | None = None) -> Path:
    path = secrets_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# tb secrets — do not commit, never stored in config.yaml", ""]
    for key, value in data.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    text = "\n".join(lines) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)
    return path


def secret_status(name: str, data_dir: Path | None = None) -> str:
    if os.environ.get(name):
        return "环境变量已设置"
    if load_secrets(data_dir).get(name):
        return "已保存在 secrets.env"
    return "未设置"


def has_secret(name: str, data_dir: Path | None = None) -> bool:
    return secret_status(name, data_dir) != "未设置"
