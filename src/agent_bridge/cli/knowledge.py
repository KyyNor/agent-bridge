from __future__ import annotations

from typing import Annotated

import typer

kb_app = typer.Typer(help="Manage knowledge bases.", no_args_is_help=True)


@kb_app.command("list")
def list_kbs() -> None:
    """List visible knowledge bases."""
    from agent_bridge.cli.app import _echo_mapping, _run_client

    kbs = _run_client(lambda client: client.list_kbs())
    for kb in kbs:
        role = f" ({kb['role']})" if kb.get("role") else ""
        typer.echo(f"{kb['slug']}{role}")


@kb_app.command("create")
def create_kb(
    slug: Annotated[str, typer.Argument(help="Knowledge base slug.")],
    name: Annotated[str, typer.Option("--name", help="Display name.")],
    description: Annotated[str, typer.Option("--description", help="Description.")] = "",
) -> None:
    """Create a knowledge base."""
    from agent_bridge.cli.app import _echo_mapping, _run_client

    kb = _run_client(lambda client: client.create_kb(slug, name, description))
    _echo_mapping(kb, ("slug", "name"))


@kb_app.command("grant")
def grant_member(
    kb_slug: Annotated[str, typer.Argument(help="Knowledge base slug.")],
    linux_user: Annotated[str, typer.Argument(help="Linux user to grant.")],
    role: Annotated[str, typer.Argument(help="Role to grant.")],
) -> None:
    """Grant a user access to a knowledge base."""
    from agent_bridge.cli.app import _echo_mapping, _run_client

    member = _run_client(lambda client: client.grant_member(kb_slug, linux_user, role))
    _echo_mapping(member, ("kb_slug", "linux_user", "role"))
