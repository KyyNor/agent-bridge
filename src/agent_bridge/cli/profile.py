from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

profile_app = typer.Typer(help="管理能力平面", no_args_is_help=True)
pins_app = typer.Typer(help="管理 Profile 自动 Pin 缓存", no_args_is_help=True)
profile_app.add_typer(pins_app, name="pins")

_POINTER_START = "<!-- agent-bridge:profile-pointer start -->"
_POINTER_END = "<!-- agent-bridge:profile-pointer end -->"


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


def _replace_agent_bridge_block(path: Path, block: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines(keepends=True)
    kept_lines: list[str] = []
    in_agent_bridge_block = False
    for line in lines:
        marker = line.strip()
        if marker == _POINTER_START:
            in_agent_bridge_block = True
            continue
        if in_agent_bridge_block:
            if marker == _POINTER_END:
                in_agent_bridge_block = False
            continue
        kept_lines.append(line)

    prefix = "".join(kept_lines)
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    next_text = f"{prefix}{block.rstrip()}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(next_text, encoding="utf-8")


def _pointer_block(content: str) -> str:
    return f"{_POINTER_START}\n{content}\n{_POINTER_END}"


def _write_profile_doc(scope: str, profile: str, markdown: str) -> Path:
    profile_path = _profile_doc_path(scope, profile)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(markdown, encoding="utf-8")
    return profile_path


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
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"配置错误: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"已写入: {path}")
    typer.echo(f"已写入: {profile_path}")


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
