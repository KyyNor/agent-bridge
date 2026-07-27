# Remove profile use Absolute Pointer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `profile use` 不再刷新或引用 Profile Markdown 文件，只在 `CLAUDE.md` 托管块保留 `<system-reminder>` 语义说明。

**Architecture:** `profile use` 直接用现有 marker block 替换逻辑写入静态说明，不再调用服务端刷新 API或解析返回路径。共享的 Profile 文件投影函数继续供 Agent Runtime 使用。

**Tech Stack:** Python 3.11、Typer、pytest。

## Global Constraints

- project 和 user scope 均不得写入 Profile 文件绝对 `@` 路径。
- 再次执行 `profile use` 必须替换旧托管块并保留其他用户内容。
- `refresh_profile_doc_context_file` 的客户端、API和服务端实现保持不变。
- `install_profile_to_cwd` 和 `profile_pointer_block` 保持不变。

---

### Task 1: 简化 profile use 的 CLAUDE.md 托管块

**Files:**

- Modify: `tests/test_cli.py`
- Modify: `src/agent_bridge/cli/profile.py`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/agent-integration-patterns.md`
- Modify: `docs/integrations/retrieval-probe-hook/README.md`
- Modify: `docs/superpowers/specs/2026-07-26-retrieval-probe-hook-design.md`

**Interfaces:**

- Consumes: `pointer_block(content: str) -> str` 和
  `replace_agent_bridge_block(path: Path, block: str) -> None`。
- Produces: `_write_claude_profile_guidance(scope: str) -> Path`，只写入
  `SYSTEM_REMINDER_GUIDANCE`。

- [ ] **Step 1: 写失败测试**

将 project scope、user scope 和旧托管块迁移测试改为断言：

```python
assert "@/server/profiles/" not in claude_md
assert "`<system-reminder>` 是补充的系统信息。" in claude_md
```

将刷新调用测试改为一个会在调用时失败的客户端：

```python
class FakeClient:
    def refresh_profile_doc_context_file(self, profile_key):
        raise AssertionError("profile use must not refresh profile docs")
```

执行 `profile use` 后断言退出码为 0，证明不再调用刷新接口。

- [ ] **Step 2: 运行聚焦测试并确认失败**

Run:

```bash
uv run pytest -q \
  tests/test_cli.py::test_profile_use_writes_system_reminder_guidance_without_profile_pointer \
  tests/test_cli.py::test_profile_use_does_not_refresh_profile_doc \
  tests/test_cli.py::test_profile_use_replaces_old_pointer_with_guidance_and_preserves_other_files \
  tests/test_cli.py::test_profile_use_writes_user_scope_guidance_without_profile_pointer
```

Expected: FAIL，因为当前实现仍调用刷新 API并写入绝对路径。

- [ ] **Step 3: 最小化修改 profile use**

在 `src/agent_bridge/cli/profile.py`：

```python
def _write_claude_profile_guidance(scope: str) -> Path:
    if scope == "project":
        claude_path = Path.cwd() / "CLAUDE.md"
    elif scope == "user":
        claude_path = Path.home() / ".claude" / "CLAUDE.md"
    else:
        raise ValueError("scope 必须是 project 或 user")
    replace_agent_bridge_block(
        claude_path,
        pointer_block(SYSTEM_REMINDER_GUIDANCE),
    )
    return claude_path
```

删除 `_server_profile_doc_path` 和 `_write_claude_profile_pointer`，并从
`profile_use` 删除：

```python
rendered_doc = _run_client(lambda client: client.refresh_profile_doc_context_file(profile))
profile_path = _server_profile_doc_path(rendered_doc)
```

改为：

```python
claude_path = _write_claude_profile_guidance(resolved_scope)
```

- [ ] **Step 4: 更新当前使用说明**

文档改为说明 `CLAUDE.md` 托管块只保存 system-reminder 语义；动态 Profile 与
Memory 由 `SessionStart` 注入。历史规格和 Agent Runtime 的文件投影说明不修改。

- [ ] **Step 5: 运行回归验证**

Run:

```bash
uv run pytest -q tests/test_cli.py tests/test_agent_service.py tests/test_profile_docs.py
uv run ruff check src/agent_bridge/cli/profile.py tests/test_cli.py
git diff --check
```

Expected: 全部通过；Agent Runtime 的 Profile 文件和 `@` 指针测试继续通过。

- [ ] **Step 6: 提交实现**

```bash
git add \
  src/agent_bridge/cli/profile.py \
  tests/test_cli.py \
  README.md \
  CLAUDE.md \
  docs/agent-integration-patterns.md \
  docs/integrations/retrieval-probe-hook/README.md \
  docs/superpowers/specs/2026-07-26-retrieval-probe-hook-design.md
git commit -m "fix(profile): remove absolute profile pointer"
```

