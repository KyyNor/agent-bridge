# Remove SessionEnd Hook Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `profile use` 停止安装 Agent Bridge `SessionEnd` Hook，并在再次执行时清理旧托管项，同时保留用户 Hook和服务端 `session-end` 兼容实现。

**Architecture:** 仅调整 `CLAUDE_MEM_COMPATIBLE_HOOKS` 的安装集合，复用现有 marker 清理流程完成旧配置迁移。服务端 action、API 和领域逻辑不改动。

**Tech Stack:** Python 3.11、Typer、pytest。

## Global Constraints

- 新配置不安装 Agent Bridge 管理的 `SessionEnd` Hook。
- 再次执行 `profile use` 必须删除旧版 Agent Bridge `SessionEnd` Hook。
- 用户自行配置的 `SessionEnd` Hook必须保留。
- `session-end` action、HTTP API和服务端实现暂时保留。

---

### Task 1: 调整 Profile Hook 安装集合

**Files:**

- Modify: `tests/test_cli.py`
- Modify: `src/agent_bridge/cli/profile.py`
- Modify: `docs/agent-integration-patterns.md`

**Interfaces:**

- Consumes: `_strip_agent_bridge_hooks(settings)` 依据
  `--agent-bridge-hook-id agent-bridge-memory` 清理旧托管 Hook。
- Produces: `_install_profile_hooks(...)` 返回不含新 Agent Bridge
  `SessionEnd` 项、但保留用户 `SessionEnd` 项的 settings。

- [ ] **Step 1: 写新安装与旧配置迁移的失败测试**

将新配置断言改为：

```python
assert "SessionEnd" not in hooks
```

新增迁移测试，初始 settings 同时包含旧托管 Hook和用户 Hook：

```python
old_managed_hook = {
    "type": "command",
    "command": (
        "agent-bridge memory hook claude-code session-end "
        "--agent-bridge-hook-id agent-bridge-memory"
    ),
}
user_hook = {"type": "command", "command": "echo user-session-end"}
```

执行 `profile use` 后断言：

```python
assert settings["hooks"]["SessionEnd"] == [{"hooks": [user_hook]}]
```

- [ ] **Step 2: 运行测试并确认失败原因**

Run:

```bash
uv run pytest -q \
  tests/test_cli.py::test_profile_use_installs_claude_mem_compatible_hooks \
  tests/test_cli.py::test_profile_use_removes_managed_session_end_hook_and_preserves_user_hook
```

Expected: FAIL，因为当前实现仍会重新安装 `session-end`。

- [ ] **Step 3: 最小化修改安装集合**

从 `CLAUDE_MEM_COMPATIBLE_HOOKS` 删除：

```python
"SessionEnd": [
    {"matcher": None, "actions": [("session-end", 60)]},
],
```

不修改 `SESSION_END_ACTION`、`CLAUDE_MEM_HOOK_ACTIONS`、
`_handle_session_end` 或 HTTP API。

- [ ] **Step 4: 更新当前架构说明**

将 `docs/agent-integration-patterns.md` 中当前 Memory Hook 事件列表和数量改为
六个事件，不再包含 `SessionEnd`；通用事件能力矩阵保持不变。

- [ ] **Step 5: 运行相关回归测试**

Run:

```bash
uv run pytest -q tests/test_cli.py tests/test_memory_hooks.py tests/test_memory_api.py
uv run ruff check src/agent_bridge/cli/profile.py tests/test_cli.py
git diff --check
```

Expected: 全部通过；`test_session_end_hook_writes_profile_context_file` 继续通过，
证明服务端兼容实现仍然存在。

- [ ] **Step 6: 提交实现**

```bash
git add src/agent_bridge/cli/profile.py tests/test_cli.py docs/agent-integration-patterns.md
git commit -m "fix(profile): stop installing session end hook"
```

