# MCP execute 参数兼容对照

`fixtures.py` 定义 6 个复杂工具。`server.py` 启动真实 stdio MCP 网关，业务端使用模拟器：只校验参数，不执行 Python/SQL、不发布文件。`run.py` 为 CMD/Codex 创建临时独立会话，模型通过原生 MCP 工具调用；`replay.py` 独立注入错误参数，不能把它的结果当作模型自然成功率。

在仓库根目录运行（输出目录必须是新的，日志采用追加模式）：

```bash
git show 04c311d:src/agent_bridge/capability_hub/gateway/metamcp.py > /tmp/metamcp-before.py
.venv/bin/python scripts/mcp_argument_benchmark/run.py --output /tmp/mcp-qwen-old --baseline /tmp/metamcp-before.py --client cmd --model qwen/qwen3.8-27b --variant old
.venv/bin/python scripts/mcp_argument_benchmark/run.py --output /tmp/mcp-qwen-new --baseline /tmp/metamcp-before.py --client cmd --model qwen/qwen3.8-27b --variant new
.venv/bin/python scripts/mcp_argument_benchmark/replay.py --output /tmp/mcp-replay --baseline /tmp/metamcp-before.py
```

Codex 使用 `--client codex --model gpt-6-luna`。每次执行最多 480 秒；CMD 最多 35 轮。客户端沿用本机登录信息，不写入仓库。临时 CMD 项目只预授权这两个模拟 MCP 工具。CLI 原始输出保留在输出目录，提交证据仅包含虚构数据的 MCP 调用记录和状态。

旧侧加载指定提交的真实网关模块，模拟业务按 Schema 验证；新侧加载当前网关模块，模拟业务调用生产校验 helper。两侧工具声明、任务和业务目标一致。生产服务层的权限与审计由领域测试覆盖，本脚本不证明真实下游部署可用。

单轮短上下文试验只能揭示具体失败，不能据此排名模型或外推至内网量化部署、200k 长上下文。耗时包含客户端启动和远端排队，不作为速度比较。
