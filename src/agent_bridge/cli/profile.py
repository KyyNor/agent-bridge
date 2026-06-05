from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

profile_app = typer.Typer(help="管理能力平面", no_args_is_help=True)


def _echo_mapping(data: dict[str, Any], keys: tuple[str, ...]) -> None:
    parts = [f"{key}: {data[key]}" for key in keys if key in data]
    typer.echo(", ".join(parts) if parts else data)


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
    url: Annotated[str, typer.Option("--url", help="Agent Bridge MetaMCP 地址")],
    profile: Annotated[str, typer.Option("--profile", help="要激活的 Profile 标识")],
    scope: Annotated[str | None, typer.Option("--scope", help="配置范围: project 或 user")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="覆盖已有配置")] = False,
) -> None:
    """接入 Profile 到 .mcp.json (Claude Code 配置)"""
    from agent_bridge.cli.app import (
        _claude_config_path,
        _confirm_overwrite,
        _load_json_file,
        _resolve_metamcp_scope,
        _with_metamcp_config,
    )

    try:
        resolved_scope = _resolve_metamcp_scope(scope)
        path = _claude_config_path(resolved_scope)
        existing = _load_json_file(path)
        _confirm_overwrite(existing, yes)
        path.write_text(
            json.dumps(_with_metamcp_config(existing, url, profile), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"配置错误: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"已写入: {path}")


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
