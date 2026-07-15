# WeKnora 多文件同步知识库恢复修复计划

> **执行说明：** 使用 `superpowers:subagent-driven-development`，由 subagent 在本 worktree 中实现；每完成本计划的实现任务后统一进行一次规格审查、质量审查和针对性测试。已知的全量测试收集问题按用户要求忽略。

**目标：** 修复 WeKnora 后端知识库丢失或目标记录缺少 `backend_kb_id` 时的同步恢复逻辑，确保同一轮多文件同步只创建/恢复一次后端知识库，后续文件复用新 ID，不再产生 N 个同名空知识库；并用真实 API 验证两个目录各同步两个 Markdown 文件。

**根因摘要：** `list_runnable_jobs()` 在同步开始时生成静态任务快照，并将缺失的 `backend_kb_id` 回退为本地 slug。`_run_job()` 遇到后端 KB 不存在时会创建并更新数据库，但同一轮同步仍继续使用旧快照，导致后续任务重复创建后端 KB。另有已存在但 `backend_kb_id IS NULL` 的 target 未被 `align_backends()` 修复。

## 任务 1：先写回归测试锁定“单轮只恢复一次”

**负责范围：** `tests/test_services.py`，必要时补充同目录下已有同步测试辅助代码。

- 构造一个本地知识库、一个 WeKnora target 和多个待同步文档；让 target 的后端 ID 缺失或让第一次远程访问表现为 KB 不存在。
- 用可记录调用的 backend adapter 断言：同一个 `(local_kb_id, backend_slug)` 在一轮同步中最多调用一次 `create_kb()`，所有文档上传使用同一个新返回 ID。
- 覆盖现有 target 行已存在但 `backend_kb_id` 为空的情形，确保同步前可以恢复/补齐目标 ID，而不是把本地 slug 当作远程 ID。
- 先运行该测试并确认在修复前失败，再实现后确认通过。

## 任务 2：修复同步恢复和 target 对齐

**负责范围：** `src/agent_bridge/app/service.py`，如确有必要才修改 `src/agent_bridge/storage/repositories/knowledge.py` 或 `src/agent_bridge/storage/sqlite.py`。

- 为一次 `sync()` 建立按 `(kb_id, backend_slug)` 复用的恢复状态，或在恢复后重新读取目标记录/任务状态；不能让旧的任务快照触发重复远程建库。
- `_run_job()` 在有新后端 ID 时必须使用新 ID 上传当前文档；后续任务也必须读取同一目标 ID。
- 对已有 target 但 `backend_kb_id` 为空的记录，在同步前执行幂等恢复并持久化真实远程 ID；修复后不得把本地 slug 作为远程 KB ID 发送给 WeKnora。
- 保持已有删除、更新、重试和其他后端行为不变；PageIndex 不因本修复被误改为支持目录或改变既有语义。
- 不改变用户已经确认的目录规则：本地根目录不作为远程虚拟目录，根下 A/B 目录同步为 A/B；WeKnora 继续保留对应文件夹层级。

## 任务 3：运行针对性验证并检查改动

**负责范围：** 当前 worktree 的测试与 diff 检查。

- 运行服务同步相关的定向 pytest（至少包含 `tests/test_services.py` 及现有 folder/sync/weknora backend 测试）。
- 运行 `git diff --check`，检查变更只涉及本计划范围；全量 pytest 的已知 `tests` 模块收集问题忽略，不把它当作本次回归。
- 完成一次规格审查和一次质量审查；若有反馈，由实现 subagent 修正后重新验证。

## 任务 4：真实环境验收并恢复干净状态

**负责范围：** Agent Bridge 本地 API、真实 WeKnora API 和清理脚本（不提交测试数据）。

- 确认清理前置条件：Agent Bridge 与 WeKnora 的知识库列表均为空，后端配置仍保留。
- 创建一个验收知识库，创建两个文件夹（例如 `A`、`B`），每个文件夹上传两个 Markdown 文件，并发起只针对 WeKnora 的同步。
- 验证同步成功数为 4、失败数为 0；验证本地每个目录各有 2 个文件；从本地 target 读取真实 `backend_kb_id`，通过 WeKnora API 验证只有一个远程 KB 且包含 4 个文件，文件路径保留 A/B 层级。
- 验收取证后删除该验收知识库及其文档/远程 KB，最后再次确认两端知识库均为 0，保留后端配置和代码改动。

## 完成条件

- 多文件同步的一轮恢复只创建一个 WeKnora 知识库，所有任务复用同一个 `backend_kb_id`。
- 已存在但 ID 为空的 target 可以被修复，远程调用不再使用本地 slug 作为伪 ID。
- 定向自动化测试通过，`git diff --check` 通过。
- 真实验收达到 1 个知识库、2 个文件夹、每个 2 个 Markdown 文件且后端同步成功；验收结束后本地和 WeKnora 均恢复为空。
