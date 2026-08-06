from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated, Any

import typer

from agent_bridge.capability_hub.profiles.docs import (
    POINTER_END,
    POINTER_START,
    SYSTEM_REMINDER_GUIDANCE,
    pointer_block,
    replace_agent_bridge_block,
    stable_hash,
)
from agent_bridge.cli.profile_hooks import profile_hook_app

profile_app = typer.Typer(help="管理能力平面", no_args_is_help=True)
pins_app = typer.Typer(help="管理 Profile 自动 Pin 缓存", no_args_is_help=True)
profile_app.add_typer(pins_app, name="pins")
profile_app.add_typer(profile_hook_app, name="hook")

AGENT_BRIDGE_MEMORY_HOOK_MARKER = "--agent-bridge-hook-id agent-bridge-memory"
AGENT_BRIDGE_RETRIEVAL_HOOK_MARKER = "--agent-bridge-hook-id agent-bridge-retrieval-probe"
AGENT_BRIDGE_PROFILE_SYNC_HOOK_MARKER = "--agent-bridge-hook-id agent-bridge-profile-sync"
AGENT_BRIDGE_HOOK_MARKERS = (
    AGENT_BRIDGE_MEMORY_HOOK_MARKER,
    AGENT_BRIDGE_RETRIEVAL_HOOK_MARKER,
    AGENT_BRIDGE_PROFILE_SYNC_HOOK_MARKER,
)
RETRIEVAL_PROBE_TIMEOUT_SECONDS = 20
RETRIEVAL_PROBE_COMMAND_TIMEOUT_SECONDS = 25
PROFILE_SYNC_HOOK_TIMEOUT_SECONDS = 5

CLAUDE_MEM_COMPATIBLE_HOOKS = {
    "Setup": [
        {"matcher": "*", "actions": [("version-check", 300)]},
    ],
    "SessionStart": [
        {"matcher": "startup|resume|clear|compact", "actions": [("session-start", 60)]},
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


def _agent_bridge_retrieval_hook_command(
    *,
    profile: str,
    server_url: str,
    timeout: int,
) -> str:
    parts = [
        "agent-bridge",
        "profile",
        "hook",
        "claude-code",
        "retrieval-probe",
        "--profile",
        profile,
        "--server-url",
        server_url,
        "--timeout",
        str(timeout),
        "--agent-bridge-hook-id",
        "agent-bridge-retrieval-probe",
    ]
    return " ".join(shlex.quote(part) for part in parts)


def _agent_bridge_profile_sync_hook_command(
    *,
    profile: str,
    url: str,
    scope: str,
) -> str:
    parts = [
        "agent-bridge",
        "profile",
        "sync",
        profile,
        "--url",
        url,
        "--scope",
        scope,
        "--quiet",
        "--agent-bridge-hook-id",
        "agent-bridge-profile-sync",
    ]
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
                if not (
                    isinstance(hook, dict)
                    and any(marker in str(hook.get("command") or "") for marker in AGENT_BRIDGE_HOOK_MARKERS)
                )
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


def _install_profile_hooks(
    settings: dict[str, Any],
    *,
    profile: str,
    server_url: str,
    scope: str,
    mcp_url: str,
) -> dict[str, Any]:
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
    prompt_entries = list(hooks.get("UserPromptSubmit") or [])
    prompt_entries.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "shell": "bash",
                    "command": _agent_bridge_retrieval_hook_command(
                        profile=profile,
                        server_url=server_url,
                        timeout=RETRIEVAL_PROBE_TIMEOUT_SECONDS,
                    ),
                    # 全量探测结果需要在本次 UserPromptSubmit 中作为上下文返回；
                    # 后台 Hook 无法把 additionalContext 注入当前轮对话。
                    "async": False,
                    "timeout": RETRIEVAL_PROBE_COMMAND_TIMEOUT_SECONDS,
                }
            ]
        }
    )
    hooks["UserPromptSubmit"] = prompt_entries
    session_end_entries = list(hooks.get("SessionEnd") or [])
    session_end_entries.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "shell": "bash",
                    "command": _agent_bridge_profile_sync_hook_command(
                        profile=profile,
                        url=mcp_url,
                        scope=scope,
                    ),
                    "timeout": PROFILE_SYNC_HOOK_TIMEOUT_SECONDS,
                }
            ]
        }
    )
    hooks["SessionEnd"] = session_end_entries
    copied["hooks"] = hooks
    return copied


def _managed_mcp_projection(config: dict[str, Any]) -> dict[str, Any]:
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    return {
        "agent-bridge": servers.get("agent-bridge"),
        "agent-capability-hub": servers.get("agent-capability-hub"),
    }


def _managed_hooks_projection(settings: dict[str, Any]) -> dict[str, Any]:
    raw_hooks = settings.get("hooks")
    if not isinstance(raw_hooks, dict):
        return {}
    projection: dict[str, Any] = {}
    for event, entries in raw_hooks.items():
        if not isinstance(entries, list):
            continue
        managed_entries = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                continue
            managed_hooks = [
                hook
                for hook in entry["hooks"]
                if isinstance(hook, dict)
                and any(marker in str(hook.get("command") or "") for marker in AGENT_BRIDGE_HOOK_MARKERS)
            ]
            if not managed_hooks:
                continue
            managed_entry: dict[str, Any] = {"hooks": managed_hooks}
            if "matcher" in entry:
                managed_entry["matcher"] = entry["matcher"]
            managed_entries.append(managed_entry)
        if managed_entries:
            projection[event] = managed_entries
    return projection


def _managed_guidance_projection(content: str) -> list[str]:
    blocks: list[str] = []
    cursor = 0
    while True:
        start = content.find(POINTER_START, cursor)
        if start < 0:
            break
        end = content.find(POINTER_END, start + len(POINTER_START))
        if end < 0:
            blocks.append(content[start:].strip())
            break
        end += len(POINTER_END)
        blocks.append(content[start:end].strip())
        cursor = end
    return blocks


def _profile_sync_projection(
    config: dict[str, Any],
    settings: dict[str, Any],
    guidance: str,
) -> dict[str, Any]:
    return {
        "mcp": _managed_mcp_projection(config),
        "hooks": _managed_hooks_projection(settings),
        "guidance": _managed_guidance_projection(guidance),
    }


def _profile_sync(
    *,
    profile: str,
    url: str,
    scope: str,
) -> list[Path]:
    from agent_bridge.cli.app import _claude_config_path, _load_json_file

    config_path = _claude_config_path(scope)
    settings_path = _claude_settings_path(scope)
    claude_path = (
        Path.cwd() / "CLAUDE.md"
        if scope == "project"
        else Path.home() / ".claude" / "CLAUDE.md"
    )
    existing_config = _load_json_file(config_path)
    existing_settings = _load_claude_settings(settings_path)
    existing_guidance = claude_path.read_text(encoding="utf-8") if claude_path.exists() else ""

    from agent_bridge.cli.app import _with_metamcp_config, _server_url_from_mcp_url

    desired_config = _with_metamcp_config(existing_config, url, profile)
    desired_settings = _install_profile_hooks(
        existing_settings,
        profile=profile,
        server_url=_server_url_from_mcp_url(url),
        scope=scope,
        mcp_url=url,
    )
    desired_guidance = pointer_block(SYSTEM_REMINDER_GUIDANCE)
    current_projection = _profile_sync_projection(existing_config, existing_settings, existing_guidance)
    desired_projection = _profile_sync_projection(desired_config, desired_settings, desired_guidance)
    changed: list[Path] = []

    if stable_hash(current_projection["mcp"]) != stable_hash(desired_projection["mcp"]):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(desired_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        changed.append(config_path)

    if stable_hash(current_projection["hooks"]) != stable_hash(desired_projection["hooks"]):
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(desired_settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        changed.append(settings_path)

    if stable_hash(current_projection["guidance"]) != stable_hash(desired_projection["guidance"]):
        replace_agent_bridge_block(claude_path, desired_guidance)
        changed.append(claude_path)
    return changed


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
        _resolve_metamcp_scope,
    )

    try:
        resolved_scope = _resolve_metamcp_scope(scope)
        path = _claude_config_path(resolved_scope)
        existing = _load_json_file(path)
        _confirm_overwrite(existing, yes)
        changed = _profile_sync(
            profile=profile,
            url=url,
            scope=resolved_scope,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"配置错误: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"已写入: {path}")
    for changed_path in changed:
        if changed_path != path:
            typer.echo(f"已写入: {changed_path}")


@profile_app.command("sync")
def profile_sync(
    profile: Annotated[str, typer.Argument(help="要同步的 Profile 标识")],
    url: Annotated[str, typer.Option("--url", help="Agent Bridge MCP 地址")] = "http://127.0.0.1:8765/mcp",
    scope: Annotated[str, typer.Option("--scope", help="配置范围: project 或 user")] = "project",
    quiet: Annotated[bool, typer.Option("--quiet", hidden=True)] = False,
    hook_id: Annotated[str, typer.Option("--agent-bridge-hook-id", hidden=True)] = "",
) -> None:
    """按当前代码生成结果幂等同步 Profile 的 MCP、Hook 和说明配置。"""
    del hook_id
    try:
        changed = _profile_sync(profile=profile, url=url, scope=scope)
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"配置同步失败: {exc}", err=True)
        raise typer.Exit(1) from None
    if quiet:
        return
    if changed:
        typer.echo(f"已同步 {len(changed)} 个配置文件")
    else:
        typer.echo("Profile 配置已是最新")


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
