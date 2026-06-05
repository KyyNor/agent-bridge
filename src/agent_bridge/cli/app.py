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
    help="Agent Bridge: capability and knowledge management.",
    no_args_is_help=True,
)

# Register sub-apps from command group modules
from agent_bridge.cli.knowledge import kb_app  # noqa: E402
from agent_bridge.cli.metamcp import metamcp_app  # noqa: E402
from agent_bridge.cli.server import server_app  # noqa: E402

app.add_typer(kb_app, name="kb")
app.add_typer(server_app, name="server")
app.add_typer(metamcp_app, name="metamcp")

# Re-export symbols used by test monkeypatching at agent_bridge.cli.app.* paths
from agent_bridge.cli.server import (  # noqa: E402, F401
    _paths_from_root,
    _run_server_action,
    server_status_cmd,
    server_init,
    server_start,
    server_stop,
)
from agent_bridge.cli.knowledge import (  # noqa: E402, F401
    list_kbs,
    create_kb,
    grant_member,
)
from agent_bridge.cli.metamcp import (  # noqa: E402, F401
    metamcp_profile_create,
    metamcp_profile_list,
    metamcp_profile_show,
    metamcp_profile_rules,
    metamcp_add,
    metamcp_config,
    metamcp_profile_app,
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
        typer.echo(f"service unavailable: {exc}", err=True)
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
    raise ValueError("scope must be project or user")


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON config: {path}") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError(f"config must be a JSON object: {path}")
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
            raise ValueError("scope must be project or user")
        return scope
    if not _stdin_is_interactive():
        raise ValueError("scope is required in non-interactive mode")
    selected = typer.prompt("选择配置范围 project/user", default="project")
    if selected not in {"project", "user"}:
        raise ValueError("scope must be project or user")
    return selected


def _confirm_overwrite(existing: dict[str, Any], yes: bool) -> None:
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict) or "agent-capability-hub" not in servers:
        return
    if yes:
        return
    if not typer.confirm("agent-capability-hub already exists, overwrite it?", default=False):
        raise RuntimeError("aborted")


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
    """Agent Bridge command line interface."""


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
