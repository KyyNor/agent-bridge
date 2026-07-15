# Coding Agent 对接方式参考：Skill / Hook / Plugin 层

> 本文整理自 `gsd-core`（open-gsd）的 EoS（Embeddable Orchestration System, ADR-1239）落地实现。
> gsd-core 同时对接了 18 个 coding agent（Claude Code / Codex / OpenCode / Cursor / Cline /
> Antigravity / Hermes / Kimi / Qwen / Kilo / Trae / Augment / CodeBuddy / Copilot / Windsurf /
> ZCode / Pi / VS Code），是目前开源里覆盖最广的案例。
>
> **本文目的**：我们 agent-bridge 当前对接 agent 的方式是「写 `.mcp.json` + Claude Code hooks」，
> 重点是 MCP 网关。如果未来要把对接能力下沉成可复用的「asset 投影层」（把同一份 skill/指令/
> hook 配置写到不同 agent 的目录里），这份梳理就是设计参考。
>
> **与现有文档的关系**：`multi-agent-adapter-research.md` 聚焦 **CodingAgent 执行层**
> （怎么跑各家 SDK / CLI）；本文聚焦 **配置/asset 投影层**（怎么把 skill/hook/plugin 装进各家）。
> 两者互补，对应 gsd-core 里"引擎执行"和"安装投影"两条边界。

---

## 0. 三个核心接口点（所有 agent 的集成面都会落到这三件事）

任何 coding agent 暴露给外部工具的集成面，归结起来就是三个点：

| 接口点 | 含义 | 典型载体 |
|--------|------|----------|
| **Skill / 指令** | 给 agent 注入"能力描述 + 工作流指引"——什么时候做什么、怎么做 | markdown 文件、TOML、规则目录 |
| **Hook（生命周期）** | 在 agent 执行的特定时机（工具调用前/后、会话开始/结束）插入外部逻辑 | settings.json 注册、独立 hooks.json、TOML `[[hooks]]`、插件事件总线 |
| **Command / Plugin** | 注册自定义斜杠命令 / 工具，或一个能跑代码的原生插件 | markdown 命令文件、JS/TS 插件、MCP server |

**关键观察**：每个 agent 在这三点上"从完整 → 退化 → 缺失"的程度差别极大。
gsd-core 的核心设计就是把这种差异**显式建模成可协商的能力轴**，而不是到处写 `if runtime === 'xxx'`。

### 两种集成哲学

- **声明式（declarative）**：只往 agent 目录写文件（markdown / TOML / JSON），agent 自己去读。
  没有进程内代码，能力有限但最简单。→ Codex / Gemini / Windsurf / Copilot / Augment / CodeBuddy / ZCode
- **命令式（imperative）**：写一个真正的插件（JS/TS/Python），在 agent 进程里跑代码，
  能订阅事件总线、程序化注册工具。→ Claude Code / OpenCode / Cursor / Hermes / Kimi / Kilo / Pi

---

## 1. 逐个 agent 的具体对接方式

下面把 18 个 agent 按"对接形态相似"分组。每组先讲共性，再标每个 agent 的差异。
字段值全部来自 gsd-core 的 `capabilities/<id>/capability.json`（运行时描述符）。

### Group 1 — Claude Code（金标准，完整 hook bus + skills）

**集成形态**：imperative，tier-1，最完整。
**这是 gsd-core 的基准**——"可移植事件地板（portable event floor）"就以 Claude 的事件命名。

| 项 | Claude Code 的做法 |
|----|-------------------|
| 配置根 | `~/.claude/`（global）+ `.claude/`（project），env `CLAUDE_CONFIG_DIR` |
| 配置格式 | `settings.json` / `settings.local.json` |
| Skill 落地 | global `~/.claude/skills/gsd-*`（flat），converter `convertClaudeCommandToClaudeSkill` |
| Command 落地 | project `.claude/commands/gsd-*`（flat，`slash-hyphen` → `/gsd:xxx`）|
| Agent 落地 | project `.claude/agents/gsd-*` |
| Hook 注册 | `hooksSurface: settings-json`，写入 `settings.json` 的 `hooks` 块 |
| Hook 事件 | `hookEvents: "claude"` 方言，约 30 个事件（`PreToolUse`/`PostToolUse`/`Stop`/`SessionStart`/`SessionEnd`/`SubagentStop`/`PreCompact`/`UserPromptSubmit`…），extendedEvents `SubagentStop/Stop/PreCompact/FileChanged` |
| 派发 | 前台无限嵌套 / 后台 maxDepth 5；后台子 agent **不能再派发**（#853 规则判别点）|

**对我们的启发**：我们 `agb profile use` 已经在用这套（写 `.claude/settings.local.json` 的 hooks + `.mcp.json`）。
Claude Code 是我们目前唯一深度对接的 agent，这层逻辑可直接复用。

---

### Group 2 — OpenCode / Kilo（imperative，JS 插件 + 事件总线，无传统 hook）

**集成形态**：写一个真实的 JS 插件文件，订阅 plugin event bus。
**最关键的洞察**：OpenCode **不支持注册生命周期 hook**（`hooksSurface: "none"`），
它用**插件扩展事件**替代，这是和 Claude 截然不同的架构。

| 项 | OpenCode | Kilo（OpenCode fork，几乎一致） |
|----|----------|------|
| 配置根 | `~/.config/opencode/`（XDG），env `OPENCODE_CONFIG_DIR` | XDG `kilo`，skills 单独在 `~/.kilo/skills` |
| 配置格式 | `settings.json` | `settings.json` |
| Skill 落地 | `skills/gsd-*`（**recursive:true**，可嵌套），converter `convertClaudeCommandToOpencodeSkill` | `skills/gsd-*`（flat，recursive:true），`convertClaudeCommandToKiloSkill` |
| Command 落地 | **`command/`（单数！）** `gsd-*` | `command/`（单数），命令 converter 为 null（原样拷贝）|
| **Hook 注册** | **`hooksSurface: "none"`** —— 无传统 hook | 同样 `none` |
| **事件机制** | `extensionEvents: "opencode"` —— ~25 个插件事件（`tool.execute.before/after`、`session.created/compacted/deleted`、`experimental.session.compacting`）| `extensionEvents: "kilo"`（复用 OpenCode 事件集）|
| **原生插件** | **`nativePlugin: {dir:"plugins", file:"gsd-core.js", source:".opencode/plugins/gsd-core.js"}`** | 同（`.kilo/plugins/gsd-core.js`，与 OpenCode 字节相同）|
| 运行时 | **bun**（插件用 Bun 跑）| bun |
| 模型模式 | **active**（可程序化请求模型）| active |

#### OpenCode 插件的实际设计（gsd-core `.opencode/plugins/gsd-core.js`）

这是本文最有参考价值的一段源码，核心策略叫 **"SUBPROCESS REUSE"（子进程复用）**：

> **不在插件里重新实现 hook 逻辑**。插件是一个薄适配器：
> 1. 把 OpenCode 插件事件翻译成 Claude Code hook payload（JSON on stdin）
> 2. spawn `node <HOOKS_DIR>/<hook>.js` 子进程，把 payload 喂给它
> 3. 把 hook 输出翻译回 OpenCode 语义（`block`→throw，`advisory`→metadata）

关键映射表（工具名归一化）：
```
OpenCode          →  Claude
read/write/edit   →  Read/Write/Edit
apply_patch/multi_edit → MultiEdit
bash              →  Bash
webfetch          →  WebFetch
web_search        →  WebSearch
task/subagent     →  Task
```
字段名也要映射：OpenCode `filePath`/`path` → Claude `file_path`。

**两种分发形态，同一个插件文件**（靠 `REPO_ROOT` 探测区分）：
- **文件拷贝**（主路径）：install.js 把 `gsd-core.js` 拷到 `<opencodeConfigDir>/plugins/`，命令/skill 已被原生文件拷贝注册，插件自己跳过注册避免双重
- **npm 包 / git-spec**：从包树加载，插件自己注册命令

#### Load-bearing gap（重要架构陷阱）

OpenCode 的 event bus 触发于 **session / tool / file / permission**，**从不触发于 workflow phase**。
所以如果你的逻辑是"在 plan 阶段后做 X"、"在 verify 后做 Y"——**OpenCode 的 bus 上没有对应事件**。
必须让引擎自己拥有 phase sequencing，把 OpenCode bus 当成"子集事件表面"。

**对我们的启发**：我们的 memory hooks（`SessionStart`/`PostToolUse`/`Stop`）目前绑死 Claude 方言。
如果要扩到 OpenCode，得写一个类似的 JS 插件适配器，不能直接套 Claude 的 settings.json hook。
好消息是我们的 memory action（`session-start`/`observation`/`summarize`）和 OpenCode 事件能对上
（`session.created`≈SessionStart、`tool.execute.after`≈PostToolUse、`session.idle`≈Stop）。

---

### Group 3 — Codex（declarative，TOML 配置 + 共享 skill 根）

**集成形态**：declarative，tier-1，但能力比 Claude 弱。
**曾经被认为是 `prose-only`**（只能写 AGENTS.md 纯文本），后来发现新版 Codex 文档化了 slash-commands，
gsd-core 改判为 `slash-file`。

| 项 | Codex 的做法 |
|----|-------------|
| 配置根 | `~/.codex/`，env `CODEX_HOME` |
| 配置格式 | **`config.toml`**（不是 JSON！）|
| Skill 落地 | **`$HOME/.agents/skills/`**（Codex core-skills 的用户级根，**不是** `~/.codex/skills`——后者已废弃，install/uninstall 会清理）。converter `convertClaudeCommandToCodexSkill` |
| Agent 落地 | **TOML agent 文件**（`[agents.gsd-*]` 角色子表，`agentTomlFiles: true`），frontmatter 方言 `codex` |
| Command 风格 | `shell-var`（不是 slash-hyphen）|
| Hook 注册 | **`hooksSurface: "codex-hooks-json"`** —— 独立的 `hooks.json` 格式（和 Claude settings-json 不同）|
| Hook 事件 | 10 个：`PreToolUse`/`PermissionRequest`/`PostToolUse`/`PreCompact`/`PostCompact`/`SessionStart`/`UserPromptSubmit`/`SubagentStart`/`SubagentStop`/`Stop`。extendedEvents `SubagentStop/Stop/PreCompact`。全路由到 `gsd-context-monitor.js` |
| 派发 | **`maxDepth: 1`（硬限制）**，GSD 显式把 `[agents] max_depth = 1` 写进 config.toml。即使 nested/background 配置都开，实际 wave 派发被**强制降级为单层内联** |

**对我们的启发**：
1. Codex 的配置是 TOML 不是 JSON，profile 注入逻辑要换序列化方式
2. `maxDepth: 1` 意味着任何多 agent 编排（我们的 workflow 里 agent 派子 agent）在 Codex 上会退化——这点和我们 `multi-agent-adapter-research.md` 里"Claude 独有 Task* 子代理生命周期，其他 agent 多数没内建子代理"的判断一致
3. skill 根在 `~/.agents/skills` 而非 `~/.codex/skills`——这是个反直觉的坑

---

### Group 4 — Cursor（imperative，自己的 hooks.json + 规则目录）

**集成形态**：imperative，tier-2，有薄适配器。

| 项 | Cursor 的做法 |
|----|--------------|
| 配置根 | `~/.cursor/` + project `.cursor/` |
| Skill | `skills/gsd-*`（flat，recursive:true），`convertClaudeCommandToCursorSkill` |
| Command | `commands/gsd-*`，`convertClaudeCommandToCursorCommand` |
| Agent | `agents/gsd-*`，`convertClaudeAgentToCursorAgent` |
| Hook 注册 | **`hooksSurface: "cursor-hooks-json"`** —— Cursor 自己的 `hooks.json`（和 Codex 的 hooks.json 又不同）|
| Hook 事件 | claude 方言；managedHookEvents `[sessionStart, postToolUse, preToolUse, stop, subagentStart, subagentStop]` |
| 派发 | nested:true，**maxDepth: 2**，background:true，**backgroundDispatch:true**（Cursor 2.5+ 子 agent 能再派子 agent）|
| 特点 | frontmatter 方言 `cursor`；`reportCommandsDir: true` |

**注意**：Cursor 的 hook 协议和 Claude 不同，要按 Cursor 的字段名写。

---

### Group 5 — Antigravity（Google，gemini CLI 的继任者）

**集成形态**：declarative，**唯一的 tier-1 declarative host**。

| 项 | Antigravity 的做法 |
|----|-------------------|
| 配置根 | `~/.gemini/antigravity/`（parent `.gemini`），env `ANTIGRAVITY_CONFIG_DIR`，localConfigDir `.agents` |
| 配置格式 | `settings.json` |
| 项目指令文件 | **`GEMINI.md`**（不是 AGENTS.md / CLAUDE.md）|
| Skill | `skills/gsd-*`（flat），`convertClaudeCommandToAntigravitySkill` |
| Agent | `agents/gsd-*`，`convertClaudeAgentToAntigravityAgent` |
| Hook 注册 | `hooksSurface: "settings-json"` |
| **Hook 事件** | **`hookEvents: "gemini"`** —— **唯一用 gemini 方言的 host**（其他都 claude 方言）|
| 权限 | **permissionWriter: "antigravity"**（写 `{"permissions":{"allow":[...]}}`）|
| 运行时 | **go** |
| 派发 | 大量 `undocumented`（官方文档没写），fail-closed 降级 |

**注意**：Gemini CLI 已 sunset，被 Antigravity 接替。`hookEvents: "gemini"` 的事件名和 claude 方言不同。

---

### Group 6 — Hermes（imperative Python 宿主，programmatic 命令）

| 项 | Hermes 的做法 |
|----|--------------|
| 运行时 | **python**（不是 node/bun）|
| 配置根 | `~/.hermes/`，env `HERMES_HOME` |
| Skill | **`skills/gsd/`**（分类桶，唯一用子目录的）|
| 事件 | `extensionEvents: "hermes"`（13 个插件事件，不用 claude 方言）|
| 命令表面 | **`slash-programmatic`**（唯一非 markdown 的命令表面）|
| 派发 | nested:true 但 maxDepth:**1**，**subagentToolkit: read-only**（子 agent 只读！）|
| 模型 | **active** |
| branding | `CLAUDE.md→HERMES.md`、"Claude Code"→"Hermes Agent" |

---

### Group 7 — Kimi（imperative Python，原生 TOML hooks）

| 项 | Kimi 的做法 |
|----|------------|
| 运行时 | **python** |
| 配置根 | 探测 `~/.config/agents` 或 `~/.agents`，但 localConfigDir 是 **`.kimi-code`**（故意分裂）|
| Skill | **global-only**（local 为空），flat，`convertClaudeCommandToKimiSkill` |
| Agent | 自定义 kind **`kimi-agents`**，prefix **`gsd`（无连字符！）**，converter null |
| Hook 注册 | **`hooksSurface: "kimi-hooks-toml"`** —— 原生 `config.toml` 的 `[[hooks]]`，GSD 拥有的块用 `# GSD Hooks BEGIN/END` 包裹 |
| 派发 | maxDepth:**1**，但 **backgroundDispatch:true**（#2095 升级，Agent 工具支持 call-time `run_in_background`）|

**坑**：agent 前缀是 `gsd` 不是 `gsd-`，hook 在 `~/.kimi/` 而技能在 `~/.agents`——目录分裂。

---

### Group 8 — Qwen / CodeBuddy（settings.json + claude 方言，简化对接）

两者形态接近，都是 settings.json + claude hook 方言。

| 项 | Qwen | CodeBuddy |
|----|------|-----------|
| 配置根 | `~/.qwen/` | `~/.codebuddy/` |
| Skill converter | `convertClaudeCommandToClaudeSkill`（复用 Claude 的）| `convertClaudeCommandToCodebuddySkill` |
| Agent converter | `convertClaudeAgentToQwenAgent` | `convertClaudeAgentToCodebuddyAgent` |
| 派发 maxDepth | **1，nested:false** | **1，nested:false** |
| branding | `CLAUDE.md→QWEN.md` | — |
| extendedEvents | `SubagentStop/Stop/PreCompact/SubagentStart` | 同 |

---

### Group 9 — Cline（规则驱动，无 hook 事件）

| 项 | Cline 的做法 |
|----|-------------|
| 配置根 | `~/.cline/`，localConfigDir `.cline` |
| 配置格式 | **`markdown-dir`**（不是 JSON！）|
| Skill | **global-only**（local 空），nested，`convertClaudeCommandToClineSkill` |
| Hook 注册 | **`hooksSurface: "cline-rules"`** —— `.clinerules` 约定，frontmatter 方言 `cline` |
| **Hook 事件** | **无！**（不发射任何 hook 事件）—— hook 强制性退化成规则文本 |
| 派发 | nested:false，maxDepth:**1**，**subagentToolkit: read-only** |
| 特点 | `localCommandsViaRules: true`（命令也通过规则注入）|

**教训**：Cline 是 hook parity 最差的——只能 instruction-backed，不能运行时强制。这和 ECC 合规矩阵对 Cline 的评级一致。

---

### Group 10 — Copilot / Windsurf / Trae / ZCode / Augment（其余 declarative / 混合）

| Agent | 配置根 | 特点 |
|-------|--------|------|
| **Copilot** | `~/.copilot/`，**localConfigDir `.github`** | `copilot-instructions`（驱动 repo AGENTS.md + copilot-instructions.md）；agent 文件扩展名 **`.agent.md`**；maxDepth:1 nested:false |
| **Windsurf** | `~/.codeium/windsurf/` | `windsurf-hooks-json`（Cascade 原生 `.windsurf/hooks.json`，**用 exit code 2 阻断**——和 Cursor 的 stdout-JSON 协议不同）；local commands 装到 **`workflows/`**（不是 commands）；大量 dispatch `undocumented` |
| **Trae** | `~/.trae/` | `hooksSurface: none`，**hookBus: engine**（VSCode/Electron fork，继承扩展宿主生命周期）；`soloStageMetadata: "workflow"`（发 `stage: workflow` frontmatter 让 Trae SOLO Agent 自动调用 skill）|
| **ZCode** | `~/.zcode/` | `hooksSurface: none`（hook/transport 文档化但**未实现**，blocked on ZCode 发布格式）；运行时 **electron**；commands/agents converter 为 null（原样拷贝）；**会从 `~/.claude` 导入 skill/MCP，装两份会重复** |
| **Augment** | `~/.augment/` | `mcpCompanion: settings-json`（MCP 放 settings.json 的 mcpServers）；commands converter null（原样）；skills nested；dispatch 大量 undocumented |

---

### Group 11 — Pi / VS Code（特殊：纯扩展，无文件投影）

| 项 | Pi | VS Code |
|----|-----|---------|
| 配置根 | `~/.pi/agent` | 无（Marketplace/VSIX 扩展，`localConfigDir: null`）|
| 安装 | **`pluginOnlyInstall: true`**（只装插件，跳过通用命令/agent）| **`installSurface: none`**（从不被 install.js 安装，在 `NON_INSTALLABLE_RUNTIMES`）|
| 原生插件 | `nativePlugin: {dir:"extensions", file:"gsd.cjs", source:"pi/gsd.cjs"}` | 扩展即宿主 |
| 命令表面 | `slash-programmatic` | **`palette`**（`vscode.commands.registerCommand` + Chat participant）|
| 模型 | active | active（`vscode.lm`）|
| Hook | extensionEvents: pi，但 nested:false maxDepth:0 background:false | **hookBus: engine**（扩展宿主拥有生命周期）|
| 状态 IO | **`session-log-append`**（追加式会话日志，不是完整 FS）| **`sandboxed-storage`**（globalState/workspaceState，不是 FS）|
| 运行时 | bun | **`sandboxed-web`**（webworker，无 Node core）|

**这两类最特殊**：不是往目录写文件，而是扩展本身即集成。VS Code 是 ADR-1239 里"IDE profile"的代表，最考验接口设计。

---

## 2. Converter 矩阵（同一份 skill 怎么转成各家的）

gsd-core 的做法是：**skill 源文件用 Claude 格式写一份**，每个 agent 配一个 converter 转过去。
这是"单一真相源（single source of truth）"原则——改一次，全 agent 生效。

### Skill converter

| Converter | 用于哪些 agent |
|-----------|---------------|
| `convertClaudeCommandToClaudeSkill` | Claude, **Qwen, Hermes, ZCode**（这几个直接复用 Claude 格式）|
| `convertClaudeCommandToCursorSkill` | Cursor |
| `convertClaudeCommandToClineSkill` | Cline |
| `convertClaudeCommandToKimiSkill` | Kimi |
| `convertClaudeCommandToKiloSkill` | Kilo |
| `convertClaudeCommandToTraeSkill` | Trae |
| `convertClaudeCommandToAntigravitySkill` | Antigravity |
| `convertClaudeCommandToAugmentSkill` | Augment |
| `convertClaudeCommandToCodebuddySkill` | CodeBuddy |
| `convertClaudeCommandToCopilotSkill` | Copilot |
| `convertClaudeCommandToCodexSkill` | Codex |
| `convertClaudeCommandToOpencodeSkill` | OpenCode |
| `convertClaudeCommandToWindsurfWorkflow` | Windsurf（commands → workflows，唯一改名）|
| `null`（原样拷贝）| Augment(commands), Kilo(commands), ZCode(commands+agents), Kimi(kimi-agents) |

### Agent converter
`convertClaudeAgentTo{Cursor,Trae,Antigravity,Augment,Codebuddy,Copilot,Qwen,Windsurf}Agent`。
Cline/Hermes/Kimi/Kilo/ZCode/Pi/VSCode **没有 agents kind**或用 null。

**启发**：converter 是个很好的抽象——把"格式差异"隔离在一个函数里，上游只管 Claude 格式。
如果我们要做 asset 投影层，应该照搬这个思路。

---

## 3. Hook 表面（hooksSurface）全景 —— 差异最大的维度

| hooksSurface | agent | 协议特点 |
|--------------|-------|---------|
| `settings-json` | Claude, Antigravity, Hermes, Qwen, CodeBuddy, Augment | 写 settings.json 的 hooks 块 |
| `codex-hooks-json` | Codex | 独立 hooks.json，claude 方言 |
| `cursor-hooks-json` | Cursor | 独立 hooks.json，stdout-JSON 协议 |
| `windsurf-hooks-json` | Windsurf | `.windsurf/hooks.json`，**exit code 2 阻断**（非 stdout-JSON）|
| `kimi-hooks-toml` | Kimi | `config.toml` 的 `[[hooks]]`，BEGIN/END 包裹 |
| `cline-rules` | Cline | `.clinerules`，**无运行时事件**（指令文本）|
| `copilot-inline` | Copilot | inline，无 hook 事件 |
| `none` | OpenCode, Kilo, Trae, ZCode, Pi, VS Code | 无传统 hook（用 extensionEvents 替代或完全没有）|

### Hook 事件方言（hookEvents / extensionEvents）

- **`claude` 方言**（最多）：Claude, Codex, Cursor, Qwen, Codebuddy, Kimi
- **`gemini` 方言**（唯一）：Antigravity
- **`hermes` extensionEvents**（13 事件）：Hermes
- **`opencode` / `kilo` extensionEvents**（~25 事件）：OpenCode, Kilo
- **`pi` extensionEvents**：Pi
- **无事件**：Cline, Copilot, Trae, ZCode, VS Code, Windsurf

**可移植事件地板（所有支持 hook 的 agent 共享的最小集）**：
`SessionStart` / `PreToolUse` / `PostToolUse` / `Stop` / `SessionEnd`。
我们的 memory hooks 已经命中了其中四个（SessionStart/PostToolUse/PreToolUse/Stop/SessionEnd/UserPromptSubmit）——这是个好基础。

---

## 4. 派发能力矩阵（多 agent 编排能不能用）

这直接影响"workflow 里 agent 派子 agent"的可行性，和我们的 `multi-agent-adapter-research.md` 直接相关。

| Agent | nested | maxDepth | background | backgroundDispatch | subagentToolkit | 编排能力 |
|-------|--------|----------|-----------|--------------------|-----------------|---------|
| **Claude** | ✓ | 5(后台)/∞(前台) | ✓ | ✗ | full | **最强** |
| **Cursor** | ✓ | 2 | ✓ | ✓ | full | 强 |
| **OpenCode** | 未文档化 | 未文档化 | ✓ | ✓ | full | 强（v1.17+ 默认后台子agent）|
| **Kilo** | ✓ | -1(无限) | ✓ | ✗ | 未文档化 | 强 |
| **Codex** | ✓ | **1** | ✓ | ✓ | full | **弱**（maxDepth卡死，强制flatten）|
| **Hermes** | ✓ | 1 | ✓ | ✗ | **read-only** | 弱（子agent只读）|
| **Kimi** | ✗ | 1 | ✓ | ✓ | 未文档化 | 中 |
| **Qwen** | ✗ | 1 | ✓ | ✗ | full | 中 |
| **Cline** | ✗ | 1 | ✓ | ✗ | **read-only** | 弱（子agent只读）|
| **Copilot** | ✗ | 1 | ✓ | ✗ | full | 中 |

**gsd-core 的降级规则（`shouldFlattenDispatch` / `degradationFor`）**：
- maxDepth < 所需 → 把 wave 并行执行**压扁成单层内联顺序**
- Codex 因 maxDepth=1，即使配置全开也被 flatten
- 这个判别点是 `backgroundDispatch`（#853 规则）：后台 agent 能否再派发

**对我们的启发**：我们的 workflow 当前只有 Claude 一个 backend，子 agent 靠 Claude 的 Task。
扩到其他 agent 时，**Codex/Hermes/Cline 几乎不能用多 agent 编排**——要么单 agent 跑，要么 flatten。
这印证了我们研究文档里"Claude 独有 Task* 子代理生命周期，其他 agent 多数没内建子代理"的判断。

---

## 5. 对 agent-bridge 的设计启发

### 现状对照

我们目前对接 agent 的方式（`cli/profile.py`）：
- **只支持 Claude Code**：写 `.claude/settings.local.json`（hooks）+ `.mcp.json` + CLAUDE.md pointer
- **Memory hooks** 绑定 7 个 Claude 事件（Setup/SessionStart/UserPromptSubmit/PostToolUse/PreToolUse/Stop/SessionEnd）
- **核心是 MCP 网关**：agent 通过 MetaMCP 的 `search`/`execute` 两个工具访问能力，不靠 asset 投影

我们的 `multi-agent-adapter-research.md` 已经规划了 **CodingAgent 执行层抽象**（怎么跑各家 SDK）。
本文补充的是**另一个正交维度**：如果要把"对接配置"也下沉成可复用层，应该怎么做。

### 如果要做 asset 投影层，gsd-core 的关键教训

| 教训 | gsd-core 的做法 | 我们可借鉴的 |
|------|----------------|-------------|
| **单一真相源** | skill 用 Claude 格式写一份，converter 转各家 | 我们的 profile/memory 指令若要多 agent 复用，用中立格式 + converter |
| **能力轴显式建模** | `capability.json` 把 8 个协商轴声明出来，运行时协商后决定能用什么 | 我们的 CodingAgent Protocol 已规划 capability flags——和这个思路一致，是正确的 |
| **fail-closed 降级** | 未文档化的轴用 `undocumented` 哨兵，从不信任、降级到最保守值 | 扩 agent 时遇到能力未知，宁可降级不要假设 |
| **hook 表面抽象** | 把 8 种 hooksSurface + 4 种事件方言抽象成统一注册层 | 我们的 hooks 现在硬编码 Claude 路径，扩 agent 时需要 hooksSurface 抽象 |
| **converter 隔离格式差异** | 每个 agent 一个 converter 函数，上游无感 | 同上，格式差异不要泄漏到业务层 |
| **目录/前缀差异是坑** | Kimi 的 `gsd` 无连字符、Codex skill 在 `~/.agents`、OpenCode `command` 单数、Copilot localConfigDir 是 `.github` | 这些细节必须查官方文档，不能猜（gsd-core 每个 ax 值都带文档引用）|
| **imperative host 写真插件** | OpenCode/Kilo/Pi 有 nativePlugin，写 JS/TS 插件订阅事件总线 | 我们要扩 OpenCode，得写一个类似 `gsd-core.js` 的薄适配器插件，用 subprocess reuse 策略复用现有 hook 脚本 |

### 具体到我们的下一步

1. **短期（保持现状合理）**：我们当前只对接 Claude Code 是对的——它是 tier-1 + 最完整 hook + 我们 workflow 依赖的子 agent 编排只有它支持得好。先不急着扩。

2. **中期（执行层抽象优先）**：按 `multi-agent-adapter-research.md` 的规划，先做 `CodingAgent` Protocol（执行层），这比 asset 投影层更紧迫——因为 workflow/understand-anything 都依赖 `AgentService.run()`。

3. **长期（若需要 asset 投影）**：如果未来要"把 profile 指令 / memory 配置装进多个 agent 的目录"，再参考 gsd-core 的：
   - 每个 agent 一个 `capability.json` 风格的描述符
   - 一个 converter 注册表（中立格式 → 各家格式）
   - 一个 hooksSurface 抽象（统一注册到 settings.json / hooks.json / TOML / 规则文件 / 插件事件）
   - 协商 + fail-closed 降级机制

4. **ACP 路线的权衡**（来自我们已有研究）：gsd-core 走的是"per-agent adapter"路线（18 个描述符），工作量大但覆盖广。我们的研究文档也提过 AionUi 的"ACP 协议优先"路线——**如果目标 agent 集支持 ACP，能省 80% 胶水**。建议扩 agent 前先验证 ACP 覆盖度。

### 一个直接可复用的技术细节

gsd-core OpenCode 插件的 **subprocess reuse 模式**，正好能解决我们扩 OpenCode 的问题：
我们的 memory hook 逻辑（`memory/hooks.py`）现在是 Claude 专属。如果要扩 OpenCode，
不用重写——写一个薄 JS 插件，把 OpenCode 事件翻译成我们 hook CLI 的调用即可
（我们的 hook 本来就是 `agent-bridge memory hook claude-code <action>` 这种 CLI 形式，天然适合被 spawn）。

---

## 附录 A：各 agent 配置根速查表

| Agent | 配置根 | 配置格式 | 命令目录 | Skill 目录 |
|-------|--------|---------|---------|-----------|
| Claude Code | `~/.claude/` | settings.json | `commands/` | `skills/` |
| Codex | `~/.codex/` | **config.toml** | — | **`~/.agents/skills/`** |
| OpenCode | `~/.config/opencode/` | settings.json | **`command/`**(单数) | `skills/` |
| Kilo | XDG `kilo` + `~/.kilo/` | settings.json | `command/` | `~/.kilo/skills` |
| Cursor | `~/.cursor/` | (none) | `commands/` | `skills/` |
| Antigravity | `~/.gemini/antigravity/` | settings.json | — | `skills/` |
| Hermes | `~/.hermes/` | settings.json | — | **`skills/gsd/`** |
| Kimi | `~/.agents` / `~/.kimi/` | — | — | `skills/` (global only) |
| Qwen | `~/.qwen/` | settings.json | — | `skills/` |
| CodeBuddy | `~/.codebuddy/` | settings.json | `commands/` | `skills/` |
| Cline | `~/.cline/` | **markdown-dir** | (via rules) | `skills/` (global only) |
| Copilot | `~/.copilot/`, local `.github/` | markdown | — | `skills/` |
| Windsurf | `~/.codeium/windsurf/` | — | **`workflows/`** | `agents/`(global) |
| Trae | `~/.trae/` | — | — | `skills/` |
| ZCode | `~/.zcode/` | — | `commands/` | `skills/` |
| Augment | `~/.augment/` | settings.json | `commands/` | `skills/` |
| Pi | `~/.pi/agent` | — | (plugin only) | — |
| VS Code | (extension) | — | (palette) | — |

## 附录 B：参考来源

- gsd-core `docs/adr/1239-gsd-embeddable-orchestration-engine.md`（EoS 架构决策）
- gsd-core `docs/reference/host-integration-capability-matrix.md`（每个 agent 每个轴的文档引用 + evidence quote）
- gsd-core `capabilities/<id>/capability.json`（18 个运行时描述符）
- gsd-core `.opencode/plugins/gsd-core.js`（OpenCode 插件实现，subprocess reuse 模式）
- ECC `docs/architecture/cross-harness.md` + `harness-adapter-compliance.md`（合规矩阵）
- 本仓库 `docs/multi-agent-adapter-research.md`（我们的 CodingAgent 执行层抽象规划）
