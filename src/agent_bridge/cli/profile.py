from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated, Any

import typer

profile_app = typer.Typer(help="管理能力平面", no_args_is_help=True)
pins_app = typer.Typer(help="管理 Profile 自动 Pin 缓存", no_args_is_help=True)
profile_app.add_typer(pins_app, name="pins")

from agent_bridge.capability_hub.profiles.docs import (  # noqa: E402
    pointer_block as _pointer_block,
    replace_agent_bridge_block as _replace_agent_bridge_block,
)


AGENT_BRIDGE_HOOK_MARKER = "--agent-bridge-hook-id agent-bridge-memory"

CLAUDE_MEM_COMPATIBLE_HOOKS = {
    "Setup": [
        {"matcher": "*", "actions": [("version-check", 300)]},
    ],
    "SessionStart": [
        {"matcher": "startup|clear|compact", "actions": [("start", 60), ("context", 60)]},
    ],
    "UserPromptSubmit": [
        {"matcher": None, "actions": [("session-init", 60)]},
    ],
    "PostToolUse": [
        {"matcher": "*", "actions": [("observation", 120)]},
    ],
    "PreToolUse": [
        {"matcher": "Read", "actions": [("file-context", 60)]},
    ],
    "Stop": [
        {"matcher": None, "actions": [("summarize", 120)]},
    ],
}


def _echo_mapping(data: dict[str, Any], keys: tuple[str, ...]) -> None:
    parts = [f"{key}: {data[key]}" for key in keys if key in data]
    typer.echo(", ".join(parts) if parts else data)


def _profile_doc_path(scope: str, profile: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".agent-bridge" / "profiles" / f"{profile}.md"
    if scope == "user":
        return Path.home() / ".agent-bridge" / "profiles" / f"{profile}.md"
    raise ValueError("scope 必须是 project 或 user")


def _pointer_paths(scope: str) -> tuple[Path, Path]:
    if scope == "project":
        return Path.cwd() / "CLAUDE.md", Path.cwd() / "AGENTS.md"
    if scope == "user":
        return Path.home() / ".claude" / "CLAUDE.md", Path.home() / ".codex" / "AGENTS.md"
    raise ValueError("scope 必须是 project 或 user")


def _write_profile_doc(scope: str, profile: str, markdown: str) -> Path:
    profile_path = _profile_doc_path(scope, profile)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(markdown, encoding="utf-8")
    return profile_path


def _claude_settings_path(scope: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".claude" / "settings.local.json"
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    raise ValueError("scope 必须是 project 或 user")


def _agent_bridge_hook_command(
    action: str,
    *,
    profile: str,
    server_url: str,
    scope: str,
    event: str,
    matcher: str | None,
    timeout: int,
) -> str:
    parts = [
        "agent-bridge",
        "memory",
        "hook",
        "claude-code",
        action,
        "--profile",
        profile,
        "--server-url",
        server_url,
        "--scope",
        scope,
        "--event",
        event,
        "--timeout",
        str(timeout),
        "--agent-bridge-hook-id",
        "agent-bridge-memory",
    ]
    if matcher is not None:
        parts.extend(["--matcher", matcher])
    return " ".join(shlex.quote(part) for part in parts)


def _load_claude_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RuntimeError(f"Claude settings must be a JSON object: {path}")
    return loaded


def _strip_agent_bridge_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    copied = dict(settings)
    raw_hooks = copied.get("hooks")
    if not isinstance(raw_hooks, dict):
        return copied
    cleaned: dict[str, Any] = {}
    for event, entries in raw_hooks.items():
        if not isinstance(entries, list):
            cleaned[event] = entries
            continue
        kept_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                kept_entries.append(entry)
                continue
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                kept_entries.append(entry)
                continue
            kept_hooks = [
                hook
                for hook in hooks
                if not (isinstance(hook, dict) and AGENT_BRIDGE_HOOK_MARKER in str(hook.get("command") or ""))
            ]
            if kept_hooks:
                new_entry = dict(entry)
                new_entry["hooks"] = kept_hooks
                kept_entries.append(new_entry)
        if kept_entries:
            cleaned[event] = kept_entries
    if cleaned:
        copied["hooks"] = cleaned
    else:
        copied.pop("hooks", None)
    return copied


def _install_memory_hooks(settings: dict[str, Any], *, profile: str, server_url: str, scope: str) -> dict[str, Any]:
    copied = _strip_agent_bridge_hooks(settings)
    hooks = copied.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    else:
        hooks = dict(hooks)
    for event, specs in CLAUDE_MEM_COMPATIBLE_HOOKS.items():
        event_entries = list(hooks.get(event) or [])
        for spec in specs:
            matcher = spec["matcher"]
            entry: dict[str, Any] = {
                "hooks": [
                    {
                        "type": "command",
                        "shell": "bash",
                        "command": _agent_bridge_hook_command(
                            action,
                            profile=profile,
                            server_url=server_url,
                            scope=scope,
                            event=event,
                            matcher=matcher,
                            timeout=timeout,
                        ),
                        "timeout": timeout,
                    }
                    for action, timeout in spec["actions"]
                ]
            }
            if matcher is not None:
                entry["matcher"] = matcher
            event_entries.append(entry)
        hooks[event] = event_entries
    copied["hooks"] = hooks
    return copied


def _write_memory_hooks(scope: str, *, profile: str, server_url: str, enabled: bool) -> Path:
    settings_path = _claude_settings_path(scope)
    settings = _load_claude_settings(settings_path)
    updated = (
        _install_memory_hooks(settings, profile=profile, server_url=server_url, scope=scope)
        if enabled
        else _strip_agent_bridge_hooks(settings)
    )
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings_path


def _profile_memory_hooks_enabled(profile: str) -> bool:
    from agent_bridge.cli.app import _run_client

    try:
        binding = _run_client(lambda client: client.get_profile_memory(profile))
    except AttributeError:
        return False
    return bool(binding.get("enabled")) and bool(binding.get("block_key"))


@profile_app.command("create")
def profile_create(
    profile_key: Annotated[str, typer.Argument(help="Profile 标识")],
    name: Annotated[str, typer.Option("--name", help="显示名称")],
    description: Annotated[str, typer.Option("--description", help="描述")] = "",
    status: Annotated[str, typer.Option("--status", help="状态")] = "active",
) -> None:
    """创建能力平面"""
    from agent_bridge.cli.app import _run_client

    profile = _run_client(lambda client: client.upsert_profile(profile_key, name, description, status))
    _echo_mapping(profile, ("profile_key", "name", "status"))


@profile_app.command("list")
def profile_list() -> None:
    """列出所有 Profile"""
    from agent_bridge.cli.app import _run_client

    profiles = _run_client(lambda client: client.list_profiles())
    for profile in profiles:
        typer.echo(
            f"{profile['profile_key']} | allow: {profile.get('allow_count', 0)} | deny: {profile.get('deny_count', 0)}"
        )


@profile_app.command("show")
def profile_show(profile_key: Annotated[str, typer.Argument(help="Profile 标识")]) -> None:
    """查看 Profile 详情与规则"""
    from agent_bridge.cli.app import _run_client

    profile = _run_client(lambda client: client.get_profile(profile_key))
    _echo_mapping(profile, ("profile_key", "name", "status"))
    for rule in profile.get("rules", []):
        typer.echo(f"  {rule['effect']} {rule['source_type']}:{rule['source_key']}")


@profile_app.command("rules")
def profile_rules(
    profile_key: Annotated[str, typer.Argument(help="Profile 标识")],
    allow: Annotated[list[str], typer.Option("--allow", help="允许的 MCP 服务标识")] = [],
    deny: Annotated[list[str], typer.Option("--deny", help="拒绝的 MCP 服务标识")] = [],
) -> None:
    """配置 Profile 的访问规则"""
    from agent_bridge.cli.app import _run_client

    rules = [
        {"source_type": "mcp_service", "source_key": source_key, "effect": "allow"}
        for source_key in allow
    ] + [
        {"source_type": "mcp_service", "source_key": source_key, "effect": "deny"}
        for source_key in deny
    ]
    profile = _run_client(lambda client: client.replace_profile_rules(profile_key, rules))
    typer.echo(f"profile: {profile['profile_key']} 规则数: {len(profile.get('rules', []))}")


@profile_app.command("use")
def profile_use(
    profile: Annotated[str, typer.Argument(help="要激活的 Profile 标识")],
    url: Annotated[str, typer.Option("--url", help="Agent Bridge MCP 地址")] = "http://127.0.0.1:8765/mcp",
    scope: Annotated[str | None, typer.Option("--scope", help="配置范围: project 或 user")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="覆盖已有配置")] = False,
) -> None:
    """接入 Profile 到 .mcp.json (Claude Code 配置)"""
    from agent_bridge.cli.app import (
        _claude_config_path,
        _confirm_overwrite,
        _load_json_file,
        _run_client,
        _resolve_metamcp_scope,
        _server_url_from_mcp_url,
        _with_metamcp_config,
    )

    try:
        resolved_scope = _resolve_metamcp_scope(scope)
        path = _claude_config_path(resolved_scope)
        existing = _load_json_file(path)
        _confirm_overwrite(existing, yes)
        rendered = _run_client(lambda client: client.render_profile_doc(profile))
        markdown = rendered.get("markdown")
        if not isinstance(markdown, str):
            raise RuntimeError("Profile 文档渲染结果缺少 markdown")
        profile_path = _write_profile_doc(resolved_scope, profile, markdown)
        claude_path, agents_path = _pointer_paths(resolved_scope)
        resolved_profile_path = profile_path.resolve()
        _replace_agent_bridge_block(claude_path, _pointer_block(f"@{resolved_profile_path}"))
        _replace_agent_bridge_block(
            agents_path,
            _pointer_block(
                "Read the active Agent Bridge profile before using agent-bridge capabilities: "
                f"{resolved_profile_path}"
            ),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_with_metamcp_config(existing, url, profile), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        hooks_path = _write_memory_hooks(
            resolved_scope,
            profile=profile,
            server_url=_server_url_from_mcp_url(url),
            enabled=_profile_memory_hooks_enabled(profile),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"配置错误: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"已写入: {path}")
    typer.echo(f"已写入: {profile_path}")
    typer.echo(f"已写入: {hooks_path}")


@profile_app.command("refresh")
def profile_refresh(
    profile: Annotated[str, typer.Argument(help="要刷新的 Profile 标识")],
    scope: Annotated[str, typer.Option("--scope", help="配置范围: project 或 user")] = "project",
) -> None:
    """刷新本地 Profile Markdown 文档"""
    from agent_bridge.cli.app import _run_client

    try:
        rendered = _run_client(lambda client: client.render_profile_doc(profile))
        markdown = rendered.get("markdown")
        if not isinstance(markdown, str):
            raise RuntimeError("Profile 文档渲染结果缺少 markdown")
        profile_path = _write_profile_doc(scope, profile, markdown)
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"配置错误: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"已刷新: {profile_path}")


@pins_app.command("refresh")
def profile_pins_refresh(profile: Annotated[str, typer.Argument(help="要清理自动 Pin 缓存的 Profile 标识")]) -> None:
    """清理 Profile 自动 Pin 缓存"""
    from agent_bridge.cli.app import _run_client

    result = _run_client(lambda client: client.refresh_profile_pin_cache(profile))
    typer.echo(f"profile: {result.get('profile_key', profile)} 自动 Pin 缓存已清理")


@profile_app.command("config")
def profile_config(scope: Annotated[str, typer.Option("--scope", help="配置范围: project 或 user")] = "project") -> None:
    """查看当前 .mcp.json 配置"""
    from agent_bridge.cli.app import _claude_config_path

    try:
        path = _claude_config_path(scope)
    except ValueError as exc:
        typer.echo(f"配置错误: {exc}", err=True)
        raise typer.Exit(1) from None
    if not path.exists():
        typer.echo(f"文件不存在: {path}")
        return
    typer.echo(path.read_text(encoding="utf-8"))
