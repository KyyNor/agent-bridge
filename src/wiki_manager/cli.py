from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

import typer


app = typer.Typer(
    help="Manage wiki content from the command line.",
    no_args_is_help=True,
)


def _package_version() -> str:
    try:
        return version("wiki-manager")
    except PackageNotFoundError:
        return "0.0.0"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"wiki-manager {_package_version()}")
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            help="Show the installed version and exit.",
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Wiki Manager command line interface."""


@app.command()
def hello(
    name: Annotated[str, typer.Argument(help="Name to greet.")] = "world",
) -> None:
    """Print a greeting to verify the CLI wiring."""
    typer.echo(f"Hello, {name}!")


def main() -> None:
    app()
