"""Locate, read and install the bundled tb SKILL.md.

The skill file is the single source of truth describing how to operate tb.
It can be consumed by any agent: tb chat injects it into the system prompt,
and `tb skill install` copies it where Claude Code auto-discovers skills.
"""

from __future__ import annotations

import shutil
from pathlib import Path

SKILL_NAME = "tb"

# 默认安装目标：Claude Code 的技能目录
CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"


def skill_path() -> Path:
    """Path of the bundled SKILL.md (repo_root/skills/tb/SKILL.md)."""
    return Path(__file__).resolve().parents[1] / "skills" / SKILL_NAME / "SKILL.md"


def read_skill() -> str:
    path = skill_path()
    if not path.exists():
        raise FileNotFoundError(f"skill 文件缺失: {path}")
    return path.read_text(encoding="utf-8")


def skill_body() -> str:
    """SKILL.md content without the YAML frontmatter."""
    text = read_skill().lstrip()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].strip()
    return text


def install_skill(target_dir: Path | None = None) -> Path:
    """Copy SKILL.md into an agent's skills directory; return the target path."""
    dest_dir = (target_dir or CLAUDE_SKILLS_DIR) / SKILL_NAME
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "SKILL.md"
    shutil.copyfile(skill_path(), dest)
    return dest
