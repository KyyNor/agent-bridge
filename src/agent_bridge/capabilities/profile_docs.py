"""Render profile guidance markdown for Agent Bridge."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
