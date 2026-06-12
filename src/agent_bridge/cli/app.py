from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any, Callable, TypeVar

import httpx
import typer

from agent_bridge.client import AgentBridgeClient
from agent_bridge.runtime.server_process import server_status, start_server, stop_server

app = typer.Typer(
    help="Agent Bridge: 能力与知识管理平台",
    no_args_is_help=True,
)

# Register sub-apps
from agent_bridge.cli.server import server_app  # noqa: E402
from agent_bridge.cli.profile import profile_app  # noqa: E402

app.add_typer(server_app, name="server")
app.add_typer(profile_app, name="profile")

# Re-export symbols used by test monkeypatching
from agent_bridge.cli.server import (  # noqa: E402, F401
    _paths_from_root,
    _run_server_action,
    server_status_cmd,
    server_init,
    server_start,
    server_stop,
)
from agent_bridge.cli.profile import (  # noqa: E402, F401
    profile_create,
    profile_list,
    profile_show,
    profile_rules,
    profile_use,
    profile_config,
)

T = TypeVar("T")


def _package_version() -> str:
    try:
        return version("agent-bridge")
    except PackageNotFoundError:
        return "0.0.0"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agent-bridge {_package_version()}")
        raise typer.Exit()


def _run_client(call: Callable[[AgentBridgeClient], T]) -> T:
    try:
        return call(AgentBridgeClient.from_config())
    except httpx.HTTPError as exc:
        typer.echo(f"服务不可用: {exc}", err=True)
        raise typer.Exit(1) from None
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None


def _echo_mapping(data: dict[str, Any], keys: tuple[str, ...]) -> None:
    parts = [f"{key}: {data[key]}" for key in keys if key in data]
    typer.echo(", ".join(parts) if parts else data)


def _claude_config_path(scope: str) -> Path:
    if scope == "project":
        return Path.cwd() / ".mcp.json"
    if scope == "user":
        return Path.home() / ".mcp.json"
    raise ValueError("scope 必须是 project 或 user")


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON 格式错误: {path}") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError(f"配置必须是 JSON 对象: {path}")
    return loaded


def _with_metamcp_config(existing: dict[str, Any], url: str, profile: str) -> dict[str, Any]:
    config = dict(existing)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers["agent-capability-hub"] = {
        "type": "http",
        "url": url,
        "headers": {"X-Agent-Bridge-MetaMCP-Profile": profile},
    }
    config["mcpServers"] = servers
    return config


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def _resolve_metamcp_scope(scope: str | None) -> str:
    if scope:
        if scope not in {"project", "user"}:
            raise ValueError("scope 必须是 project 或 user")
        return scope
    if not _stdin_is_interactive():
        raise ValueError("非交互模式下必须指定 scope")
    import questionary
    selected = questionary.select(
        "选择配置范围",
        choices=[
            {"name": "project  当前项目 (.mcp.json)", "value": "project"},
            {"name": "user  全局 (~/.mcp.json)", "value": "user"},
        ],
    ).ask()
    if selected is None:
        raise typer.Abort()
    return selected


def _confirm_overwrite(existing: dict[str, Any], yes: bool) -> None:
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict) or "agent-capability-hub" not in servers:
        return
    if yes:
        return
    if not typer.confirm("agent-capability-hub 已存在，是否覆盖？", default=False):
        raise RuntimeError("已取消")


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", "-v",
            callback=_version_callback,
            help="显示版本号",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Agent Bridge 命令行工具"""


def main() -> None:
    app()
