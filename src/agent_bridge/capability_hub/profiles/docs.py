"""Render profile guidance markdown for Agent Bridge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


# --- Profile pointer integration (CLAUDE.md / AGENTS.md @-import blocks) ---
# These mirror the `agb profile use` behavior: the rendered profile markdown
# lives in its own file and is referenced from CLAUDE.md via an @-import
# pointer wrapped in marker comments, so the block can be replaced idempotently.
POINTER_START = "<!-- agent-bridge:profile-pointer start -->"
POINTER_END = "<!-- agent-bridge:profile-pointer end -->"


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pointer_block(content: str) -> str:
    """Wrap profile-pointer content in the agent-bridge marker block."""
    return f"{POINTER_START}\n{content}\n{POINTER_END}"


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
    loads it as project guidance. Mirrors ``agb profile use``. Returns the
    profile document path.
    """
    from agent_bridge.core.slug import make_slug

    doc_path = cwd / ".agent-bridge" / "profiles" / f"{make_slug(profile)}.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(markdown, encoding="utf-8")
    replace_agent_bridge_block(cwd / "CLAUDE.md", pointer_block(f"@{doc_path.resolve()}"))
    return doc_path


def render_profile_markdown(summary: dict[str, Any], manual_notes: str) -> str:
    profile_name = summary.get("profile_name") or summary.get("profile_key") or "Unnamed Profile"
    services = _named_items(summary.get("services") or [], key_field="service_key")
    repositories = _named_items(summary.get("code_repositories") or [], key_field="repo_key")
    knowledge_bases = _named_items(summary.get("knowledge_bases") or [], key_field="slug")
    notes = manual_notes.strip() or "No manual notes."

    return "\n".join(
        [
            f"# Agent Bridge Profile: {profile_name}",
            "",
            "## How To Use Agent Bridge",
            "",
            "- Use `search` to discover relevant tools and resources available to this profile.",
            "- Use `execute` to call an allowed MCP tool after choosing the right service and tool.",
            "- Use code repository capabilities to search and inspect indexed source code.",
            "- Use knowledge base capabilities to search and ask questions over connected documentation.",
            "- High-frequency capabilities may also be exposed directly as `pin_*` tools.",
            "",
            "## Available MCP Services",
            "",
            "*_No MCP services._" if not services else "\n".join(services),
            "",
            "## Available Code Repositories",
            "",
            "*_None._" if not repositories else "\n".join(repositories),
            "",
            "## Available Knowledge Bases",
            "",
            "*_None._" if not knowledge_bases else "\n".join(knowledge_bases),
            "",
            "## Manual Notes",
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
        if not key:
            continue
        rendered.append(f"- {name} (`{key}`)")
    return rendered
