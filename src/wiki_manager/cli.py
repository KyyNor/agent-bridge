from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any, Callable, TypeVar

import httpx
import typer

from wiki_manager.client import WikiManagerClient
from wiki_manager.server_process import server_status, start_server, stop_server


app = typer.Typer(
    help="Manage wiki content from the command line.",
    no_args_is_help=True,
)
kb_app = typer.Typer(help="Manage knowledge bases.", no_args_is_help=True)
server_app = typer.Typer(help="Manage the local wiki-manager server.", no_args_is_help=True)
app.add_typer(kb_app, name="kb")
app.add_typer(server_app, name="server")

T = TypeVar("T")


def _package_version() -> str:
    try:
        return version("wiki-manager")
    except PackageNotFoundError:
        return "0.0.0"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"wiki-manager {_package_version()}")
        raise typer.Exit()


def _run_client(call: Callable[[WikiManagerClient], T]) -> T:
    try:
        return call(WikiManagerClient.from_config())
    except httpx.HTTPError as exc:
        typer.echo(f"service unavailable: {exc}", err=True)
        raise typer.Exit(1) from None
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None


def _run_server_action(call: Callable[[], T]) -> T:
    try:
        return call()
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"server error: {exc}", err=True)
        raise typer.Exit(1) from None


def _echo_mapping(data: dict[str, Any], keys: tuple[str, ...]) -> None:
    parts = [f"{key}: {data[key]}" for key in keys if key in data]
    typer.echo(", ".join(parts) if parts else data)


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


@kb_app.command("list")
def list_kbs() -> None:
    """List visible knowledge bases."""
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
    kb = _run_client(lambda client: client.create_kb(slug, name, description))
    _echo_mapping(kb, ("slug", "name"))


@kb_app.command("grant")
def grant_member(
    kb_slug: Annotated[str, typer.Argument(help="Knowledge base slug.")],
    linux_user: Annotated[str, typer.Argument(help="Linux user to grant.")],
    role: Annotated[str, typer.Argument(help="Role to grant.")],
) -> None:
    """Grant a user access to a knowledge base."""
    member = _run_client(lambda client: client.grant_member(kb_slug, linux_user, role))
    _echo_mapping(member, ("kb_slug", "linux_user", "role"))


@server_app.command("start")
def server_start() -> None:
    status = _run_server_action(start_server)
    typer.echo(f"running: {status['running']} pid: {status['pid']}")


@server_app.command("stop")
def server_stop() -> None:
    result = _run_server_action(stop_server)
    typer.echo(f"stopped: {result['stopped']} pid: {result['pid']}")


@server_app.command("status")
def server_status_cmd() -> None:
    status = _run_server_action(server_status)
    typer.echo(f"running: {status['running']} pid: {status['pid']}")


@server_app.command("init")
def server_init() -> None:
    """Initialize the running wiki-manager service schema."""
    _run_client(lambda client: client.init_system())
    typer.echo("initialized")


@app.command()
def add(
    source: Annotated[
        Path,
        typer.Argument(
            help="Document file to add.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    kb_slugs: Annotated[list[str], typer.Option("--kb", help="Knowledge base slug.")],
    later: Annotated[bool, typer.Option("--later", help="Queue sync for later.")] = False,
) -> None:
    """Add a document."""
    doc = _run_client(lambda client: client.add_document(source, kb_slugs, later))
    _echo_mapping(doc, ("slug", "current_version_no"))


@app.command()
def update(
    doc_slug: Annotated[str, typer.Argument(help="Document slug.")],
    source: Annotated[
        Path,
        typer.Argument(
            help="Replacement document file.",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    later: Annotated[bool, typer.Option("--later", help="Queue sync for later.")] = False,
) -> None:
    """Add a new document version."""
    doc = _run_client(lambda client: client.update_document(doc_slug, source, later))
    _echo_mapping(doc, ("slug", "current_version_no"))


@app.command("delete")
def delete_document(
    doc_slug: Annotated[str, typer.Argument(help="Document slug.")],
) -> None:
    """Soft delete a document."""
    result = _run_client(lambda client: client.delete_document(doc_slug))
    _echo_mapping(result, ("slug", "status"))


@app.command("purge")
def purge_document(
    doc_slug: Annotated[str, typer.Argument(help="Document slug.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Confirm permanent purge.")] = False,
) -> None:
    """Permanently purge a document."""
    if not yes:
        typer.echo("purge requires --yes", err=True)
        raise typer.Exit(1)
    result = _run_client(lambda client: client.purge_document(doc_slug, confirm=True))
    _echo_mapping(result, ("slug", "status"))


@app.command("backends")
def list_backends() -> None:
    """List configured backends."""
    backends = _run_client(lambda client: client.list_backends())
    for backend in backends:
        typer.echo(f"{backend['slug']} ({backend['type']})")


@app.command("docs")
def list_docs(
    kb_slug: Annotated[str, typer.Option("--kb", help="Knowledge base slug.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
) -> None:
    """List documents in a knowledge base."""
    docs = _run_client(lambda client: client.list_docs(kb_slug, backend=backend))
    for doc in docs:
        title = f" - {doc['title']}" if doc.get("title") else ""
        typer.echo(f"{doc['slug']}{title}")


@app.command("doc")
def get_doc(
    doc_slug: Annotated[str, typer.Argument(help="Document slug.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
) -> None:
    """Show document details."""
    doc = _run_client(lambda client: client.get_doc(doc_slug, backend=backend))
    _echo_mapping(doc, ("slug", "title", "current_version_no", "status"))
    if doc.get("kb_slugs"):
        typer.echo(f"kbs: {', '.join(doc['kb_slugs'])}")
    if doc.get("sync_states"):
        for state in doc["sync_states"]:
            info = f"  {state.get('backend_slug', '')}: {state.get('status', '')}"
            if state.get("chunk_count") is not None:
                info += f" | chunks: {state['chunk_count']}"
            if state.get("backend_status"):
                info += f" | {state['backend_status']}"
            typer.echo(info)


@app.command()
def status(
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
) -> None:
    """Show sync status."""
    result = _run_client(lambda client: client.status(backend=backend))
    jobs = result.get("jobs", [])
    typer.echo(f"jobs: {len(jobs)}")
    for job in jobs:
        parts = [
            str(job.get("status", "")),
            str(job.get("operation", "")),
            str(job.get("backend_slug", "")),
            str(job.get("kb_slug", "")),
            str(job.get("doc_slug", "")),
        ]
        typer.echo(" ".join(part for part in parts if part))


@app.command()
def sync(
    all_users: Annotated[bool, typer.Option("--all", help="Sync jobs for all users.")] = False,
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
) -> None:
    """Run pending sync jobs."""
    result = _run_client(lambda client: client.sync(all_users, backend=backend))
    typer.echo(f"processed: {result.get('processed', 0)}")


@app.command()
def search(
    question: Annotated[str, typer.Argument(help="Search query.")],
    kb_slug: Annotated[str, typer.Option("--kb", help="Knowledge base slug.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
    top_k: Annotated[int, typer.Option("--top-k", help="Number of results.")] = 6,
) -> None:
    """Search knowledge base chunks."""
    result = _run_client(lambda client: client.search(kb_slug, question, backend=backend, top_k=top_k))
    results = result.get("results", [])
    if not results:
        typer.echo("no results")
        return
    for i, chunk in enumerate(results, 1):
        typer.echo(f"[{i}] {chunk.get('document_name', '')} (sim: {chunk.get('similarity', 0):.2f})")
        content = chunk.get("content", "")
        preview = content[:200] + "..." if len(content) > 200 else content
        typer.echo(f"    {preview}")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question to ask.")],
    kb_slug: Annotated[str, typer.Option("--kb", help="Knowledge base slug.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Backend slug filter.")] = None,
    session: Annotated[str | None, typer.Option("--session", help="Session ID for multi-turn.")] = None,
) -> None:
    """Ask a question against a knowledge base."""
    result = _run_client(lambda client: client.ask(kb_slug, question, backend=backend, session_id=session))
    typer.echo(result.get("answer", ""))
    if result.get("session_id"):
        typer.echo(f"session: {result['session_id']}")


def main() -> None:
    app()
