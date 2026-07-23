# Agent Bridge 开发约定

本文件适用于整个仓库。更完整的架构背景见 `CLAUDE.md`；命令与使用方式见 `README.md`。

## 开发原则

1. 应用层只编排领域服务和 adapter，不根据具体后端类型写 `isinstance` 或多段 `if/elif`。
2. 同一能力的不同实现必须通过 Protocol、adapter 和 registry 接入。新增实现时优先注册，不修改中心分发链。
3. 不通过 monkeypatch、动态 `setattr` 或修改任意异常对象传递业务状态。错误上下文使用明确类型，并通过 `raise ... from exc` 保留原因。
4. 重复出现的进程生命周期、网络传输、JSONL 解析、过滤和状态管理应提取为共享组件；保留各协议真实差异。
5. `AgentBridgeService` 是装配与兼容门面，不承载大段具体领域逻辑。新增业务进入对应领域 service。
6. Python 和 Vue 单文件应保持单一职责。文件持续增长到约 800 行时必须评估拆分；超过 1200 行原则上不再增加新职责。

## 后端与标识约束

- 文档知识后端实现 `BackendAdapter`；可选能力通过独立 runtime-checkable Protocol 表达。
- 能力来源实现 `CapabilitySourceAdapter` 并注册到来源 registry。
- Coding Agent 的配置暂时要求 `slug == type`。在支持实例级差异配置之前，不创建同一 type 的多个无差异 slug。
- OpenCode 使用由 Agent Bridge 按 run 管理的 server HTTP 模式；server 启动、就绪探测、SSE framing、请求和回收集中在 `opencode_server.py`，adapter 只负责 OpenCode V1 API 事件与统一事件模型的映射，便于未来替换 V2 client。
- Mock 后端只能由显式 `type = "mock"` 使用，不得作为未知或缺失配置的静默回退。
- CodeGraph CLI/MCP 是同一正式后端的两种调用通道，统一通过 `CodeGraphBackend` 使用。
- 禁止为 CodeGraph 恢复 SQLite 隐式文本索引降级；后端缺失或索引未就绪必须明确失败。
- 仓库文件读取和文件列表基于 Git 镜像，不应依赖 CodeGraph 后端。

## 时间处理规范

- 禁止新增 `datetime.utcnow()`、naive UTC 或散落的 `datetime.now(...).isoformat()`。
- 统一使用 `agent_bridge.core.timeutil`：获取当前时间用 `utc_now()`，持久化用 `utc_iso()`，解析历史时间用 `parse_utc()`。
- 内部时间对象必须带 UTC 时区；对外序列化格式由公共 helper 固定，不在调用点手写 `replace("+00:00", "Z")`。
- 涉及过期、租约、缓存和耗时的逻辑必须有边界测试；持续时间使用 monotonic 时钟。

## 日志、文档与语言

- 使用 `logging.getLogger(__name__)` 和 `%s` 惰性参数；不要直接引入 loguru。
- 生命周期、降级、拒绝、失败等核心日志必须使用清晰中文，并包含可定位的实体 key、阶段、状态和耗时。
- 不得静默吞掉核心异常。允许容错的边界也要记录 warning 或持久化错误状态。
- 注释、docstring、README、CLAUDE、AGENTS 和用户可见错误优先中文；协议字段、标识符和外部产品名保留英文。
- 代码行为、CLI、配置或目录变化时，同一提交同步更新 README/CLAUDE/AGENTS 和示例。
- Agent 运行的工具输入/输出、模型推理详情与阶段耗时统一进入规范化事件流；短 payload 可内联，长 payload 只能通过运行目录的安全相对引用按需读取。时间轴可以把同一工具调用的开始/结果和同一文本流的增量合并展示，但不得丢失原始事件。

## 前端约定

- 页面复用的判断、格式化、状态加载进入 `src/lib` 或 `src/composables`；页面不得再定义同名但语义相反的私有实现。
- 大视图按功能区域提取组件，不在单个 `.vue` 中继续累积列表、编辑、运行、详情等多种职责。
- 设计稿和原型不得放入 Vite `public/` 生产目录。测试应验证真实组件或行为。
- 保持 Chrome 90 兼容约束和现有语义颜色令牌体系。

## 验证与提交

常规修改至少运行相关领域测试。跨领域或发布相关修改运行：

```bash
./scripts/test.sh full -q
```

前端修改至少运行：

```bash
cd frontend/capabilities
npm run check
```

发布构建必须从干净依赖开始构建前端，再构建并安装 wheel 做 smoke test。提交只包含当前任务文件，不覆盖或清理其他人的未提交修改。
