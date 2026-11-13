from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from agent_bridge.capability_hub.profiles.docs import (
    POINTER_END,
    POINTER_START,
    remove_agent_bridge_blocks,
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


@dataclass(frozen=True)
class _ProfileInstallation:
    scope: str
    profile_keys: tuple[str, ...]
    components: tuple[str, ...]

    @property
    def profile_label(self) -> str:
        if not self.profile_keys:
            return "未知 Profile"
        if len(self.profile_keys) == 1:
            return self.profile_keys[0]
        return f"Profile 不一致：{' / '.join(self.profile_keys)}"

    @property
    def choice_label(self) -> str:
        scope_label = "当前项目" if self.scope == "project" else "用户级"
        return f"{scope_label} | {self.profile_label} | {'、'.join(self.components)}"

def _echo_mapping(data: dict[str, Any], keys: tuple[str, ...]) -> None:
    parts = [f"{key}: {data[key]}" for key in keys if key in data]
    typer.echo(", ".join(parts) if parts else data)


def _claude_settings_path(scope: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".claude" / "settings.local.json"
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    raise ValueError("scope 必须是 project 或 user")


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


def _profile_scope_paths(scope: str) -> tuple[Path, Path, Path]:
    from agent_bridge.cli.app import _claude_config_path

    config_path = _claude_config_path(scope)
    settings_path = _claude_settings_path(scope)
    claude_path = (
        Path.cwd() / "CLAUDE.md"
        if scope == "project"
        else Path.home() / ".claude" / "CLAUDE.md"
    )
    return config_path, settings_path, claude_path


def _profile_key_from_mcp_config(config: dict[str, Any]) -> str | None:
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    server = servers.get("agent-bridge")
    if not isinstance(server, dict):
        return None
    headers = server.get("headers")
    if not isinstance(headers, dict):
        return None
    profile = str(headers.get("X-Agent-Bridge-MetaMCP-Profile") or "").strip()
    return profile or None


def _profile_key_from_managed_command(command: str) -> str | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if "--profile" in argv:
        index = argv.index("--profile") + 1
        if index < len(argv) and argv[index].strip():
            return argv[index].strip()
    if AGENT_BRIDGE_PROFILE_SYNC_HOOK_MARKER in command and len(argv) > 3:
        if argv[0:3] == ["agent-bridge", "profile", "sync"]:
            return argv[3].strip() or None
    return None


def _profile_keys_from_managed_hooks(settings: dict[str, Any]) -> set[str]:
    raw_hooks = settings.get("hooks")
    if not isinstance(raw_hooks, dict):
        return set()
    profiles: set[str] = set()
    for entries in raw_hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                continue
            for hook in entry["hooks"]:
                if not isinstance(hook, dict):
                    continue
                command = str(hook.get("command") or "")
                if not any(marker in command for marker in AGENT_BRIDGE_HOOK_MARKERS):
                    continue
                profile = _profile_key_from_managed_command(command)
                if profile:
                    profiles.add(profile)
    return profiles


def _discover_profile_installations() -> list[_ProfileInstallation]:
    from agent_bridge.cli.app import _load_json_file

    installations: list[_ProfileInstallation] = []
    for scope in ("project", "user"):
        config_path, settings_path, claude_path = _profile_scope_paths(scope)
        config = _load_json_file(config_path)
        settings = _load_claude_settings(settings_path)
        guidance = claude_path.read_text(encoding="utf-8") if claude_path.exists() else ""
        servers = config.get("mcpServers")
        has_mcp = isinstance(servers, dict) and any(
            name in servers for name in ("agent-bridge", "agent-capability-hub")
        )
        has_hooks = bool(_managed_hooks_projection(settings))
        has_guidance = bool(_managed_guidance_projection(guidance))
        if not (has_mcp or has_hooks or has_guidance):
            continue
        profiles = set(_profile_keys_from_managed_hooks(settings))
        mcp_profile = _profile_key_from_mcp_config(config)
        if mcp_profile:
            profiles.add(mcp_profile)
        components = tuple(
            component
            for component, present in (
                ("MCP", has_mcp),
                ("Hook", has_hooks),
                ("CLAUDE.md 说明块", has_guidance),
            )
            if present
        )
        installations.append(
            _ProfileInstallation(
                scope=scope,
                profile_keys=tuple(sorted(profiles)),
                components=components,
            )
        )
    return installations


def _profile_uninstall(scope: str) -> list[Path]:
    from agent_bridge.cli.app import _load_json_file

    config_path, settings_path, claude_path = _profile_scope_paths(scope)
    existing_config = _load_json_file(config_path)
    existing_settings = _load_claude_settings(settings_path)
    existing_guidance = claude_path.read_text(encoding="utf-8") if claude_path.exists() else ""
    changed: list[Path] = []

    updated_config = dict(existing_config)
    servers = updated_config.get("mcpServers")
    if isinstance(servers, dict):
        updated_servers = dict(servers)
        updated_servers.pop("agent-bridge", None)
        updated_servers.pop("agent-capability-hub", None)
        if updated_servers:
            updated_config["mcpServers"] = updated_servers
        else:
            updated_config.pop("mcpServers", None)
    if stable_hash(_managed_mcp_projection(existing_config)) != stable_hash(
        _managed_mcp_projection(updated_config)
    ):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(updated_config, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(config_path)

    updated_settings = _strip_agent_bridge_hooks(existing_settings)
    if stable_hash(_managed_hooks_projection(existing_settings)) != stable_hash(
        _managed_hooks_projection(updated_settings)
    ):
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(updated_settings, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(settings_path)

    updated_guidance = remove_agent_bridge_blocks(existing_guidance)
    if stable_hash(_managed_guidance_projection(existing_guidance)) != stable_hash(
        _managed_guidance_projection(updated_guidance)
    ):
        claude_path.parent.mkdir(parents=True, exist_ok=True)
        claude_path.write_text(updated_guidance, encoding="utf-8")
        changed.append(claude_path)
    return changed


def _select_profile_installation(
    installations: list[_ProfileInstallation],
    scope: str | None,
) -> _ProfileInstallation | None:
    if scope is not None:
        if scope not in {"project", "user"}:
            raise ValueError("scope 必须是 project 或 user")
        selected = [installation for installation in installations if installation.scope == scope]
        if not selected:
            raise RuntimeError(f"{scope} 范围没有可卸载的 Profile")
        return selected[0]

    from agent_bridge.cli.app import _stdin_is_interactive

    if not _stdin_is_interactive():
        raise ValueError("非交互模式下必须指定 scope")
    import questionary

    selected_scope = questionary.select(
        "选择要卸载的 Profile",
        choices=[
            {"name": installation.choice_label, "value": installation.scope}
            for installation in installations
        ],
    ).ask()
    if selected_scope is None:
        return None
    return next(
        (installation for installation in installations if installation.scope == selected_scope),
        None,
    )


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
    """旧 Claude Code 接入命令，已停用。"""
    typer.echo("Claude Code 接入已停用。请在 Agent 工作台选择 DSH 或 Pi；旧配置可用 profile unuse 卸载。", err=True)
    raise typer.Exit(1)


@profile_app.command("sync", hidden=True)
def profile_sync(
    profile: Annotated[str, typer.Argument()],
    url: Annotated[str, typer.Option("--url")] = "http://127.0.0.1:8765/mcp",
    scope: Annotated[str, typer.Option("--scope")] = "project",
    quiet: Annotated[bool, typer.Option("--quiet", hidden=True)] = False,
    hook_id: Annotated[str, typer.Option("--agent-bridge-hook-id", hidden=True)] = "",
) -> None:
    """旧 SessionEnd Hook 的兼容入口：不再写入配置。"""
    if not quiet:
        typer.echo("Claude Code 配置同步已停用，请用 profile unuse 卸载旧接入。", err=True)
    raise typer.Exit(1)


@profile_app.command("unuse")
def profile_unuse(
    scope: Annotated[str | None, typer.Option("--scope", help="配置范围: project 或 user")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="跳过卸载确认")] = False,
) -> None:
    """交互选择并卸载当前项目或用户级 Profile 配置。"""
    try:
        installations = _discover_profile_installations()
        if not installations:
            typer.echo("当前目录和用户级没有可卸载的 Profile")
            return
        if scope is None:
            typer.echo("可卸载的 Profile：")
            for installation in installations:
                typer.echo(f"- {installation.choice_label}")
        selected = _select_profile_installation(installations, scope)
        if selected is None:
            typer.echo("已取消")
            return
        if not yes and not typer.confirm(
            f"确认卸载 {selected.choice_label}？",
            default=False,
        ):
            typer.echo("已取消")
            return
        changed = _profile_uninstall(selected.scope)
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"卸载错误: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"已卸载: {selected.choice_label}")
    for changed_path in changed:
        typer.echo(f"已更新: {changed_path}")


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
