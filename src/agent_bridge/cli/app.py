from __future__ import annotations

import json
import getpass
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any, Callable, TypeVar

import httpx
import typer

from agent_bridge.client import AgentBridgeClient
from agent_bridge.server_runtime.server_process import server_status, start_server, stop_server

app = typer.Typer(
    help="Agent Bridge: 能力与知识管理平台",
    no_args_is_help=True,
)

# Register sub-apps
from agent_bridge.cli.server import server_app  # noqa: E402
from agent_bridge.cli.profile import profile_app  # noqa: E402
from agent_bridge.cli.memory import memory_app  # noqa: E402

app.add_typer(server_app, name="server")
app.add_typer(profile_app, name="profile")
app.add_typer(memory_app, name="memory")

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


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


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
