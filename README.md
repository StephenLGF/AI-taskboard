# tb

高级 / 中级 / 低级任务队列和可启停的 Worker。命令名是 `tb`。

数据目录默认在 `~/.tb/`，代码和状态分开存放。

## 命令

```
tb enqueue [high|mid|low] --title TITLE --prompt PROMPT   # 只配一个队列时队列名可省略
tb status
tb watch                               # 已配置队列的分栏实时视图
tb list [worker|queue|factory]         # live; add --once for one shot
tb factory add --id ID --title TITLE --prompt PROMPT [--queue high|mid|low]
tb factory run ID
tb logs [task-id]
tb cancel TASK_ID
tb test [high|mid|low]                 # 测试 API 连通性
tb chat [high|mid|low] [-p PROMPT]     # 与队列模型对话，可直接操作 tb
tb skill [show|path|install]           # tb 的 Agent Skill
tb worker start|stop|restart high|mid|low
tb setup [high|mid|low]                # 问答式配置 URL / API key / 模型
tb config show
tb config set KEY VALUE
```

**只有配置了 API key 的队列才会显示、才能入队。** 新装先跑 `tb setup`。

入队后如果开启了 `auto_start_workers`（默认开），对应 Worker 会自动拉起。队列空了并空闲超过 `idle_timeout_seconds`（默认 120 秒）后自动退出。

## 配置

```
tb setup          # 交互式选择队列、服务和模型
tb setup high     # 直达指定队列
```

按提示用上下键选择服务和模型，输入 API key（星号回显）。保存后会主动询问是否测试连通性，也可随时 `tb test`。

API key 只保存在 `~/.tb/secrets.env`（权限 600），不会写入 `config.yaml`。环境变量 `TB_HIGH_API_KEY` / `TB_MID_API_KEY` / `TB_LOW_API_KEY` 仍然优先于该文件。

## tb chat

`tb chat` 不只是对话：system prompt 内置了 `skills/tb/SKILL.md` 的完整文档，并挂载了
`tb_enqueue` / `tb_list_tasks` / `tb_status` / `tb_cancel` 四个工具（function calling）。
对它说「帮我创建个任务」会真正入队，而不是只给建议。后端不支持 tools 时自动降级为纯对话。

## Agent Skill

`skills/tb/SKILL.md` 是 Agent Skills 标准格式的技能文件，描述 tb 的全部用法：

```
tb skill install    # 安装到 ~/.claude/skills/tb/，Claude Code 自动发现
tb skill path       # 打印文件位置，可加入其他 agent 的技能目录
```
