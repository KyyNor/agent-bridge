# TODO

- [x] SQLite 改成 WAL 模式
- [x] 解耦 CLI 与服务端：当前 CLI 很多功能依赖 localhost 的服务或本地文件，尤其是 `profile use` 命令依赖本地实时更新的说明文档
- [ ] （可选）服务管理支持 CLI 类型的服务
- [x] 把 wiki 和 codegraph 从 `execute` 提取到顶层 MCP 工具

## Agent 相关

- [x] Agent 协助编写脚本
- [x] Agent 协助编写工作流
- [ ] 工作流图翻译为中文（当前解析出的图均为英文）

## 记忆

- [x] 集成 claude-mem 作为记忆引擎，agent-bridge 负责控制平面

  结论：
  - 不做纯 native 重写，也不把 claude-mem 当完全黑盒直接塞进来。
  - 采用混合收敛方案：agent-bridge 管 UI、profile、hook 注入、MCP 门面；claude-mem 管 worker、压缩、存储、检索。
  - 第一阶段不 fork claude-mem、不改其内部 schema，先通过 worker API 和 MCP/search 能力完成集成验证。

  主要工作：
  - [x] 新增「记忆区块」实体与 UI，可创建、列表查看、进入详情查看记忆内容。
  - [x] profile 增加最多一个 active memory block 的单选绑定。
  - [x] `profile use` 根据 active memory block 注入/更新/清理 Claude Code hooks。
  - [x] 新增 agent-bridge hook wrapper：读取 Claude Code hook stdin payload，补充 profile/memory block 作用域，转发到服务端 hook API；worker 不可用时静默降级。
  - [x] 修改 `agb` 命令行的 hook 行为：当 user 和 project 都配置了 claude-mem hook 时，以 project 为准（project 覆盖 user），避免重复注入或冲突。
  - [ ] 新增 claude-mem worker 管理与健康检查：处理未安装、端口变化、版本过旧、worker 未启动、Bun/uv 缺失等状态。
    - 第一阶段已支持服务端插件发现、`CLAUDE_MEM_DATA_DIR` 隔离、配置化 worker URL、基础健康状态与 hook 降级；自动端口发现、版本兼容矩阵、Bun/uv 诊断放第二阶段。
  - [x] 在 agent-bridge FastMCP gateway 暴露 `memory_search` / `memory_timeline` / `memory_get`，背后代理 claude-mem 检索，避免用户面对两套 MCP 入口。
  - [x] 设计 memory block 与 claude-mem project/data dir 的映射，避免 profile 作用域和 claude-mem 默认 project 作用域混乱。
  - [ ] 保留 `<private>`/敏感字段过滤/截断策略，明确额外 observer LLM 调用带来的成本模型。
  - [x] SQLite 改 WAL 作为前置或同期工作，降低 hook 高频写入带来的锁冲突风险。
# 工作流

- [ ] 工作流执行日志聚合：一个 workflow run 作为顶层记录，内部关联多个 Agent Run；普通 Agent Run 继续独立展示。第一版先由 `workflow_node_runs` 保存关联关系。
