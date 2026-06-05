from __future__ import annotations

from typing import Annotated

import typer

kb_app = typer.Typer(help="管理知识库", no_args_is_help=True)


@kb_app.command("list")
def list_kbs() -> None:
    """列出可见的知识库"""
    from agent_bridge.cli.app import _echo_mapping, _run_client

    kbs = _run_client(lambda client: client.list_kbs())
    for kb in kbs:
        role = f" ({kb['role']})" if kb.get("role") else ""
        typer.echo(f"{kb['slug']}{role}")


@kb_app.command("create")
def create_kb(
    slug: Annotated[str, typer.Argument(help="知识库标识")],
    name: Annotated[str, typer.Option("--name", help="显示名称")],
    description: Annotated[str, typer.Option("--description", help="描述")] = "",
) -> None:
    """创建知识库"""
    from agent_bridge.cli.app import _echo_mapping, _run_client

    kb = _run_client(lambda client: client.create_kb(slug, name, description))
    _echo_mapping(kb, ("slug", "name"))


@kb_app.command("grant")
def grant_member(
    kb_slug: Annotated[str, typer.Argument(help="知识库标识")],
    linux_user: Annotated[str, typer.Argument(help="授权用户")],
    role: Annotated[str, typer.Argument(help="角色")],
) -> None:
    """授权用户访问知识库"""
    from agent_bridge.cli.app import _echo_mapping, _run_client

    member = _run_client(lambda client: client.grant_member(kb_slug, linux_user, role))
    _echo_mapping(member, ("kb_slug", "linux_user", "role"))
