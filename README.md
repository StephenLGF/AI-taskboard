# tb

高级 / 中级 / 低级任务队列和可启停的 Worker。命令名是 `tb`。

数据目录默认在 `~/.tb/`，代码和状态分开存放。

## 命令

```
tb enqueue high|mid|low --title TITLE --prompt PROMPT
tb status
tb watch
tb list [worker|queue|factory]     (live; add --once for one shot)
tb factory add --id ID --title TITLE --prompt PROMPT [--queue high|mid|low]
tb factory run ID
tb logs [task-id]
tb cancel TASK_ID
tb worker start|stop|restart high|mid|low
tb setup                         # 问答式配置每个队列的 URL / API key / 模型
tb config show
tb config set KEY VALUE
```

入队后如果开启了 `auto_start_workers`（默认开），对应 Worker 会自动拉起。队列空了并空闲超过 `idle_timeout_seconds`（默认 120 秒）后自动退出。

当前 Worker 默认 `provider: stub`，会把任务跑通并写下占位结果。接入真实模型时，在终端运行：

```
tb setup
```

按提示用上下键选择队列、服务和模型，并输入 URL 与 API key。也可以一次只配其中一个：`tb worker setup`。

API key 只保存在 `~/.tb/secrets.env`（权限 600），不会写入 `config.yaml`。环境变量 `TB_HIGH_API_KEY` / `TB_MID_API_KEY` / `TB_LOW_API_KEY` 仍然优先于该文件。
