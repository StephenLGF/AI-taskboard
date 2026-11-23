"""Interactive wizard to configure each worker class."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from tb.config import load_config, save_config, worker_config
from tb.paths import DATA_DIR, QUEUES
from tb.prompt import ask, ask_secret, choose, confirm
from tb.secrets import has_secret, secrets_path, set_secret
from tb.worker import all_worker_statuses, start_worker, stop_worker

QUEUE_LABELS = {
    "high": "高级 high",
    "mid": "中级 mid",
    "low": "低级 low",
}

PRESETS: list[tuple[str, str, str]] = [
    ("openai", "OpenAI", "https://api.openai.com/v1"),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
    ("moonshot", "Moonshot / Kimi", "https://api.moonshot.cn/v1"),
    ("xai", "xAI / Grok", "https://api.x.ai/v1"),
    ("siliconflow", "SiliconFlow", "https://api.siliconflow.cn/v1"),
    ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
    ("custom", "自定义 URL", ""),
]


def normalize_base_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url = url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    path = url.split("://", 1)[-1]
    host_and_path = path.split("/", 1)
    if len(host_and_path) == 1:
        url += "/v1"
    return url


def fetch_models(base_url: str, api_key: str, timeout: float = 12.0) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "tb",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    names: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                name = item.get("id") or item.get("name")
            else:
                name = item
            if name:
                names.append(str(name))
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def _queue_summary(queue: str, cfg: dict[str, Any], data_dir: Path) -> str:
    worker = worker_config(cfg, queue)
    provider = worker.get("provider") or "stub"
    model = worker.get("model") or "未设模型"
    url = worker.get("base_url") or "未设 URL"
    key_name = str(worker.get("api_key_env") or "")
    key = "有密钥" if key_name and has_secret(key_name, data_dir) else "无密钥"
    if provider == "stub":
        return f"{QUEUE_LABELS[queue]}  ·  stub 占位"
    return f"{QUEUE_LABELS[queue]}  ·  {provider} / {model}  ·  {key}"


def _pick_queues(cfg: dict[str, Any], data_dir: Path) -> list[str]:
    options: list[tuple[str, str]] = [(q, _queue_summary(q, cfg, data_dir)) for q in QUEUES]
    options.append(("quit", "退出"))
    default = 0
    for i, queue in enumerate(QUEUES):
        worker = worker_config(cfg, queue)
        if (worker.get("provider") or "stub") == "stub" or not worker.get("model"):
            default = i
            break
    chosen = choose("先选择要配置的 Worker 队列：", options, default=default)
    if chosen == "quit":
        return []
    return [chosen]


def _configure_one(queue: str, cfg: dict[str, Any], data_dir: Path) -> bool:
    print(f"正在配置 {QUEUE_LABELS[queue]} worker")
    print()
    worker = worker_config(cfg, queue)
    kind = choose(
        "选择接口类型：",
        [
            ("openai_compatible", "OpenAI 兼容（输入 URL / API key / 模型）"),
            ("stub", "占位 stub（不调用真实模型）"),
        ],
        default=0 if worker.get("provider") != "stub" else 0,
    )
    if kind == "stub":
        worker["provider"] = "stub"
        save_config(cfg)
        print(f"已将 {queue} 设为 stub。")
        print()
        return True

    preset_options = [(name, f"{label}  ·  {url}" if url else label) for name, label, url in PRESETS]
    current_url = str(worker.get("base_url") or "")
    default_preset = len(PRESETS) - 1
    for i, (_name, _label, url) in enumerate(PRESETS):
        if current_url and url and current_url.rstrip("/") == url:
            default_preset = i
            break
    preset = choose("选择服务：", preset_options, default=default_preset)
    preset_url = next(url for name, _label, url in PRESETS if name == preset)
    if preset == "custom":
        base_url = normalize_base_url(
            ask("请输入 API Base URL（一般以 /v1 结尾）", default=current_url or "", allow_empty=False)
        )
    else:
        use_preset = True
        if current_url and current_url.rstrip("/") != preset_url:
            use_preset = confirm(f"使用推荐地址 {preset_url}？选否可改成自定义", default=True)
        if use_preset:
            base_url = preset_url
            print(f"API URL：{base_url}")
            print()
        else:
            base_url = normalize_base_url(ask("请输入 API Base URL", default=current_url, allow_empty=False))

    env_name = str(worker.get("api_key_env") or f"TB_{queue.upper()}_API_KEY")
    existing = has_secret(env_name, data_dir)
    secret = ask_secret("API key", has_existing=existing)
    if secret is not None:
        set_secret(env_name, secret, data_dir)
        api_key = secret
        print("密钥已写入 secrets.env（权限 600，不写入 config.yaml）")
        print()
    else:
        from tb.config import resolve_api_key

        api_key = resolve_api_key(worker, data_dir)
        print("已保留现有密钥")
        print()

    model = _pick_model(str(worker.get("model") or ""), base_url, api_key)

    worker["provider"] = "openai_compatible"
    worker["base_url"] = base_url
    worker["model"] = model
    worker["api_key_env"] = env_name

    print("即将保存：")
    print(f"  队列     {queue}")
    print(f"  provider openai_compatible")
    print(f"  URL      {base_url}")
    print(f"  模型     {model}")
    print(f"  密钥     已保存到 {secrets_path(data_dir)}，不写入配置文件")
    print()
    if not confirm("确认保存这次配置？", default=True):
        print("已放弃这次修改。")
        print()
        return False
    save_config(cfg)
    print(f"已保存 {queue} worker 配置。")
    print()
    return True


def _pick_model(current: str, base_url: str, api_key: str) -> str:
    print("正在获取模型列表…")
    names: list[str] = []
    error = ""
    try:
        names = fetch_models(base_url, api_key)
    except urllib.error.HTTPError as exc:
        error = f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or exc.__class__.__name__

    if not names:
        if error:
            print(f"无法自动拉取模型列表（{error}），请手动输入。")
        else:
            print("接口没有返回模型列表，请手动输入。")
        print()
        return ask("请输入模型名", default=current, allow_empty=False)

    print(f"找到 {len(names)} 个模型。")
    print()
    visible = names
    if len(names) > 40:
        keyword = ask("模型比较多，可输入关键字筛选（直接回车看全部前 40 个）", default="")
        if keyword:
            visible = [name for name in names if keyword.lower() in name.lower()]
            if not visible:
                print("没有匹配的模型，改为手动输入。")
                print()
                return ask("请输入模型名", default=current, allow_empty=False)
        visible = visible[:40]

    options: list[tuple[str, str]] = [(name, name) for name in visible]
    options.append(("__custom__", "自己输入模型名"))
    default = 0
    if current:
        for i, name in enumerate(visible):
            if name == current:
                default = i
                break
    chosen = choose("选择要使用的模型：", options, default=default)
    if chosen == "__custom__":
        return ask("请输入模型名", default=current, allow_empty=False)
    return chosen


def _maybe_restart(queues: list[str], data_dir: Path) -> None:
    statuses = all_worker_statuses(data_dir)
    running = [queue for queue in queues if statuses.get(queue, {}).get("running")]
    if not running:
        return
    labels = "、".join(running)
    if not confirm(f"{labels} worker 正在运行，要立刻重启以加载新配置吗？", default=True):
        print("稍后可用 tb worker restart <queue> 手动重启。")
        return
    for queue in running:
        stop_worker(queue, data_dir)
        state = start_worker(queue, data_dir)
        print(f"已重启 {queue} worker pid={state.get('pid')}")


def run_setup(data_dir: Path | None = None) -> int:
    data_dir = data_dir or DATA_DIR
    print("tb worker 配置向导")
    print("按 Ctrl-C 随时退出。API key 只写到 ~/.tb/secrets.env，不会进入 config.yaml。")
    print()
    try:
        cfg = load_config()
        queues = _pick_queues(cfg, data_dir)
        if not queues:
            print("已退出。")
            return 0
        saved: list[str] = []
        for queue in queues:
            if _configure_one(queue, cfg, data_dir):
                saved.append(queue)
        if not saved:
            print("没有保存任何更改。")
            return 0
        if len(queues) == 1:
            while confirm("还要配置其他队列吗？", default=False):
                remaining = [q for q in QUEUES if q not in saved]
                if not remaining:
                    print("三个队列都已在这次向导里保存过了。")
                    break
                options = [(q, _queue_summary(q, cfg, data_dir)) for q in remaining]
                nxt = choose("接下来配置哪一个？", options)
                if _configure_one(nxt, cfg, data_dir):
                    saved.append(nxt)
        _maybe_restart(saved, data_dir)
        print("配置完成。可以用 tb list worker 查看，或 tb enqueue 试跑一条任务。")
        return 0
    except KeyboardInterrupt:
        print()
        print("已取消。")
        return 130
    except RuntimeError as exc:
        print(f"tb: {exc}")
        return 1
