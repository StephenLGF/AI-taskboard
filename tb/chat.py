"""Interactive chat with a configured queue's model (OpenAI-compatible).

The assistant knows about tb itself: its system prompt is built from the
bundled SKILL.md, and it can create/list/cancel tasks and inspect queue
state through function-calling tools — so "帮我创建个任务" actually
enqueues a task instead of just talking about it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from tb.config import load_config, resolve_api_key, worker_config
from tb.paths import DATA_DIR, QUEUES
from tb.skill import skill_body
from tb.store import cancel_task, counts, enqueue_task, iter_tasks

_BEHAVIOR_RULES = """你可以通过工具直接操作本机的 tb：创建任务、查询队列和任务状态、取消任务。

行为准则：
- 用户要创建/添加/派发任务时，调用 tb_enqueue 真正创建，不要只给建议；title 简短概括，prompt 写清具体要做什么
- 用户没指定优先级时自行判断，拿不准用 mid 并说明理由
- 回答状态类问题前必须调用工具拿真实数据，禁止编造任务 ID 或数量
- 创建成功后告诉用户任务 ID 和所在队列
- 用中文，回答简洁"""


def _system_prompt() -> str:
    try:
        knowledge = skill_body()
    except FileNotFoundError:
        knowledge = "（tb skill 文件缺失，仅按通用助手回答）"
    return (
        "你是 tb 任务队列系统的内置助手，运行在本机。\n\n"
        f"以下是 tb 的完整使用文档（skill）：\n\n{knowledge}\n\n"
        f"{_BEHAVIOR_RULES}"
    )


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "tb_enqueue",
            "description": "创建一个新任务放入指定优先级队列",
            "parameters": {
                "type": "object",
                "properties": {
                    "queue": {"type": "string", "enum": list(QUEUES)},
                    "title": {"type": "string", "description": "简短标题"},
                    "prompt": {"type": "string", "description": "任务的详细要求/上下文"},
                },
                "required": ["queue", "title", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tb_list_tasks",
            "description": "列出任务，可按队列和状态过滤",
            "parameters": {
                "type": "object",
                "properties": {
                    "queue": {"type": "string", "enum": list(QUEUES)},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "running", "done", "failed", "canceled"],
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tb_status",
            "description": "查看各队列任务计数和 worker 运行状态",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tb_cancel",
            "description": "取消一个 pending/running 状态的任务",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
]

MAX_TOOL_ROUNDS = 6


def _execute_tool(name: str, args: dict[str, Any], data_dir: Path) -> str:
    """Run a tb tool locally and return a JSON-string result for the LLM."""
    try:
        if name == "tb_enqueue":
            task = enqueue_task(
                str(args["queue"]), str(args["title"]), str(args["prompt"]), data_dir
            )
            from tb.worker import maybe_autostart

            maybe_autostart(task["queue"], data_dir)
            return json.dumps(
                {"ok": True, "id": task["id"], "queue": task["queue"], "title": task["title"]},
                ensure_ascii=False,
            )
        if name == "tb_list_tasks":
            tasks = iter_tasks(
                data_dir,
                queue=args.get("queue") or None,
                status=args.get("status") or None,
            )
            out = [
                {
                    "id": t["id"],
                    "queue": t["queue"],
                    "status": t["status"],
                    "title": t["title"],
                }
                for t in list(tasks)[:30]
            ]
            return json.dumps({"tasks": out, "count": len(out)}, ensure_ascii=False)
        if name == "tb_status":
            from tb.worker import all_worker_statuses

            c = counts(data_dir)
            workers = all_worker_statuses(data_dir)
            return json.dumps(
                {
                    "queues": c,
                    "workers": {
                        q: {
                            "running": bool(w.get("running")),
                            "current_task": w.get("current_task"),
                        }
                        for q, w in workers.items()
                    },
                },
                ensure_ascii=False,
            )
        if name == "tb_cancel":
            task = cancel_task(str(args["task_id"]), data_dir)
            return json.dumps({"ok": True, "id": task["id"]}, ensure_ascii=False)
        return json.dumps({"error": f"unknown tool: {name}"})
    except (ValueError, FileNotFoundError, KeyError, OSError) as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def _require_backend(wcfg: dict[str, Any]) -> tuple[str, str, str]:
    model = str(wcfg.get("model") or "").strip()
    base_url = str(wcfg.get("base_url") or "").rstrip("/")
    api_key = resolve_api_key(wcfg)
    if not model or not base_url:
        raise ValueError("模型或 base_url 未配置，先运行 tb setup")
    if not api_key:
        raise ValueError(f"API key 未设置（{wcfg.get('api_key_env')}）")
    return model, base_url, api_key


def _post(
    messages: list[dict[str, Any]],
    wcfg: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """One chat/completions round-trip; returns the assistant message object."""
    model, base_url, api_key = _require_backend(wcfg)
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:300]}") from exc
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("LLM 未返回内容")
    return choices[0].get("message") or {}


def run_turn(
    messages: list[dict[str, Any]],
    wcfg: dict[str, Any],
    data_dir: Path,
    on_tool: Callable[[str, dict[str, Any], str], None] | None = None,
    use_tools: bool = True,
) -> str:
    """Run one user turn, executing tool calls until a final text reply."""
    tools = TOOLS if use_tools else None
    for _round in range(MAX_TOOL_ROUNDS):
        try:
            message = _post(messages, wcfg, tools=tools)
        except RuntimeError as exc:
            # 后端不支持 tools 时降级为纯对话
            if tools and "400" in str(exc):
                tools = None
                message = _post(messages, wcfg)
            else:
                raise
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            content = message.get("content")
            if not content:
                raise RuntimeError("LLM 返回了空内容")
            messages.append({"role": "assistant", "content": str(content)})
            return str(content)
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
        )
        for call in tool_calls:
            func = call.get("function") or {}
            name = str(func.get("name") or "")
            try:
                args = json.loads(func.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _execute_tool(name, args, data_dir)
            if on_tool:
                on_tool(name, args, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or ""),
                    "content": result,
                }
            )
    raise RuntimeError("工具调用轮数过多，已中止")


def run_chat(queue: str, data_dir: Path = DATA_DIR, one_shot: str | None = None) -> int:
    cfg = load_config()
    wcfg = worker_config(cfg, queue)
    model = str(wcfg.get("model") or "unset")
    messages: list[dict[str, Any]] = [{"role": "system", "content": _system_prompt()}]

    def _print_tool(name: str, args: dict[str, Any], result: str) -> None:
        summary = json.dumps(args, ensure_ascii=False)
        if len(summary) > 80:
            summary = summary[:77] + "..."
        print(f"  ⚙ {name} {summary}")

    if one_shot is not None:
        messages.append({"role": "user", "content": one_shot})
        print(run_turn(messages, wcfg, data_dir, on_tool=_print_tool))
        return 0

    print(f"tb chat [{queue}]  模型 {model}")
    print("我可以直接操作 tb：创建任务、查状态、取消任务。")
    print("/clear 清空上下文，/exit 退出（Ctrl-C 也可以）。")
    print()
    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in {"/exit", "/quit", "/q"}:
            return 0
        if line == "/clear":
            messages[:] = [m for m in messages if m["role"] == "system"]
            print("（上下文已清空）")
            continue
        messages.append({"role": "user", "content": line})
        try:
            reply = run_turn(messages, wcfg, data_dir, on_tool=_print_tool)
        except (RuntimeError, ValueError) as exc:
            messages.pop()
            print(f"请求失败：{exc}")
            continue
        print(f"{model}> {reply}")
        print()
