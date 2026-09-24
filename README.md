# Agent Bridge

Agent Bridge 是面向内部可信环境的 Agent 能力与知识管理平台。它把 MCP、OpenAPI、内置知识能力、Coding Agent、工作流、脚本和记忆统一到一个 FastAPI 服务，并提供 Vue 3 管理后台。

## 主要能力

- 注册 MCP 与 OpenAPI 服务，同步工具定义并统一检索、执行和审计。
- 通过 Profile 管理来源级与资源级访问策略，并将常用工具提升为 pinned tools。
- 管理文档知识库、CodeGraph 代码知识和 claude-mem 记忆。
- 支持 Claude、Codex、OpenCode、Pi、DSH 等 Coding Agent 后端。
- Coding Agent 可按后端配置思考力度（`server.toml` `[agents.<slug>]` 的 `effort`，或「系统管理 →
  知识处理配置 → Coding Agent 运行配置」编辑）：Claude 支持 low/medium/high/xhigh/max、Codex 支持
  minimal~xhigh、Pi 支持 off~xhigh，OpenCode 透传 provider 相关的 variant 名。留空使用各 CLI 默认值；
  例如部署模型不接受默认的 `high` 时，可把 Claude 后端设为 `xhigh` 或 `medium` 规避 litellm 报错。
  DSH 后端不支持配置思考力度（其托管供应商路由不暴露 reasoning effort，配置会被拒绝）。
- DSH 可作为 Coding Agent 后端执行后台任务：每次 run 以目标 Linux 用户身份启动独立的
  `dsh --profile acp` 进程，通过标准 Agent Client Protocol（JSON-RPC over stdio）完成一次
  任务并随 stdin EOF 优雅退出；模型接入（Base URL/模型/API Key）复用 DSH Web Runtime 的
  组级配置与用户级配置目录，后台 run 不依赖 Web Runtime 是否启动；`.mcp.json` 转换为 ACP
  的 MCP 声明，Profile 能力平面照常经 MetaMCP 网关生效，MCP 工具调用照常进入「调用日志」。
- OpenCode 由 Agent Bridge 按 run 启动并回收本机 server，通过 HTTP API 执行会话；当前使用
  `prompt_async` + `/event` SSE 实时接收文本、阶段和工具事件，OpenCode V1 事件映射与 server
  生命周期分开，便于未来替换 V2 client；运行时间轴会合并工具调用/结果和文本增量，同时保留
  原始事件用于诊断。
- 通过结构化 DAG 编排 Agent、脚本、任务领取和 Markdown/HTML 产物。
- 托管用户级 DSH Web Runtime：按业务用户隔离的交互式工作台进程，动态监听
  `127.0.0.1` 端口、以所属小组映射的 Linux 用户身份运行、长期空闲自动回收；
  公共 Base URL/模型与组级默认模型、API Key 在每次启动时注入 DSH 原生配置。
- 「DSH 工作台」页面按需在新标签页打开站内伪全屏工作台：`/agent-workspace/**`
  由后端反代到当前登录用户自己的 runtime（HTTP/WebSocket/SSE，服务端完成 DSH
  首次鉴权，浏览器不接触动态端口与 token），进入前可选择能力平面（也可以不选），
  所选 Profile 以短期 capability + DSH 原生 `--patch` 覆盖层注入 MCP。
- 工作流产物的标题、摘要、路径和正文使用 jieba 预分词与 SQLite FTS5 检索；长度至少 3 的 ASCII 标识符支持前缀匹配，结构化权限与版本过滤仍由 SQLite 普通条件处理。
- `artifacts_search` 使用 DiskCache 缓存检索结果，默认保留 8 小时；缓存时长可在「系统管理」页面修改，保存后立即按新配置生效。当前版本不主动因新产物写入而清理缓存。
- 「平台概览」使用 DiskCache 缓存聚合结果，默认保留 4 小时；缓存按用户、所属小组、可见资源范围和日期区间隔离，页面上的“刷新”会强制重建当前缓存。
- 管理服务端 Python 脚本、Skill、同步调度和插件更新。
- 在 `/agent-bridge/` 提供管理后台，在 `/api/v1/` 提供第一方 HTTP API，在 `/mcp` 提供 MetaMCP 入口。

「系统管理 → 顶层 MCP 工具」可查看所有可配置的 `/mcp` 顶层工具（`search` 和 `execute` 两个固定入口除外），并临时关闭其中任意工具。关闭会立即使该工具不再出现在 MCP tools/list 和能力目录中，且通过通用 `execute` 调用其对应内置能力同样会被拒绝；重新启用即可恢复。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 与 npm（构建管理后台时需要）
- CodeGraph CLI（启用代码图索引、查询和 Explore 时必须安装）

安装后端与前端依赖：

```bash
uv sync
cd frontend/capabilities
npm ci
```

## 启动

```bash
uv run agent-bridge server start
uv run agent-bridge server init
uv run agent-bridge server status
```

然后访问：

- 管理后台：<http://127.0.0.1:8765/agent-bridge/>
- 健康检查：<http://127.0.0.1:8765/health>
- MetaMCP：<http://127.0.0.1:8765/mcp>

停止服务：

```bash
uv run agent-bridge server stop
```

短命令 `agb` 与 `agent-bridge` 等价。当前 CLI 根命令只有 `server`、`profile`、`memory`；知识库、工作流、Agent 和系统管理通过管理后台或 HTTP API 管理。

## DSH Web Runtime

Agent Bridge 可为每个业务用户托管一个 DSH Web 实例（DeepSeek Harness 浏览器
工作台，试用/评估阶段）。实例按需启动、只在 `127.0.0.1` 上监听动态端口、
以该用户所属小组映射出的 Linux 用户身份运行；同一小组的多个业务用户共享
Linux uid，但各自的 DSH 配置目录互相独立：

```text
/home/<linux-user>/.config/dsh/<business-user>/
```

该目录同时是 DSH 的 `DSH_HOME`：DSH 自身在其中创建 `profiles/`、`storages/`
与 `.credentials.yaml`。目录由 Agent Bridge 在首次启动时创建并归属该 Linux
用户，不进入 `AGENT_BRIDGE_ROOT/data`；停止或回收实例不会删除其中的 DSH 配置
与 session 数据。

管理员在「系统管理 → DSH Web Runtime」维护两层配置：

- **公共接入（全局）**：`base_url`、`available_models[]`，以及启动命令模板
  （默认 `dsh web {patch} --host 127.0.0.1 --port {port} --no-open`）与空闲
  回收阈值（分钟，默认 120）。`base_url` 留空时回落为系统「公共模型配置」的
  Base URL；`--no-open` 关闭 DSH 的浏览器自启（工作台经站内反向代理访问）。
- **组级配置**：`linux_user`（小组映射的 Linux 用户，默认取小组标识）、
  `default_model`（必须取自全局可用模型列表）与敏感的 `api_key`（只写不回显）。

从按组保存 Base URL/模型的早期版本升级时，迁移会把各组已保存的值回填到全局
公共接入（只填全局为空的字段，不覆盖新值）再删除旧列，避免升级静默丢配置。

启动时 Agent Bridge 把公共接入与组级默认模型合并写入该用户 DSH 配置目录的
`settings.yaml`（`llm-pi-ai.providers.agent-bridge` 声明 Base URL、模型目录与
承载密钥的环境变量名，`agent-default-model` 指定默认模型），API Key 只经
`AGENT_BRIDGE_DSH_API_KEY` 环境变量传给 DSH 进程，不写入文件；用户自身的其他
settings（主题、onboarding 等）原样保留。

插件首装：插件名单内置于包内（`src/agent_bridge/dsh/dsh-plugins.txt`，随版本
发布；每行一个 spec，如 `@scope/name@0.3.23`，支持 `#` 注释；全部钉住精确
版本以保证同一版本各部署插件集一致，升级插件即改版本号随版本部署）。插件在 **web 进程启动之前**
以目标 Linux 用户身份逐个执行 `dsh plugin --profile web add <spec>` 安装完毕
（运行中的 DSH 不会热加载 profile 变更，先启动后安装会出现首访无插件的竞态），
成功条目记入 `<DSH_HOME>/agent-bridge-plugins.txt`；已就位的条目不会重装，
失败或超出总预算的条目不阻塞启动、会在下次 runtime 启动时自动重试，从名单
移除条目不会卸载已装插件。

原生依赖构建：安装前会初始化 profile 并显式声明**不构建**原生依赖
（`pnpm-workspace.yaml` 的 `allowBuilds: {node-pty: false}`，`BLOCKED_BUILDS` 随
版本维护）——pnpm 10 默认拦截依赖的 install/postinstall 脚本并留下待定告警，
而 node-pty 只随包附带 win32/darwin 预编译、Linux 必须本地 node-gyp 编译
（需要工具链与 Node headers）：默认跳过构建可保证插件名单整体装得上，依赖
node-pty 的能力（如 dsh-better-sidebar 的终端）由插件自身降级并给出提示。
内网若需要终端能力：把 node-pty 移出 `BLOCKED_BUILDS` 并预置 python3/make/g++
与匹配 Node 版本的 headers（`nodedir` 或 `~/.cache/node-gyp/<版本>/`）。

### DSH Workspace 与能力平面

「DSH 工作台」页面列出当前工作台状态（运行中/未运行、Linux 用户、当前能力平面、
空闲时长，可一键停止），选择能力平面后点「进入工作台」，会在**新标签页**打开
伪全屏工作台 `/workspace/live`；浏览器地址始终是 Agent Bridge，不暴露 DSH 端口。

反向代理 `/agent-workspace/**` 复用 dashboard 代理的流式转发骨架，支持 HTTP、
WebSocket 与 SSE 长连接；Host/Origin 指向目标，`Location` 重写回前缀。代理目标
只能来自当前登录业务用户已登记的 runtime，不接受 URL 指定端口；代理命中即刷新
空闲时间，未运行时自动按需启动。

DSH 前端以 `<base href="/">` 用根绝对路径请求资源、插件模块、`/api/**` 与实时
通道（`/api/remote.mux`），这些请求不在 `/agent-workspace` 前缀下：代理按 Referer
（HTTP）与同源 Origin（WebSocket）把它们认领给工作台，`/api/v1/**`、
`/agent-bridge/**` 等平台自身路径不受影响；上游 `Origin` 改写为目标 origin，
会话 Cookie 保持 `Path=/`，使前缀外的请求与 WS 握手同样携带会话。

DSH 的首次访问必须携带启动 token（``GET /?token=…`` 换取会话 Cookie，否则返回
"authentication required"）：代理在服务端完成这次换取——每次根导航都用捕获的
token 换取新鲜会话 Cookie（DSH 的 303 在服务端消化），浏览器直接拿到页面与
Cookie，token 不出现在地址栏。DSH 会话 Cookie 由 DSH 进程内密钥签名，runtime
重启后旧 Cookie 必然失效，因此代理不依据“浏览器已带 Cookie”跳过换取。

能力平面（Agent Bridge Profile）是 Workspace/会话级选择，**可以跳过**（此时不
注入任何 MCP）：授权时服务端校验该 Profile 对当前用户的可见性，签发绑定
(用户, Profile, 归属组) 的 24 小时短期 capability，并生成 DSH 的 loader patch
覆盖文件（`<DSH_HOME>/agent-bridge-mcp.patch.yml`，插入 `dsh-mcp-client`
streamable-http 实例并携带 Profile 与 capability 头），通过启动命令的
`{patch}` 占位符以 `--patch` 注入。DSH 作为 MCP client 携带 capability 请求
`/mcp`，服务端按既有 Profile 权限体系过滤工具并归属业务用户审计；切换（含退出）
能力平面会回收重启实例，重复进入同一平面只刷新 capability。

## 模型评估运行时

「系统管理 → 模型评估」只支持本地 Docker，不会向 Agent Bridge 主 Python 环境安装 OpenCompass。评估按五个能力维度组织：通用知识（C-Eval、MMLU-Pro）、数学（GSM8K）、指令遵循（IFEval）、代码（HumanEval、MBPP）和 Agent（SWE-bench Lite）。页面可设置“每个数据集最多题数”（默认 64、范围 1–1000），并选择固定前 N 条或带 seed 的随机抽样；所有勾选数据集按相同上限执行，SWE-bench 中对应最多任务数。评估详情提供五维雷达图：同维度的已选测试集按百分比分数等权平均，未选维度或未产生有效分数的已选测试集均按 0 分计。

部署机需要预先构建或导入两份镜像。OpenCompass、HumanEval 与 MBPP 数据在构建时打入镜像，运行时不下载、不挂载 OpenCompass cache：

```bash
docker build -t agent-bridge-opencompass-runner:latest docker/model-evaluation/opencompass
docker build -t agent-bridge-agent-worker:latest docker/model-evaluation/agent-worker

export AGENT_BRIDGE_EVAL_OPENCOMPASS_IMAGE=agent-bridge-opencompass-runner:latest
export AGENT_BRIDGE_EVAL_AGENT_WORKER_IMAGE=agent-bridge-agent-worker:latest
```

镜像构建前需按 [docker/model-evaluation/README.md](docker/model-evaluation/README.md) 放入固定版本的 OpenCompass、HumanEval 与 MBPP 数据。SWE-bench manifest 默认从 `AGENT_BRIDGE_ROOT/data/model-evaluation/swebench-manifest.json` 以只读方式挂入 `agent-worker`；也可通过 `AGENT_BRIDGE_EVAL_SWEBENCH_MANIFEST` 指定宿主机绝对路径。修改 manifest 无需重编 worker 镜像。各 task 对应的 testbed 镜像仍须提前导入本机 Docker。Docker daemon 或任一指定镜像缺失时，模型评估功能会直接显示为不可用，不创建任务。

普通题集在一次性 `opencompass-runner` 容器中执行；HumanEval/MBPP 的每个 case 会启动无网络、无 API Key 的独立代码沙箱；SWE-bench 按任务启动独立 testbed。HumanEval/MBPP 代码生成阶段单次模型补全请求超时 300 秒，单题生成失败只记录该题的 `generation_error` 并按失败计分，不会中止整批评估。SWE Agent 单次模型请求最多 6 分钟、最多 40 轮，单条命令最多 3 分钟、最终验收最多 15 分钟；当前不设置单题总时长上限。API Key 只作为容器运行时环境变量传递，不保存到 SQLite、运行请求文件或日志。

评估执行目录以 bind mount 挂入容器；Linux 会按宿主 uid 校验写权限，因此启动容器前会把挂载根目录放开为 1777（含 sticky 位），镜像内的非 root 用户才能创建 `output` 等产物目录。容器异常退出时，失败信息会附带对应日志（`runner.log` / `generation.log`）的尾部内容；完整日志保留在 `AGENT_BRIDGE_ROOT/run/model-evaluations/<run_id>/executions/<runner>/` 下。

若公共模型配置的 Base URL 指向宿主机的 `localhost`，容器默认会改用 `host.docker.internal`；Linux 部署可通过 `AGENT_BRIDGE_EVAL_DOCKER_HOST` 指向可从容器访问的宿主机地址或网关。没有可访问的 Docker 环境或指定镜像时，不存在 venv/CLI 后备，评估功能直接不可用。

## Profile 接入 Claude Code

先在管理后台或 API 创建 Profile，再执行：

```bash
uv run agent-bridge profile use safe-readonly \
  --scope project \
  --url http://127.0.0.1:8765/mcp
```

`profile use` 会写入或更新：

- 项目级 `.mcp.json`（`scope=project`）或用户级 `~/.mcp.json`；
- Claude Code hooks 配置；
- 项目 `CLAUDE.md` 或用户 `~/.claude/CLAUDE.md` 中由 Agent Bridge 管理的
  `<system-reminder>` 语义说明块。

写入的 `agent-bridge` MCP server 默认设置 300 秒工具调用超时（`timeout: 300000`，单位为毫秒），用于覆盖 Claude Code 远程 HTTP MCP 的短请求超时。AgentService 为受管 Agent 生成的临时 MCP 配置也使用同一设置。

`profile use` 还会安装一个 Claude Code `SessionEnd` Hook。每次会话结束时，它会对 Agent Bridge 实际管理的 MCP、Hook 和 `CLAUDE.md` 说明块计算配置 hash；只有生成结果变化时才更新，用户自己的 MCP server、Hook 和文档内容会保留。代码升级后无需手动修改版本号；首次接入某个 project 或 user scope 仍需执行一次 `profile use` 安装该 Hook。

执行 `profile unuse` 会列出当前项目和用户级已接入的 Profile，交互选择一个后卸载对应范围的 Agent Bridge MCP、托管 Hook 和说明块；用户自己的 MCP server、Hook 与文档内容会保留。非交互场景可使用 `--scope project|user --yes`。

它不会把完整 profile 正文或绝对文件路径复制进 CLAUDE.md。动态 profile 与
memory 上下文由服务端维护，并通过 `SessionStart` Hook 注入。

`profile use` 安装的全量检索探测 Hook 会在 `UserPromptSubmit` 同步执行：它在本轮等待至多 20 秒，将命中的资源提示作为当前轮 `additionalContext` 返回；没有命中或失败时不影响正常对话。

常用 Profile 命令：

```bash
uv run agent-bridge profile list
uv run agent-bridge profile show safe-readonly
uv run agent-bridge profile config --scope project
uv run agent-bridge profile sync safe-readonly --scope project
uv run agent-bridge profile unuse
uv run agent-bridge profile pins refresh safe-readonly
```

`profile use` 会自动安装 Claude Code 普通 `async` 全量检索探测 Hook。CLI 只转发 Claude
Code 的原始 Hook payload；服务端通过标准 `full-probe` Hook 使用系统管理的模型生成 0–8
个业务检索短句，并按 Profile/session 结合最近 3 轮历史去重（最多缓存 12 轮、30 天滑动
TTL），仅探测当前 Profile 的工作流产出物。模型未配置或调用失败时 Hook 保持
静默，并将原始 prompt 与完整 Hook 请求/响应写入通用审计日志。监控页仅对
`codegraph_explore`、`session-start` 和 `full-probe` 的限定 Markdown 字段提供预览，其余
日志仍通过 JSON 查看完整载荷。工作方式和独立 API 契约见
[Claude Code 全量检索探测 Hook](docs/integrations/retrieval-probe-hook/README.md)。

## 测试与质量检查

推荐从仓库根目录执行：

```bash
./scripts/test.sh fast -q
./scripts/test.sh full -q
# 需要真实外部服务/CLI/本地进程时再运行：
./scripts/test.sh all -q
```

- `fast`：不运行 e2e、真实 CLI、进程和外部知识后端测试；前端运行单元测试。
- `full`：运行完整的自包含后端测试；前端运行测试、类型检查和生产构建。
- `all`：清除默认 marker 排除，运行包括真实 CLI、进程和外部后端在内的全部测试。
- `integration`：运行需要真实 RagFlow/Weknora 的集成测试。

`full` 和 `all` 都会先运行 Ruff 基础静态检查。CI 在全新 checkout 中执行 `full`，随后构建 wheel、安装到隔离环境并验证管理后台静态文件已经打包。

前端单独开发：

```bash
cd frontend/capabilities
npm run dev
npm test
npm run typecheck
npm run build
npm run check
```

Vite 产物写入 `src/agent_bridge/static/capabilities/`。该目录不提交到 Git；发布 wheel 前必须先执行 `npm ci && npm run build`。管理后台使用 Vue Router 的 History 路由：部署入口为 `/agent-bridge/`，服务端会将其下的深链接刷新回退到同一前端入口；第一方 HTTP API 统一位于 `/api/v1/`，`/mcp` 与 `/health` 保持独立入口。`npm run dev` 同样可通过 `/agent-bridge/` 验证客户端路由。

CodeGraph 不提供 SQLite 文本索引降级。CLI 缺失、索引未建立或查询失败时，API 会明确返回后端不可用；安装 CLI 后需要重新同步受影响仓库。

## 数据与配置

默认数据根目录是 `/root/agent-bridge`，可通过 `AGENT_BRIDGE_ROOT` 覆盖：

```text
/root/agent-bridge/
├── config/
├── data/
├── logs/
├── repos/
└── run/
```

服务配置位于 `config/server.toml`，数据库和运行数据位于 `data/`。主业务库为 `data/agent-bridge.db`，高频工具调用与 Agent 运行审计独立保存到 `data/agent-bridge-logs.db`；升级时会安全复制历史 `wiki.db` 到新的主库文件名。日志默认写入 `logs/agent-bridge.log`。升级后的首次启动会为任务队列和日志聚合构建查询索引，数据量较大时该过程可能耗时数分钟，属一次性成本。

历史数据由统一的数据生命周期任务治理（系统配置页「数据生命周期」：详情保留天数 `retention_detail_days` 默认 20、历史保留天数 `retention_history_days` 默认 60、每日清理时间 `retention_cleanup_time` 默认 22:00，按部署机器本地时间执行）。每天在清理时间执行一次：详情窗口内的运行详情、工具调用请求/响应与 Agent 运行目录完整保留；超过详情窗口清理大字段只留轻量摘要（状态、耗时、错误、审计摘要）；超过历史窗口删除整行，工作流节点运行/运行产物关联与模型评测执行随外键级联，历史工作流产物（`is_current = 0` 且未被仍保留的 run 经 `workflow_run_artifacts` 复用引用的）按窗口清理而当前产物永久保留，`workflow_run_logs` 超过详情窗口直接删除，`sync_jobs`/`codegraph_sync_runs` 与过期导入预览按 60 天/到期清理。业务台账、知识库与文档、用户权限、Script/Skill/Workflow 定义、`workflow_tasks` 不做时间清理。运行中/待处理的活动任务（`status` 为 `running`/`pending` 的 agent run、工作流 run、模型评测与脚本运行）不做 TTL 删除，长跑任务的运行目录因此始终受保护。日常任务只分批 DELETE 加 `wal_checkpoint`、不 VACUUM；首次升级到该版本时按 `2026_09_data_retention_v1` marker 自动执行一次历史清理与各库 VACUUM（台账库仅在 freelist 偏高时），**该迁移在应用就绪前阻塞执行**（大库 VACUUM 期间服务暂不可用，避免与业务 SQLite 请求争锁），分阶段记录进度、中断后下次启动重跑未完成阶段。旧的「运行日志保留天数」独立清理机制已退役，显式设置过的保留期会在首次升级时迁移为对应历史保留天数。

Web 入口使用内部总账户系统签发的短期 JWT：浏览器访问
`/api/v1/auth/sso/callback?token=<JWT>&next=/` 后，后端校验 `server.toml` 的
`[identity]` 配置并写入 HttpOnly Cookie。前端不读取用户身份，也不自行添加鉴权 Header。
Agent Bridge CLI 和由 CLI 启动的 MCP/Agent 调用使用当前 Linux 用户名，通过内网可信的
`X-Agent-Bridge-User` Header 传给后端；该 Header 入口可用
`identity.allow_cli_header = false` 关闭。无 Cookie 且无受信 Header 的请求返回 401。

浏览器侧另提供全局管理员密码入口：无论当前是裸访问还是已经持有 SSO Cookie，都可以从页面左下角输入密码进入管理员模式。系统尚未设置密码时，第一次成功提交会直接完成初始化；密码只以 PBKDF2-SHA256 哈希保存到主业务库，浏览器获得有效期 12 小时的 HttpOnly 管理员 Cookie。管理员模式复用 `server.toml` 中的维护管理员身份，可跨组查看和维护全部数据，但不会改变资源已有的 `owner_group_key` 或 `visibility`。可通过 `GET /api/v1/auth/admin/status`、`POST/DELETE /api/v1/auth/admin/session` 管理提权会话；系统管理页通过 `PUT /api/v1/auth/admin/password` 修改密码，改密会立即使所有旧管理员 Cookie 失效。首次设置属于内网部署初始化动作，不额外引入验证码、互联网限流或页面路由鉴权。

系统在业务库分别维护用户目录、小组目录和“用户 ID → 单一小组”的归属关系。用户可以暂未分配小组；换组或取消归属只更新该关系，不删除用户。普通用户只能修改本小组资源；同组用户可以互相读取和修改。共享白名单资源可标记为 `shared`，此时其他小组只能读取和使用，不能修改、删除或变更共享状态。管理员可通过 `/api/v1/access/users`、`/api/v1/access/groups` 和 `/api/v1/access/memberships` 接口维护目录和归属，并保留跨组故障处理旁路。删除含成员的小组会被拒绝，需先处理成员归属。升级前已有映射会回填为用户目录记录；无法确认归属的数据保持组内范围并仅允许维护管理员修复，不会自动扩大为共享。`GET /api/v1/access/me` 返回当前用户显示名（`user_name`，来自 SSO claim 或 CLI Header，缺省回退用户 ID）与所属小组；`GET /api/v1/access/group-names` 提供登录即可读的 `group_key → 组中文名` 目录，前端各资源列表用它展示归属小组中文名。

知识库、MCP/OpenAPI 服务、代码仓库、业务台账和工作流产物接受 `visibility=group|shared`，默认 `group`。知识库除创建时选择范围外，归属组和管理员可随时通过 `PUT /api/v1/kbs/{slug}/visibility` 在组内与共享之间切换（沿用 defaults 编辑令牌做并发护栏）；工作流产物可在详情中逐项切换共享范围；定义、任务、运行过程和日志不会随产物共享。后端在列表、详情、编辑、删除、工具执行、查询、导出、事件流和 Dashboard 代理入口统一检查资源归属；能力 Profile、记忆块及其绑定保持组内可见，Profile 的 allow 列表与小组可见范围取交集，也可以引用其他组显式共享的资源。用户后来换组不会迁移既有资源，资源仍由创建时的归属组维护。

工作流手工运行要求调用者对定义有组内写权限；定时运行使用工作流自身的归属组，不使用维护管理员旁路。运行期间 Agent MCP 和脚本 helper 会携带服务端签发的短期 capability，并与 Profile、workflow 和 run ID 一起校验；单独伪造这些 Header 不能领取或修改其他组任务。运行产生的工具日志、Agent 记录和产物始终归父工作流 run 所属组。

工作流编辑器对归属组用户开放：列表、详情、脚本/技能/能力平面引用都按登录身份读取，节点后端下拉改由 `GET /api/v1/agent-runtime/backends` 提供，只返回默认后端与各后端的 slug、展示名、能力和思考力度，不含各后端的命令与模型配置。`GET /api/v1/agent-runtime/config` 仍只对维护管理员开放，仅供系统管理页使用；保存、运行、停止、删除等写入按工作流的 `owner_group_key` 判定组内写权限。

业务台账保存于 `data/agent-bridge-ledgers.db`。每个台账最多 100 个字段、200,000 行记录；服务启动后异步构建内存快照，管理读写和后续的受控查询均使用同一份完整快照。共享台账允许其他组查询和导出，但定义、记录和导入操作仍只能由归属组修改。

业务台账的 Excel 导入窗口可下载当前字段定义生成的空白 `.xlsx` 模板；模板只包含字段标识表头，不会携带台账中的已有数据。

新建或编辑业务台账定义时可使用“AI 设计”：Agent 先返回可视化字段草案，管理员确认“采纳并保存”后才会写入定义；该能力不会由 Agent 直接新增、修改或删除记录。

能力平面通过 `business_ledger` 资源规则显式授权业务台账。获授权的 Agent 使用顶级 MCP 工具 `query_business_ledger` 查询；所有字段默认支持精确匹配和排序，文本字段可额外开启字面包含检索，数字、日期与日期时间字段默认支持大于、小于、大于等于、小于等于和范围筛选。排序可按多个字段依次传入；台账、字段和查询方式自动注入 Profile，上下文外的台账不可发现也不可查询。

Agent 运行记录采用 SQLite 与运行目录混合存储：`data/agent-bridge-logs.db` 保存 `agent_runs` 摘要、固化的数据组、终态结果和规范化事件；工具调用日志、统计及 Agent 运行的列表、详情、事件、SSE、payload、子 Agent 和停止操作均按运行所属组过滤，维护管理员可跨组排障。每次运行的 `messages.jsonl`、实时 `events.jsonl` 和较大的工具输入/输出保存在 `run/agent-runs/<run-key>/` 下。运行中的时间轴通过 `GET /api/v1/agent-runs/{run_key}/events/stream` SSE 接收已落盘的新事件，以 `Last-Event-ID` 断线重放；`GET /api/v1/agent-runs/{run_key}/events` 仍用于初始快照和重同步。浏览器客户端使用 fetch 流沿用同源会话 Cookie，并通过 `Last-Event-ID` 请求头续传，不直接使用原生 `EventSource`。若经反向代理部署，必须关闭 SSE 响应缓冲并将读取超时设置为大于最长 Agent run。事件时间轴展示短 payload，较大的 payload 通过 `/api/v1/agent-runs/{run_key}/payload?ref=...` 按需读取；Agent 运行详情、工作流批量执行详情、任务展开日志和运行进度复用同一组输入提示词和执行结果卡片，每张卡片均可打开详情。Markdown 在详情中正常渲染，JSON 先格式化再展示，HTML、Python、JavaScript 使用语法高亮；工具输入、输出和模型详情同样提供“查看”入口。

工作流 Agent 的 JSON 输出 Schema 按 JSON Schema Draft 07 校验和传递给 Coding Agent。已有
Draft 2020-12 Schema 中的 `$defs` 及其本地 `$ref` 会自动转换为 Draft 07 的 `definitions`；
无法无损转换的 2020-12 专属关键字会在工作流校验阶段被拒绝。

工作流的 `agent` 与 `output` 节点可分别设置 `timeout_seconds`（默认 600 秒，范围 1–86400 秒）。它是运行控制参数，不改变节点处理语义；单独调整该值不会使增量运行失去既有节点结果复用资格。工作流名称、描述、节点显示名称和 Output 节点的展示标题同样不参与增量复用或重跑判定。调整这些展示/运行控制字段会产生新的版本号并在 diff 中可见（版本历史口径），但不会触发任务重跑（执行语义口径不变）。

工作流编辑页每次进入时都会重新读取最新定义。保存请求携带独立的 `edit_version` 乐观锁；如果定义已被其他页面更新，服务端返回 `409` 并保留当前页面草稿，避免旧标签页覆盖新内容。`edit_version` 只用于并发编辑保护，与增量运行使用的 `revision_no` 无关。

编辑工作流时可以选择任务刷新策略：默认“保存并安排增量刷新”（`task_refresh_policy=auto`）会将受影响的最新完成任务标记为 `stale`；“仅保存，暂不刷新”（`task_refresh_policy=defer`）只创建新 revision，不改变任务队列、不创建运行。此时任务仍显示为 `completed`，但任务列表会通过 `needs_refresh` 标记其结果来自旧执行语义；之后可以在任务页按任务或批量调用刷新操作，再安排增量运行。`content_hash` 仍按当前定义计算，不通过伪造 hash 规避版本一致性检查。

其他管理页采用统一的 `edit_token` 乐观并发协议。代码仓库、分类、知识后端、知识库默认检索配置、能力服务、Profile 配置、脚本和 Skill 在进入编辑时会读取最新详情；保存时传回 `expected_edit_token`。系统级配置页也会携带加载时取得的令牌。若另一个标签页已先保存，服务端返回 `409`，当前页面保留草稿并提示刷新，不会用历史数据覆盖新配置。令牌是服务端根据可编辑字段生成的不透明摘要，不包含或暴露 API Key、认证头等秘密。

文档知识库的“上传后立即同步”按后端目标分别设置，默认全部关闭。例如可让 Weknora 上传后立即同步，而 RAGFlow、PageIndex 只等待计划同步。关闭时上传仅完成入库并创建待同步任务，由系统配置的 `doc_sync_cron` 定时处理；管理后台只会对本批启用了立即同步的后端发起同步。HTTP 上传接口的 `later` 参数默认也为延后同步，调用方可显式传 `false` 覆盖。

当前部署模型是内部可信 VM，不提供互联网级认证。浏览器身份来自总账户系统换取的会话 Cookie；CLI、MCP 与 Agent 进程身份来自受信的 Linux 用户名 Header。部署方必须限制监听地址、网络访问和反向代理边界，并禁止外部请求直接进入 CLI Header 信任通道。

## 目录概览

```text
src/agent_bridge/
├── api/                       # FastAPI 路由与页面入口
├── app/                       # 应用装配与兼容门面
├── capability_hub/            # 能力来源、治理、MetaMCP
├── knowledge_management/      # 文档、代码与记忆知识
├── agent_runtime/             # Coding Agent 抽象与执行
├── automation/workflows/      # 结构化 DAG 工作流
├── system_config/             # 脚本、Skill、插件调度
├── server_runtime/            # uvicorn 服务进程管理
└── storage/                   # SQLite schema 与 repositories

frontend/capabilities/         # Vue 3 管理后台
examples/workflows/            # 当前格式的工作流导入示例
tests/                         # 后端测试
```

开发约定与架构不变量见 [AGENTS.md](AGENTS.md) 和 [CLAUDE.md](CLAUDE.md)。

## 工作流示例

- `examples/workflows/fine-report-analysis/workflow.json`
- `examples/workflows/hellogithub-summary/workflow.json`

示例均使用 `agent-bridge.workflow` / `format_version=1` 导入信封，并由测试校验结构化 DAG。

## 工作流增量复用

增量执行会复用配置、运行资源和历史产物均一致的已完成节点。条件分支的实际路径依赖节点输出，预览会将该分支及其下游标为“待条件结果”；运行时仅在条件命中后决定复用或重新执行，未命中的分支不会使汇合节点失效。

## 工作流首次使用导览

工作流列表及编辑器、文档知识、能力接入、工具调试、能力平面配置、记忆区块和脚本管理均使用 Driver.js 提供简洁的首次使用导览，并提供“新手指南”按钮供随时回看。导览脚本位于 `frontend/capabilities/src/lib/onboardingTours.ts`，每个导览以稳定的 `data-tour` 锚点、独立 `key` 与 `version` 定义；用户完成、跳过状态则独立保存在主库的 `onboarding_tour_statuses`，按当前请求身份、导览 key 与版本隔离。前端仅在页面主要内容加载后自动检查；动态、空数据或无权限而未渲染的锚点会安全跳过。前端通过 `GET/PUT /api/v1/onboarding/tours/{tour_key}` 读写状态；提升导览 `version` 即会为所有用户提供新版导览，无需删除旧记录。其他页面只需新增导览定义，并把 `TourReplayButton` 与 `useOnboardingTour` 接入其已完成渲染的页面。

## 工作流任务版本演进

`task_key` 是任务的唯一身份，`task_version` 是它的版本演进线。当同一 `task_key` 出现新的 `task_version` 时：

- 尚未运行的旧版本（`pending`/`stale`）以及无需继续重试的旧版本（`failed`/`abandoned`）被标记为 `superseded`，调度器永不领取它们；
- 正在运行的旧版本（`running`）让它跑完，不被取代；
- 已成功完成（`completed`）的旧版本保留为历史产物，其产物仍按 `task_key` 聚合、按版本归档。

跨版本禁止增量复用：新版本首次执行因无同 `task_version` 的基线而全量运行（不同版本的报表内容与解析结构不同，不能复用）。`workflow_set_task`（MCP 单发/批量）与 Excel 导入确认共用同一下发入口，取代行为一致。存量库中“同 `task_key` 多个 version 都在排队”的数据会在启动迁移中自动回填为 `superseded`，每个 `task_key` 仅保留最新版本参与执行。

## 工作流自动调度并发

系统管理页面可分别设置自动调度的全局并发数（默认 4）和单个工作流并发数（默认 2）。调度器按工作流轮转分配运行槽位，既不会超过全局上限，也不会让单个工作流占用超过自身上限的槽位。`workflow_max_runs` 仍表示每个调度窗口内的自动运行次数上限，与并发配置独立。

服务重启时会将上一进程遗留的工作流运行标记为失败，关闭未完成的节点并释放精确任务租约；启动调度器前会恢复当前窗口的自动运行计数，避免概览永久显示“执行中”或在同一窗口重复调度。页面上的批量任务队列仍是前端队列，重启页面或服务后不会续跑未提交的队列项。
