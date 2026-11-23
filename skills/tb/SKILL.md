---
name: tb
description: 操作本机 tb 任务队列系统（创建/查询/取消任务、查看队列与 worker 状态、管理任务工厂）。当用户要派发任务、查看任务进度、管理队列时使用。
---

# tb 任务队列

tb 是本机的命令行任务队列工具，把任务派给不同优先级的 LLM worker 异步执行。

## 核心概念

- **三个优先级队列**：`high`（紧急/重要）、`mid`（日常）、`low`（后台/批量）
- 每个队列有独立 worker，调用各自配置的 LLM（OpenAI 兼容协议）处理任务
- 任务状态：`pending` → `running` → `done` / `failed` / `canceled`
- **只有配了 API key 的队列才可见、可入队**；未配置时先 `tb setup`
- 数据目录默认 `~/.tb/`（环境变量 `TB_DATA_DIR` 可覆盖）

## 常用命令

```bash
# 创建任务（只配了一个队列时队列名可省略；prompt 可用 --file 或管道传入）
tb enqueue high --title "修复登录崩溃" --prompt "复现步骤：…，请定位原因并给出修复方案"
echo "长文本 prompt" | tb enqueue mid --title "分析日志"

# 查看状态
tb status              # 队列计数 + worker 状态 + 进行中任务
tb list --once         # 表格视图（worker/queue/factory）
tb watch               # 分栏实时监控，Ctrl-C 退出
tb logs [task-id]      # worker 日志 / 任务详情（含 result、error）

# 管理任务
tb cancel TASK_ID

# 连通性与对话
tb test [queue]        # 测试 API 连通性
tb chat [queue]        # 与队列模型对话（支持工具调用，可直接操作 tb）

# 配置
tb setup [queue]       # 交互式配置 URL / API key / 模型（key 存 ~/.tb/secrets.env）
tb config show         # 查看配置与密钥状态
```

## 任务工厂（模板复用）

```bash
tb factory add --id daily-report --title "生成日报" --prompt "汇总今日 git 提交" --queue mid
tb factory run daily-report    # 从模板创建任务
tb factory list
```

## 工作建议

- 创建任务时：`title` 一句话概括，`prompt` 写清背景、要求和期望输出
- 优先级判断：紧急重要 → `high`；常规 → `mid`；批量/可延后 → `low`；拿不准用 `mid`
- 入队后 worker 会自动拉起（`auto_start_workers: true`），空闲 120 秒自动退出
- 查询类问题先跑 `tb status` / `tb list --once` 拿真实数据，不要猜
