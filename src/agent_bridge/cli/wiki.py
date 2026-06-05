from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Callable, TypeVar

import httpx
import typer

from agent_bridge.client import AgentBridgeClient

wiki_app = typer.Typer(help="知识库与文档管理", no_args_is_help=True)

T = TypeVar("T")


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


@wiki_app.command("add")
def add(
    source: Annotated[
        Path,
        typer.Argument(help="文档文件路径", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    kb_slugs: Annotated[list[str], typer.Option("--kb", help="知识库标识")],
    later: Annotated[bool, typer.Option("--later", help="稍后同步")] = False,
) -> None:
    """添加文档"""
    doc = _run_client(lambda client: client.add_document(source, kb_slugs, later))
    _echo_mapping(doc, ("slug", "current_version_no"))


@wiki_app.command("update")
def update(
    doc_slug: Annotated[str, typer.Argument(help="文档标识")],
    source: Annotated[
        Path,
        typer.Argument(help="替换文档文件路径", exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
    later: Annotated[bool, typer.Option("--later", help="稍后同步")] = False,
) -> None:
    """更新文档版本"""
    doc = _run_client(lambda client: client.update_document(doc_slug, source, later))
    _echo_mapping(doc, ("slug", "current_version_no"))


@wiki_app.command("delete")
def delete_document(
    doc_slug: Annotated[str, typer.Argument(help="文档标识")],
) -> None:
    """软删除文档"""
    result = _run_client(lambda client: client.delete_document(doc_slug))
    _echo_mapping(result, ("slug", "status"))


@wiki_app.command("purge")
def purge_document(
    doc_slug: Annotated[str, typer.Argument(help="文档标识")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="确认永久删除")] = False,
) -> None:
    """永久删除文档"""
    if not yes:
        typer.echo("永久删除需要 --yes 确认", err=True)
        raise typer.Exit(1)
    result = _run_client(lambda client: client.purge_document(doc_slug, confirm=True))
    _echo_mapping(result, ("slug", "status"))


@wiki_app.command("backends")
def list_backends() -> None:
    """列出已配置的后端"""
    backends = _run_client(lambda client: client.list_backends())
    for backend in backends:
        typer.echo(f"{backend['slug']} ({backend['type']})")


@wiki_app.command("docs")
def list_docs(
    kb_slug: Annotated[str, typer.Option("--kb", help="知识库标识")],
    backend: Annotated[str | None, typer.Option("--backend", help="后端标识筛选")] = None,
) -> None:
    """列出知识库中的文档"""
    docs = _run_client(lambda client: client.list_docs(kb_slug, backend=backend))
    for doc in docs:
        title = f" - {doc['title']}" if doc.get("title") else ""
        typer.echo(f"{doc['slug']}{title}")


@wiki_app.command("doc")
def get_doc(
    doc_slug: Annotated[str, typer.Argument(help="文档标识")],
    backend: Annotated[str | None, typer.Option("--backend", help="后端标识筛选")] = None,
) -> None:
    """查看文档详情"""
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


@wiki_app.command("status")
def status(
    backend: Annotated[str | None, typer.Option("--backend", help="后端标识筛选")] = None,
) -> None:
    """查看同步状态"""
    result = _run_client(lambda client: client.status(backend=backend))
    jobs = result.get("jobs", [])
    typer.echo(f"任务数: {len(jobs)}")
    for job in jobs:
        parts = [
            str(job.get("status", "")),
            str(job.get("operation", "")),
            str(job.get("backend_slug", "")),
            str(job.get("kb_slug", "")),
            str(job.get("doc_slug", "")),
        ]
        typer.echo(" ".join(part for part in parts if part))


@wiki_app.command("sync")
def sync(
    all_users: Annotated[bool, typer.Option("--all", help="同步所有用户的任务")] = False,
    backend: Annotated[str | None, typer.Option("--backend", help="后端标识筛选")] = None,
) -> None:
    """执行待处理的同步任务"""
    result = _run_client(lambda client: client.sync(all_users, backend=backend))
    typer.echo(f"已处理: {result.get('processed', 0)}")


@wiki_app.command("search")
def search(
    question: Annotated[str, typer.Argument(help="搜索查询")],
    kb_slug: Annotated[str, typer.Option("--kb", help="知识库标识")],
    backend: Annotated[str | None, typer.Option("--backend", help="后端标识筛选")] = None,
    top_k: Annotated[int, typer.Option("--top-k", help="返回结果数量")] = 6,
) -> None:
    """搜索知识库"""
    result = _run_client(lambda client: client.search(kb_slug, question, backend=backend, top_k=top_k))
    results = result.get("results", [])
    if not results:
        typer.echo("无结果")
        return
    for i, chunk in enumerate(results, 1):
        typer.echo(f"[{i}] {chunk.get('document_name', '')} (相似度: {chunk.get('similarity', 0):.2f})")
        content = chunk.get("content", "")
        preview = content[:200] + "..." if len(content) > 200 else content
        typer.echo(f"    {preview}")


@wiki_app.command("ask")
def ask(
    question: Annotated[str, typer.Argument(help="提问内容")],
    kb_slug: Annotated[str, typer.Option("--kb", help="知识库标识")],
    backend: Annotated[str | None, typer.Option("--backend", help="后端标识筛选")] = None,
    session: Annotated[str | None, typer.Option("--session", help="多轮会话 ID")] = None,
) -> None:
    """向知识库提问"""
    result = _run_client(lambda client: client.ask(kb_slug, question, backend=backend, session_id=session))
    typer.echo(result.get("answer", ""))
    if result.get("session_id"):
        typer.echo(f"session: {result['session_id']}")


# Register kb as nested sub-command under wiki
from agent_bridge.cli.knowledge import kb_app  # noqa: E402

wiki_app.add_typer(kb_app, name="kb")
