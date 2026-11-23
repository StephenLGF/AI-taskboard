from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from tb import __version__
from tb.config import (
    configured_queues,
    load_config,
    queue_configured,
    save_config,
    set_config_value,
)
from tb.setup import run_setup
from tb.secrets import secret_status
from tb.paths import DATA_DIR, QUEUES, ensure_layout, worker_log_path
from tb.factory import add_factory, list_factories, run_factory
from tb.store import cancel_task, counts, enqueue_task, find_task, iter_tasks
from tb.worker import (
    all_worker_statuses,
    maybe_autostart,
    run_worker_loop,
    start_worker,
    stop_worker,
    worker_status,
)

USAGE = """tb — high/mid/low task queues and workers

Usage:
  tb enqueue high|mid|low --title TITLE --prompt PROMPT
  tb status
  tb watch                          (已配置队列的分栏实时视图)
  tb list [worker|queue|factory]     (live, Ctrl-C to exit; --once for one shot)
  tb factory add --id ID --title TITLE --prompt PROMPT [--queue high|mid|low]
  tb factory run ID
  tb logs [task-id]
  tb cancel TASK_ID
  tb setup                            (问答式配置 worker 的 URL / API key / 模型)
  tb worker start|stop|restart high|mid|low
  tb worker run high|mid|low          (internal)
  tb config show
  tb config set KEY VALUE
  tb help

注: 只有配置了 API key 的队列才会显示、才能入队。用 tb setup 配置。
"""


def _data_dir() -> Path:
    return Path(os.environ.get("TB_DATA_DIR") or DATA_DIR)


def _char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def _fit(text: str, width: int) -> str:
    """Truncate (CJK-aware) and pad text to exactly `width` display columns."""
    out: list[str] = []
    used = 0
    for ch in text:
        cw = _char_width(ch)
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(out) + " " * (width - used)


def _render_columns(blocks: list[list[str]], gap: str = " │ ") -> list[str]:
    """Render per-queue blocks side by side; stack vertically when too narrow."""
    total = shutil.get_terminal_size((100, 24)).columns
    n = len(blocks)
    col_w = (total - len(gap) * (n - 1)) // n
    if col_w < 20:
        lines: list[str] = []
        for i, block in enumerate(blocks):
            if i:
                lines.append("─" * min(total, 60))
            lines.extend(block)
        return lines
    height = max(len(b) for b in blocks)
    lines = []
    for row in range(height):
        parts = [
            _fit(block[row], col_w) if row < len(block) else " " * col_w
            for block in blocks
        ]
        lines.append(gap.join(parts).rstrip())
    return lines


def _require_configured(queue: str, data_dir: Path) -> str | None:
    """Return an error message if the queue has no API key, else None."""
    if queue_configured(queue, data_dir):
        return None
    available = configured_queues(data_dir)
    hint = f"可用队列: {', '.join(available)}" if available else "尚无已配置队列"
    return f"队列 {queue} 未配置 API key（{hint}）。运行 tb setup 配置。"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE, end="")
        return 0
    if argv[0] in {"-V", "--version", "version"}:
        print(f"tb {__version__}")
        return 0
    ensure_layout(_data_dir())
    cmd, rest = argv[0], argv[1:]
    handlers = {
        "enqueue": cmd_enqueue,
        "status": cmd_status,
        "watch": cmd_watch,
        "list": cmd_list,
        "factory": cmd_factory,
        "logs": cmd_logs,
        "cancel": cmd_cancel,
        "worker": cmd_worker,
        "config": cmd_config,
        "setup": cmd_setup,
    }
    handler = handlers.get(cmd)
    if handler is None:
        print(f"unknown command: {cmd}\n", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2
    try:
        return handler(rest)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"tb: {exc}", file=sys.stderr)
        return 1


def _parse_enqueue(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tb enqueue", add_help=True)
    parser.add_argument("queue", choices=QUEUES)
    parser.add_argument("--title", required=True)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--file", dest="prompt_file", help="read prompt from a file")
    return parser.parse_args(argv)


def cmd_enqueue(argv: list[str]) -> int:
    args = _parse_enqueue(argv)
    err = _require_configured(args.queue, _data_dir())
    if err:
        print(f"tb: {err}", file=sys.stderr)
        return 1
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    elif not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read()
    task = enqueue_task(args.queue, args.title, prompt, _data_dir())
    started = maybe_autostart(args.queue, _data_dir())
    print(f"enqueued {task['id']} on {task['queue']}: {task['title']}")
    if started and started.get("running"):
        print(f"started {args.queue} worker pid={started.get('pid')}")
    elif started and started.get("starting"):
        print(f"starting {args.queue} worker pid={started.get('pid')}")
    return 0


def _fmt_worker(queue: str, state: dict[str, Any]) -> str:
    if state.get("running"):
        current = state.get("current_task") or "idle"
        model = state.get("model") or "unset"
        return f"{queue}: running pid={state.get('pid')} model={model} task={current}"
    return f"{queue}: stopped"


def cmd_status(_argv: list[str]) -> int:
    data_dir = _data_dir()
    queues = configured_queues(data_dir)
    if not queues:
        print("没有已配置 API key 的队列。运行 tb setup 配置后使用。")
        return 0
    c = counts(data_dir)
    workers = all_worker_statuses(data_dir)
    print("queues")
    for queue in queues:
        q = c[queue]
        print(
            f"  {queue}: pending={q['pending']} running={q['running']} "
            f"done={q['done']} failed={q['failed']} canceled={q['canceled']}"
        )
    print("workers")
    for queue in queues:
        print("  " + _fmt_worker(queue, workers[queue]))
    running_tasks = [
        t for t in iter_tasks(data_dir, status="running") if t["queue"] in queues
    ]
    if running_tasks:
        print("in progress")
        for task in running_tasks:
            print(f"  {task['id']} [{task['queue']}] {task['title']}")
    return 0


def _watch_column(queue: str, data_dir: Path, max_pending: int = 8) -> list[str]:
    """Build one column of the watch view for a single queue."""
    state = worker_status(queue, data_dir)
    c = counts(data_dir)[queue]
    lines: list[str] = []
    if state.get("running"):
        lines.append(f"{queue.upper()}  ● running pid={state.get('pid')}")
    else:
        lines.append(f"{queue.upper()}  ○ stopped")
    provider = str(state.get("provider") or "stub")
    model = str(state.get("model") or "unset")
    lines.append(f"{provider} / {model}")
    lines.append(f"task: {state.get('current_task') or '-'}")
    lines.append(f"pend {c['pending']}  run {c['running']}  done {c['done']}")
    lines.append(f"fail {c['failed']}  cancel {c['canceled']}")
    lines.append("─" * 12)
    pending = list(iter_tasks(data_dir, queue=queue, status="pending"))
    if not pending:
        lines.append("(no pending)")
    else:
        for task in pending[:max_pending]:
            lines.append(task["title"])
            lines.append(f"  {task['id']}")
        if len(pending) > max_pending:
            lines.append(f"... +{len(pending) - max_pending} more")
    return lines


def cmd_watch(_argv: list[str]) -> int:
    data_dir = _data_dir()
    queues = configured_queues(data_dir)
    if not queues:
        print("没有已配置 API key 的队列。运行 tb setup 配置后使用。")
        return 0
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")
            print(f"tb watch  {time.strftime('%Y-%m-%d %H:%M:%S')}   Ctrl-C to exit")
            print()
            blocks = [_watch_column(q, data_dir) for q in queues]
            for line in _render_columns(blocks):
                print(line)
            sys.stdout.flush()
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        return 0


def cmd_logs(argv: list[str]) -> int:
    data_dir = _data_dir()
    if argv:
        task = find_task(argv[0], data_dir)
        if not task:
            raise FileNotFoundError(f"task not found: {argv[0]}")
        print(json.dumps({k: v for k, v in task.items() if not k.startswith('_')}, ensure_ascii=False, indent=2))
        return 0
    for queue in QUEUES:
        path = worker_log_path(queue, data_dir)
        print(f"== worker {queue} ({path}) ==")
        if not path.exists():
            print("(no log yet)")
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-20:]:
            print(line)
        print()
    return 0


def cmd_cancel(argv: list[str]) -> int:
    if not argv:
        print("usage: tb cancel TASK_ID", file=sys.stderr)
        return 2
    task = cancel_task(argv[0], _data_dir())
    print(f"canceled {task['id']}")
    return 0


def cmd_setup(_argv: list[str]) -> int:
    return run_setup(_data_dir())


def cmd_worker(argv: list[str]) -> int:
    if not argv:
        print("usage: tb worker setup|start|stop|restart|run high|mid|low", file=sys.stderr)
        return 2
    action, rest = argv[0], argv[1:]
    if action in {"setup", "init", "config"}:
        return run_setup(_data_dir())
    if action == "run":
        if not rest or rest[0] not in QUEUES:
            print("usage: tb worker run high|mid|low", file=sys.stderr)
            return 2
        return run_worker_loop(rest[0], _data_dir())
    if action not in {"start", "stop", "restart"} or not rest or rest[0] not in QUEUES:
        print("usage: tb worker setup|start|stop|restart high|mid|low", file=sys.stderr)
        return 2
    queue = rest[0]
    data_dir = _data_dir()
    if action in {"start", "restart"}:
        err = _require_configured(queue, data_dir)
        if err:
            print(f"tb: {err}", file=sys.stderr)
            return 1
    if action == "stop":
        if stop_worker(queue, data_dir):
            print(f"stopped {queue} worker")
        else:
            print(f"{queue} worker is not running")
        return 0
    if action == "restart":
        stop_worker(queue, data_dir)
        state = start_worker(queue, data_dir)
        print(f"restarted {queue} worker pid={state.get('pid')}")
        return 0
    state = start_worker(queue, data_dir)
    if state.get("running"):
        print(f"started {queue} worker pid={state.get('pid')}")
    else:
        print(f"tried to start {queue} worker pid={state.get('pid')}")
    return 0


def cmd_config(argv: list[str]) -> int:
    if not argv or argv[0] == "show":
        cfg = load_config()
        from tb.config import dump_simple_yaml

        print(dump_simple_yaml(cfg), end="")
        print("密钥（不显示内容）：")
        workers = cfg.get("workers") or {}
        for queue in QUEUES:
            env_name = str((workers.get(queue) or {}).get("api_key_env") or f"TB_{queue.upper()}_API_KEY")
            print(f"  {queue:<4} {env_name}: {secret_status(env_name, _data_dir())}")
        return 0
    if argv[0] in {"setup", "init"}:
        return run_setup(_data_dir())
    if argv[0] == "set":
        if len(argv) < 3:
            print("usage: tb config set KEY VALUE", file=sys.stderr)
            return 2
        set_config_value(argv[1], " ".join(argv[2:]))
        print(f"set {argv[1]}")
        return 0
    print("usage: tb config show|set|setup", file=sys.stderr)
    return 2


def _live_view(title: str, render, interval: float = 1.0) -> int:
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")
            print(f"{title}  {time.strftime('%Y-%m-%d %H:%M:%S')}   Ctrl-C to exit")
            print()
            render()
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        return 0


def cmd_list(argv: list[str]) -> int:
    aliases = {
        "worker": "worker",
        "workers": "worker",
        "queue": "queue",
        "queues": "queue",
        "factory": "factory",
        "factories": "factory",
        "任务工厂": "factory",
        "all": "all",
    }
    once = False
    kind = "all"
    for arg in argv:
        if arg in {"--once", "-1"}:
            once = True
            continue
        mapped = aliases.get(arg)
        if mapped is None:
            print("usage: tb list [worker|queue|factory] [--once]", file=sys.stderr)
            return 2
        kind = mapped

    def render() -> None:
        if kind in {"all", "worker"}:
            if kind == "all":
                print("workers")
            _list_workers()
        if kind == "all":
            print()
        if kind in {"all", "queue"}:
            if kind == "all":
                print("queues")
            _list_queues()
        if kind == "all":
            print()
        if kind in {"all", "factory"}:
            if kind == "all":
                print("factories")
            _list_factories()

    if once:
        render()
        return 0
    title = {"all": "tb list", "worker": "tb list worker", "queue": "tb list queue", "factory": "tb list factory"}[kind]
    return _live_view(title, render)


def _list_workers() -> int:
    queues = configured_queues(_data_dir())
    if not queues:
        print("没有已配置 API key 的队列。运行 tb setup 配置后使用。")
        return 0
    workers = all_worker_statuses(_data_dir())
    print(f"{'QUEUE':<6} {'STATE':<10} {'PID':<8} {'PROVIDER':<18} {'MODEL':<16} TASK")
    for queue in queues:
        state = workers[queue]
        running = "running" if state.get("running") else "stopped"
        pid = str(state.get("pid") or "-")
        provider = str(state.get("provider") or "stub")
        model = str(state.get("model") or "unset")
        task = state.get("current_task") or "-"
        print(f"{queue:<6} {running:<10} {pid:<8} {provider:<18} {model:<16} {task}")
    return 0


def _list_queues() -> int:
    queues = configured_queues(_data_dir())
    if not queues:
        print("没有已配置 API key 的队列。运行 tb setup 配置后使用。")
        return 0
    c = counts(_data_dir())
    print(f"{'QUEUE':<6} {'PENDING':<9} {'RUNNING':<9} {'DONE':<8} {'FAILED':<8} {'CANCELED'}")
    for queue in queues:
        q = c[queue]
        print(
            f"{queue:<6} {q['pending']:<9} {q['running']:<9} {q['done']:<8} {q['failed']:<8} {q['canceled']}"
        )
    return 0


def _list_factories() -> int:
    items = list_factories(_data_dir())
    if not items:
        print("no factories yet")
        print("add one: tb factory add --id NAME --title TITLE --prompt PROMPT [--queue high|mid|low]")
        return 0
    print(f"{'ID':<16} {'QUEUE':<6} TITLE")
    for item in items:
        print(f"{item.get('id', '-'):<16} {item.get('queue', '-'):<6} {item.get('name') or item.get('title') or '-'}")
    return 0


def cmd_factory(argv: list[str]) -> int:
    if not argv:
        print("usage: tb factory add|run|list", file=sys.stderr)
        return 2
    action, rest = argv[0], argv[1:]
    if action in {"list", "ls"}:
        return _list_factories()
    if action == "run":
        if not rest:
            print("usage: tb factory run ID", file=sys.stderr)
            return 2
        data_dir = _data_dir()
        factories = {f.get("id"): f for f in list_factories(data_dir)}
        factory = factories.get(rest[0])
        if factory:
            err = _require_configured(str(factory.get("queue") or "mid"), data_dir)
            if err:
                print(f"tb: {err}", file=sys.stderr)
                return 1
        task = run_factory(rest[0], data_dir)
        started = maybe_autostart(task["queue"], data_dir)
        print(f"enqueued {task['id']} on {task['queue']} from factory {rest[0]}")
        if started and (started.get("running") or started.get("starting")):
            print(f"started {task['queue']} worker pid={started.get('pid')}")
        return 0
    if action == "add":
        parser = argparse.ArgumentParser(prog="tb factory add")
        parser.add_argument("--id", required=True)
        parser.add_argument("--title", required=True)
        parser.add_argument("--prompt", default="")
        parser.add_argument("--queue", choices=QUEUES, default="mid")
        parser.add_argument("--name", default="")
        args = parser.parse_args(rest)
        err = _require_configured(args.queue, _data_dir())
        if err:
            print(f"tb: {err}", file=sys.stderr)
            return 1
        prompt = args.prompt
        if not prompt and not sys.stdin.isatty():
            prompt = sys.stdin.read()
        factory = add_factory(args.id, args.title, prompt, args.queue, args.name, _data_dir())
        print(f"added factory {factory['id']} -> {factory['queue']}")
        return 0
    print("usage: tb factory add|run|list", file=sys.stderr)
    return 2



if __name__ == "__main__":
    raise SystemExit(main())
