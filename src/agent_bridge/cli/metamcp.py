from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

metamcp_app = typer.Typer(help="Manage MetaMCP profiles and Claude Code connection.", no_args_is_help=True)
metamcp_profile_app = typer.Typer(help="Manage Project Profiles.", no_args_is_help=True)
metamcp_app.add_typer(metamcp_profile_app, name="profile")


@metamcp_profile_app.command("create")
def metamcp_profile_create(
    profile_key: Annotated[str, typer.Argument(help="Project Profile key.")],
    name: Annotated[str, typer.Option("--name", help="Display name.")],
    description: Annotated[str, typer.Option("--description", help="Description.")] = "",
    status: Annotated[str, typer.Option("--status", help="Profile status.")] = "active",
) -> None:
    from agent_bridge.cli.app import _echo_mapping, _run_client

    profile = _run_client(lambda client: client.upsert_profile(profile_key, name, description, status))
    _echo_mapping(profile, ("profile_key", "name", "status"))


@metamcp_profile_app.command("list")
def metamcp_profile_list() -> None:
    from agent_bridge.cli.app import _run_client

    profiles = _run_client(lambda client: client.list_profiles())
    for profile in profiles:
        typer.echo(
            f"{profile['profile_key']} | allow: {profile.get('allow_count', 0)} | deny: {profile.get('deny_count', 0)}"
        )


@metamcp_profile_app.command("show")
def metamcp_profile_show(profile_key: Annotated[str, typer.Argument(help="Project Profile key.")]) -> None:
    from agent_bridge.cli.app import _echo_mapping, _run_client

    profile = _run_client(lambda client: client.get_profile(profile_key))
    _echo_mapping(profile, ("profile_key", "name", "status"))
    for rule in profile.get("rules", []):
        typer.echo(f"  {rule['effect']} {rule['source_type']}:{rule['source_key']}")


@metamcp_profile_app.command("rules")
def metamcp_profile_rules(
    profile_key: Annotated[str, typer.Argument(help="Project Profile key.")],
    allow: Annotated[list[str], typer.Option("--allow", help="Allowed MCP service key.")] = [],
    deny: Annotated[list[str], typer.Option("--deny", help="Denied MCP service key.")] = [],
) -> None:
    from agent_bridge.cli.app import _run_client

    rules = [
        {"source_type": "mcp_service", "source_key": source_key, "effect": "allow"}
        for source_key in allow
    ] + [
        {"source_type": "mcp_service", "source_key": source_key, "effect": "deny"}
        for source_key in deny
    ]
    profile = _run_client(lambda client: client.replace_profile_rules(profile_key, rules))
    typer.echo(f"profile: {profile['profile_key']} rules: {len(profile.get('rules', []))}")


@metamcp_app.command("add")
def metamcp_add(
    url: Annotated[str, typer.Option("--url", help="MetaMCP HTTP URL.")],
    profile: Annotated[str, typer.Option("--profile", help="Project Profile key.")],
    scope: Annotated[str | None, typer.Option("--scope", help="project or user.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Overwrite existing config without confirmation.")] = False,
) -> None:
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
        typer.echo(f"metamcp config error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"written: {path}")


@metamcp_app.command("config")
def metamcp_config(scope: Annotated[str, typer.Option("--scope", help="project or user.")] = "project") -> None:
    from agent_bridge.cli.app import _claude_config_path

    try:
        path = _claude_config_path(scope)
    except ValueError as exc:
        typer.echo(f"metamcp config error: {exc}", err=True)
        raise typer.Exit(1) from None
    if not path.exists():
        typer.echo(f"missing: {path}")
        return
    typer.echo(path.read_text(encoding="utf-8"))
