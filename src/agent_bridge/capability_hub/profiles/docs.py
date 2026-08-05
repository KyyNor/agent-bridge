"""Render profile guidance markdown for Agent Bridge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


# --- Agent Runtime profile projection (CLAUDE.md / AGENTS.md @-import blocks) ---
# Isolated Agent runs write rendered profile markdown into their work directory
# and reference it from CLAUDE.md through a replaceable marker block. Interactive
# `profile use` only reuses the marker helpers for system-reminder guidance.
POINTER_START = "<!-- agent-bridge:profile-pointer start -->"
POINTER_END = "<!-- agent-bridge:profile-pointer end -->"
SYSTEM_REMINDER_GUIDANCE = "`<system-reminder>` 是补充的系统信息。"


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pointer_block(content: str) -> str:
    """Wrap profile-pointer content in the agent-bridge marker block."""
    return f"{POINTER_START}\n{content}\n{POINTER_END}"


def profile_pointer_block(profile_path: str | Path) -> str:
    """构造 Claude Profile 引用及 system-reminder 语义说明。"""
    return pointer_block(
        f"@{profile_path}\n\n{SYSTEM_REMINDER_GUIDANCE}"
    )


def replace_agent_bridge_block(path: Path, block: str) -> None:
    """Idempotently replace the agent-bridge pointer block in a markdown file.

    Any existing block between ``POINTER_START`` and ``POINTER_END`` is removed
    first, then the new block is appended after the surviving content.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines(keepends=True)
    kept: list[str] = []
    in_block = False
    for line in lines:
        marker = line.strip()
        if marker == POINTER_START:
            in_block = True
            continue
        if in_block:
            if marker == POINTER_END:
                in_block = False
            continue
        kept.append(line)

    prefix = "".join(kept)
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{prefix}{block.rstrip()}\n", encoding="utf-8")


def install_profile_to_cwd(cwd: Path, profile: str, markdown: str) -> Path:
    """Install a profile's rendered markdown into ``cwd`` for an agent run.

    Writes the profile document to ``cwd/.agent-bridge/profiles/{profile}.md``
    and injects a ``@``-import pointer into ``cwd/CLAUDE.md`` so Claude Code
    loads it as project guidance. This is specific to isolated Agent Runtime
    workspaces; interactive ``profile use`` does not write this pointer.
    Returns the profile document path.
    """
    from agent_bridge.core.slug import make_slug

    doc_path = cwd / ".agent-bridge" / "profiles" / f"{make_slug(profile)}.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(markdown, encoding="utf-8")
    replace_agent_bridge_block(
        cwd / "CLAUDE.md",
        profile_pointer_block(doc_path.resolve()),
    )
    return doc_path


def render_profile_markdown(summary: dict[str, Any], manual_notes: str) -> str:
    profile_name = summary.get("profile_name") or summary.get("profile_key") or "未命名 Profile"
    services = _named_items(summary.get("services") or [], key_field="service_key")
    repositories = _named_items(summary.get("code_repositories") or [], key_field="repo_key")
    knowledge_bases = _named_items(summary.get("knowledge_bases") or [], key_field="slug")
    business_ledgers = summary.get("business_ledgers") or []
    notes = manual_notes.strip() or "暂无手动备注。"

    return "\n".join(
        [
            f"# Agent Bridge Profile：{profile_name}",
            "",
            "## 如何使用 Agent Bridge",
            "",
            "- 收到 Agent Bridge 后台探测结果时，根据命中资源和建议工具继续检索；探测命中数仅用于路由，不应作为答案证据。",
            "- 仅当用户询问下方「可用代码仓库」所列仓库中的源码实现（函数 / 模块 / 调用关系）时，调用 `codegraph_explore`。",
            "- SQL、数据加工、ETL、报表口径等不在代码仓库里的逻辑，不要使用 `codegraph_explore`。",
            "- 使用 `search` 发现此 profile 可用的相关工具和资源。",
            "- 选择合适的服务与工具后，使用 `execute` 调用已允许的 MCP 工具。",
            "- 需要原始知识库片段时使用 `wiki_search`；需要基于知识库生成回答时使用 `wiki_ask`。",
            "- 高频能力也可能以 `pin_*` 工具的形式直接暴露。",
            "",
            "## 可用 MCP 服务（使用 `execute`、`search` 调用）",
            "",
            "*_暂无 MCP 服务。_*" if not services else "\n".join(services),
            "",
            "## 可用代码仓库",
            "",
            "*_暂无。_*" if not repositories else "\n".join(repositories),
            "",
            "## 可用知识库",
            "",
            "*_暂无。_*" if not knowledge_bases else "\n".join(knowledge_bases),
            "",
            "## 可用业务台账",
            "",
            "使用顶级工具 `query_business_ledger` 查询；只可使用下列台账和字段。"
            if business_ledgers
            else "*_暂无。_*",
            *(_business_ledger_items(business_ledgers) if business_ledgers else []),
            "",
            "## 手动备注",
            "",
            notes,
            "",
        ]
    )


def _named_items(items: list[dict[str, Any]], *, key_field: str) -> list[str]:
    rendered = []
    for item in items:
        key = str(item.get(key_field) or "").strip()
        name = str(item.get("name") or key).strip()
        description = " ".join(str(item.get("description") or "").split())
        if not key:
            continue
        suffix = f"：{description}" if description else ""
        rendered.append(f"- {name} (`{key}`){suffix}")
    return rendered


def _business_ledger_items(items: list[dict[str, Any]]) -> list[str]:
    rendered: list[str] = []
    for item in items:
        key = str(item.get("ledger_key") or "").strip()
        if not key:
            continue
        name = str(item.get("name") or key).strip()
        description = " ".join(str(item.get("description") or "").split())
        suffix = f"：{description}" if description else ""
        rendered.append(f"- {name} (`{key}`){suffix}")
        for field in item.get("fields") or []:
            modes = "/".join(str(mode) for mode in field.get("query_modes") or []) or "仅返回"
            rendered.append(f"  - `{field.get('field_key')}`（{field.get('name')}，{field.get('field_type')}，{modes}）")
    return rendered
