from __future__ import annotations

from pathlib import Path
from typing import Annotated, Callable, TypeVar

import typer

from agent_bridge.runtime.server_process import server_status, start_server, stop_server

server_app = typer.Typer(help="Manage the local Agent Bridge server.", no_args_is_help=True)

T = TypeVar("T")


def _paths_from_root(root: Path | None):
    from agent_bridge.core.config import AgentBridgePaths

    return AgentBridgePaths.from_root(root) if root is not None else None


def _run_server_action(call: Callable[[], T]) -> T:
    try:
        return call()
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"server error: {exc}", err=True)
        raise typer.Exit(1) from None


@server_app.command("start")
def server_start(
    root: Annotated[Path | None, typer.Option("--root", help="Agent Bridge root directory.")] = None,
) -> None:
    import agent_bridge.cli.app as _app

    paths = _paths_from_root(root)
    status = _run_server_action(lambda: _app.start_server(paths) if paths is not None else _app.start_server())
    typer.echo(f"running: {status['running']} pid: {status['pid']}")


@server_app.command("stop")
def server_stop(
    root: Annotated[Path | None, typer.Option("--root", help="Agent Bridge root directory.")] = None,
) -> None:
    import agent_bridge.cli.app as _app

    paths = _paths_from_root(root)
    result = _run_server_action(lambda: _app.stop_server(paths) if paths is not None else _app.stop_server())
    typer.echo(f"stopped: {result['stopped']} pid: {result['pid']}")


@server_app.command("status")
def server_status_cmd(
    root: Annotated[Path | None, typer.Option("--root", help="Agent Bridge root directory.")] = None,
) -> None:
    import agent_bridge.cli.app as _app

    paths = _paths_from_root(root)
    status = _run_server_action(lambda: _app.server_status(paths) if paths is not None else _app.server_status())
    typer.echo(f"running: {status['running']} pid: {status['pid']}")


@server_app.command("init")
def server_init() -> None:
    """Initialize the running Agent Bridge service schema."""
    from agent_bridge.cli.app import _run_client

    _run_client(lambda client: client.init_system())
    typer.echo("initialized")
