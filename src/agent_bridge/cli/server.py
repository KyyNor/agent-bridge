from __future__ import annotations

from pathlib import Path
from typing import Annotated, Callable, TypeVar

import typer

from agent_bridge.server_runtime.server_process import server_status, start_server, stop_server

server_app = typer.Typer(help="管理 Agent Bridge 服务", no_args_is_help=True)

T = TypeVar("T")


def _paths_from_root(root: Path | None):
    from agent_bridge.core.config import AgentBridgePaths

    return AgentBridgePaths.from_root(root) if root is not None else None


def _run_server_action(call: Callable[[], T]) -> T:
    try:
        return call()
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"服务错误: {exc}", err=True)
        raise typer.Exit(1) from None


@server_app.command("start")
def server_start(
    root: Annotated[Path | None, typer.Option("--root", help="Agent Bridge 数据目录")] = None,
) -> None:
    """启动服务"""
    import agent_bridge.cli.app as _app

    paths = _paths_from_root(root)
    status = _run_server_action(lambda: _app.start_server(paths) if paths is not None else _app.start_server())
    typer.echo(f"运行中: {status['running']} 进程: {status['pid']}")


@server_app.command("stop")
def server_stop(
    root: Annotated[Path | None, typer.Option("--root", help="Agent Bridge 数据目录")] = None,
) -> None:
    """停止服务"""
    import agent_bridge.cli.app as _app

    paths = _paths_from_root(root)
    result = _run_server_action(lambda: _app.stop_server(paths) if paths is not None else _app.stop_server())
    typer.echo(f"已停止: {result['stopped']} 进程: {result['pid']}")


@server_app.command("status")
def server_status_cmd(
    root: Annotated[Path | None, typer.Option("--root", help="Agent Bridge 数据目录")] = None,
) -> None:
    """查看服务运行状态"""
    import agent_bridge.cli.app as _app

    paths = _paths_from_root(root)
    status = _run_server_action(lambda: _app.server_status(paths) if paths is not None else _app.server_status())
    typer.echo(f"运行中: {status['running']} 进程: {status['pid']}")


@server_app.command("init")
def server_init() -> None:
    """初始化服务数据库表结构"""
    from agent_bridge.cli.app import _run_client

    _run_client(lambda client: client.init_system())
    typer.echo("初始化完成")
