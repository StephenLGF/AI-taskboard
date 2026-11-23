"""Interactive terminal prompts with arrow-key selection. Stdlib only."""

from __future__ import annotations

import sys
import termios
import tty
from collections.abc import Sequence

Option = tuple[str, str]


def _normalize(options: Sequence[str | tuple[str, str]]) -> list[Option]:
    out: list[Option] = []
    for item in options:
        if isinstance(item, tuple):
            out.append((str(item[0]), str(item[1])))
        else:
            out.append((str(item), str(item)))
    if not out:
        raise ValueError("options must not be empty")
    return out


def _read_key() -> str:
    fd = sys.stdin.fileno()
    first = sys.stdin.read(1)
    if first == "\x03":
        raise KeyboardInterrupt
    if first in {"\r", "\n"}:
        return "enter"
    if first in {"\x7f", "\b"}:
        return "backspace"
    if first == "\x1b":
        old = termios.tcgetattr(fd)
        try:
            new = termios.tcgetattr(fd)
            new[6][termios.VMIN] = 0
            new[6][termios.VTIME] = 1
            termios.tcsetattr(fd, termios.TCSANOW, new)
            rest = sys.stdin.read(2)
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)
        if rest == "[A" or rest == "OA":
            return "up"
        if rest == "[B" or rest == "OB":
            return "down"
        if rest == "[C" or rest == "OC":
            return "right"
        if rest == "[D" or rest == "OD":
            return "left"
        return "esc"
    if first in {"k", "K", "p"}:
        return "up"
    if first in {"j", "J", "n"}:
        return "down"
    if first == "q":
        return "esc"
    return first


def _print_menu(title: str, labels: list[str], index: int, hint: str) -> int:
    lines = [title, ""]
    for i, label in enumerate(labels):
        mark = ">" if i == index else " "
        lines.append(f"  {mark} {label}")
    lines.append("")
    lines.append(hint)
    text = "\n".join(lines)
    sys.stdout.write(text)
    sys.stdout.flush()
    return text.count("\n") + 1


def _rewind(line_count: int) -> None:
    if line_count <= 0:
        return
    sys.stdout.write(f"\033[{line_count}A\r\033[J")
    sys.stdout.flush()


def choose(
    title: str,
    options: Sequence[str | tuple[str, str]],
    default: int = 0,
    hint: str = "↑/↓ 选择，Enter 确认",
) -> str:
    items = _normalize(options)
    index = max(0, min(default, len(items) - 1))
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _choose_numbered(title, items, index)

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    labels = [label for _, label in items]
    drawn = 0
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    try:
        # cbreak（而非 raw）保留输出处理，\n 才会自动带回车，菜单不会阶梯错位
        tty.setcbreak(fd)
        while True:
            if drawn:
                _rewind(drawn)
            drawn = _print_menu(title, labels, index, hint)
            key = _read_key()
            if key == "up":
                index = (index - 1) % len(items)
            elif key == "down":
                index = (index + 1) % len(items)
            elif key == "enter":
                break
            elif key == "esc":
                raise KeyboardInterrupt
            elif key.isdigit():
                num = int(key)
                if 1 <= num <= len(items):
                    index = num - 1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        _rewind(drawn)
        sys.stdout.write("\033[?25h")
        print(title)
        print(f"  > {labels[index]}")
        print()
        sys.stdout.flush()
    return items[index][0]


def _choose_numbered(title: str, items: list[Option], default: int) -> str:
    print(title)
    for i, (_, label) in enumerate(items, 1):
        mark = " (默认)" if i - 1 == default else ""
        print(f"  {i}) {label}{mark}")
    print()
    while True:
        raw = input(f"请输入序号 [{default + 1}]: ").strip()
        if not raw:
            return items[default][0]
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1][0]
        print(f"请输入 1 到 {len(items)} 之间的数字")


def ask(title: str, default: str = "", allow_empty: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    if not sys.stdin.isatty():
        raise RuntimeError("需要在交互式终端里运行")
    while True:
        raw = input(f"{title}{suffix}: ").strip()
        if raw:
            return raw
        if default or allow_empty:
            return default
        print("这项不能为空")


def ask_secret(title: str, has_existing: bool = False) -> str | None:
    """Return a new secret, or None to keep the existing one."""
    import getpass

    hint = "（输入不会回显" + ("，直接回车保留已有密钥）" if has_existing else "）")
    if not sys.stdin.isatty():
        raise RuntimeError("需要在交互式终端里运行")
    while True:
        value = getpass.getpass(f"{title}{hint}: ")
        if value:
            return value
        if has_existing:
            return None
        print("还没有密钥，请输入 API key")


def confirm(title: str, default: bool = True) -> bool:
    yes, no = ("是", "否")
    options = [("yes", yes), ("no", no)]
    chosen = choose(title, options, default=0 if default else 1, hint="↑/↓ 选择，Enter 确认")
    return chosen == "yes"
