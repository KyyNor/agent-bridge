from __future__ import annotations

import inspect
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from agent_bridge.core.domain import AskResult, BackendDocStatus, RetrievalResult
from agent_bridge.knowledge_management.docs_knowledge.backends._office_convert import (
    SOFFICE_TARGETS,
    convert_via_soffice,
)

_DOCS_INDEX = "documents.json"
_KB_META = "kb.json"
_DIRECT_EXTENSIONS = {".pdf", ".md", ".markdown"}
# Formats markitdown can convert directly to Markdown.
_MARKITDOWN_EXTENSIONS = {
    ".csv",
    ".docx",
    ".htm",
    ".html",
    ".json",
    ".pptx",
    ".txt",
    ".xls",
    ".xlsx",
    ".xml",
}
# Legacy binary Office formats that must be pre-converted to their OOXML
# counterpart (via LibreOffice) before markitdown can read them.
_SOFFICE_PRE_EXTENSIONS = SOFFICE_TARGETS

def _default_pageindex_client_class():
    try:
        from pageindex import PageIndexClient
    except ImportError as exc:
        raise RuntimeError(
            "PageIndex backend requires the PageIndex SDK. Install the "
            "VectifyAI/PageIndex package in the agent-bridge runtime."
        ) from exc
    return PageIndexClient


def _default_markitdown_factory():
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise RuntimeError(
            "PageIndex backend requires MarkItDown for this file type. "
            "Install markitdown with the required format extras."
        ) from exc
    return MarkItDown(enable_plugins=False)


def _litellm_gateway_model(model: str | None, base_url: str | None) -> str | None:
    if not model:
        return model
    if "/" in model or not base_url:
        return model
    return f"openai/{model}"


def _has_markdown_heading(markdown: str) -> bool:
    return re.search(r"^#{1,6}\s+\S", markdown, flags=re.MULTILINE) is not None


def _ensure_markdown_heading(markdown: str, fallback_title: str) -> str:
    if _has_markdown_heading(markdown):
        return markdown
    title = fallback_title.strip() or "Document"
    body = markdown.strip()
    return f"# {title}\n\n{body}" if body else f"# {title}\n"


def _configure_pageindex_agent_runtime(api_key: str | None) -> None:
    try:
        from agents import (
            set_default_openai_api,
            set_default_openai_key,
            set_tracing_disabled,
        )
    except ImportError:
        return
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)
    if api_key:
        set_default_openai_key(api_key, use_for_tracing=False)


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text, flags=re.UNICODE)
        if token.strip()
    }


class _PageIndexCollectionClient:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def index(self, file_path: str, mode: str = "auto") -> str:
        return self._collection.add(file_path)

    def get_document_structure(self, doc_id: str) -> Any:
        return self._collection.get_document_structure(doc_id)

    def get_document(self, doc_id: str) -> Any:
        if hasattr(self._collection, "get_document"):
            return self._collection.get_document(doc_id)
        return {"doc_id": doc_id}

    def get_page_content(self, doc_id: str, pages: str) -> Any:
        if hasattr(self._collection, "get_page_content"):
            return self._collection.get_page_content(doc_id, pages)
        return self.get_document_structure(doc_id)

    def query(
        self,
        question: str,
        doc_ids: list[str] | None = None,
        stream: bool = False,
    ) -> str:
        return self._collection.query(question, doc_ids=doc_ids, stream=stream)

    def delete_document(self, doc_id: str) -> None:
        if hasattr(self._collection, "delete_document"):
            self._collection.delete_document(doc_id)


class PageIndexBackend:
    """Internal PageIndex adapter.

    The adapter stores PageIndex workspaces under ``root/<kb_slug>/workspace`` and
    keeps a small document index for Agent Bridge status, delete, and retrieval.
    """

    def __init__(
        self,
        root: Path,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        retrieve_model: str | None = None,
        client_class: type | None = None,
        markitdown_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key
        self.model = model
        self.retrieve_model = retrieve_model
        self._client_class = client_class
        self._markitdown_factory = markitdown_factory
        self._clients: dict[str, Any] = {}

    def create_kb(self, slug: str, name: str) -> str:
        kb_dir = self._kb_dir(slug)
        kb_dir.mkdir(parents=True, exist_ok=True)
        (kb_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (kb_dir / "converted").mkdir(parents=True, exist_ok=True)
        (kb_dir / _KB_META).write_text(
            json.dumps({"slug": slug, "name": name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not (kb_dir / _DOCS_INDEX).exists():
            self._save_docs(slug, {})
        return slug

    def delete_kb(self, backend_kb_id: str) -> None:
        shutil.rmtree(self._kb_dir(backend_kb_id), ignore_errors=True)
        self._clients.pop(backend_kb_id, None)

    def upload(
        self,
        backend_kb_id: str,
        doc_slug: str,
        file_path: Path,
        filename: str,
    ) -> str:
        if not file_path.exists():
            raise FileNotFoundError(f"file not found: {file_path}")
        self._ensure_kb(backend_kb_id)
        index_path, mode, source_type = self._prepare_index_file(
            backend_kb_id, doc_slug, file_path, filename
        )
        client = self._client(backend_kb_id)
        backend_doc_id = client.index(str(index_path), mode=mode)
        docs = self._load_docs(backend_kb_id)
        docs[backend_doc_id] = {
            "backend_doc_id": backend_doc_id,
            "doc_slug": doc_slug,
            "filename": filename,
            "indexed_path": str(index_path),
            "source_type": source_type,
            "status": "completed",
        }
        self._save_docs(backend_kb_id, docs)
        return backend_doc_id

    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
        docs = self._load_docs(backend_kb_id)
        docs.pop(backend_doc_id, None)
        self._save_docs(backend_kb_id, docs)
        client = self._clients.get(backend_kb_id)
        if client is not None and hasattr(client, "delete_document"):
            client.delete_document(backend_doc_id)
        elif client is not None and hasattr(client, "documents"):
            client.documents.pop(backend_doc_id, None)

    def get_status(
        self, backend_kb_id: str, backend_doc_id: str
    ) -> BackendDocStatus:
        doc = self._load_docs(backend_kb_id).get(backend_doc_id)
        if doc is None:
            return BackendDocStatus(status="not_found")
        status = str(doc.get("status") or "completed")
        return BackendDocStatus(
            status=status,
            chunk_count=doc.get("chunk_count"),
            progress=1.0 if status == "completed" else None,
            error_message=doc.get("error_message"),
        )

    def retrieve(
        self, backend_kb_id: str, question: str, top_k: int = 6
    ) -> list[RetrievalResult]:
        query_tokens = _tokens(question)
        if not query_tokens:
            return []
        client = self._client(backend_kb_id)
        scored: list[tuple[float, RetrievalResult]] = []
        for backend_doc_id, doc in self._load_docs(backend_kb_id).items():
            for index, content in enumerate(
                self._candidate_contents(client, backend_doc_id), start=1
            ):
                content_tokens = _tokens(content)
                overlap = query_tokens & content_tokens
                if not overlap:
                    continue
                similarity = len(overlap) / len(query_tokens)
                scored.append(
                    (
                        similarity,
                        RetrievalResult(
                            chunk_id=f"{backend_doc_id}:{index}",
                            content=content,
                            document_name=str(doc.get("filename") or backend_doc_id),
                            similarity=similarity,
                            dataset_id=backend_kb_id,
                        ),
                    )
                )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:top_k]]

    def ask(
        self,
        backend_kb_id: str,
        question: str,
        chat_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> tuple[AskResult, str]:
        client = self._client(backend_kb_id)
        docs = self._load_docs(backend_kb_id)
        chunks = self.retrieve(backend_kb_id, question, top_k=6)
        if not hasattr(client, "query"):
            raise RuntimeError(
                "PageIndex ask requires the official PageIndex query interface. "
                "Install a local PageIndex SDK version whose collection exposes query()."
            )
        self._configure_llm_env()
        _configure_pageindex_agent_runtime(self.api_key)
        answer = client.query(question, doc_ids=list(docs), stream=False)
        return AskResult(answer=answer, chunks=chunks, session_id=session_id), (chat_id or "")

    def _prepare_index_file(
        self,
        backend_kb_id: str,
        doc_slug: str,
        file_path: Path,
        filename: str,
    ) -> tuple[Path, str, str]:
        suffix = Path(filename).suffix.lower() or file_path.suffix.lower()
        if suffix == ".pdf":
            return file_path, "pdf", "pdf"
        if suffix in {".md", ".markdown"}:
            markdown = file_path.read_text(encoding="utf-8")
            normalized = _ensure_markdown_heading(markdown, Path(filename).stem or doc_slug)
            if normalized != markdown:
                converted_path = self._kb_dir(backend_kb_id) / "converted" / f"{doc_slug}.md"
                converted_path.parent.mkdir(parents=True, exist_ok=True)
                converted_path.write_text(normalized, encoding="utf-8")
                return converted_path, "md", "markdown:normalized"
            return file_path, "md", "markdown"
        if suffix in _SOFFICE_PRE_EXTENSIONS:
            # Legacy .doc/.ppt → OOXML via LibreOffice, then markitdown → Markdown.
            outdir = self._kb_dir(backend_kb_id) / "converted"
            converted_ooxml = convert_via_soffice(
                file_path, _SOFFICE_PRE_EXTENSIONS[suffix], outdir
            )
            converted_path = self._run_markitdown(
                backend_kb_id, doc_slug, converted_ooxml, Path(filename).stem
            )
            return converted_path, "md", f"soffice+markitdown:{suffix.removeprefix('.')}"
        if suffix in _MARKITDOWN_EXTENSIONS:
            converted_path = self._run_markitdown(
                backend_kb_id, doc_slug, file_path, Path(filename).stem
            )
            return converted_path, "md", f"markitdown:{suffix.removeprefix('.')}"
        supported = ", ".join(
            sorted(_DIRECT_EXTENSIONS | _MARKITDOWN_EXTENSIONS | set(_SOFFICE_PRE_EXTENSIONS))
        )
        raise ValueError(
            f"unsupported PageIndex file format: {suffix or '<none>'}; supported: {supported}"
        )

    def _run_markitdown(
        self,
        backend_kb_id: str,
        doc_slug: str,
        source: Path,
        fallback_title: str,
    ) -> Path:
        """Run markitdown on ``source`` and persist the Markdown output.

        Writes to ``<kb>/converted/<doc_slug>.md`` (creating the dir if
        needed) and returns that path. ``fallback_title`` is used to ensure
        the output has at least one top-level heading.
        """
        converter = (
            self._markitdown_factory()
            if self._markitdown_factory is not None
            else _default_markitdown_factory()
        )
        result = converter.convert_local(str(source))
        markdown = getattr(result, "text_content", "") or ""
        title = fallback_title.strip() or doc_slug
        markdown = _ensure_markdown_heading(markdown, title)
        converted_path = self._kb_dir(backend_kb_id) / "converted" / f"{doc_slug}.md"
        converted_path.parent.mkdir(parents=True, exist_ok=True)
        converted_path.write_text(markdown, encoding="utf-8")
        return converted_path

    def _candidate_contents(self, client: Any, backend_doc_id: str) -> list[str]:
        try:
            raw_structure = client.get_document_structure(backend_doc_id)
            structure = (
                json.loads(raw_structure)
                if isinstance(raw_structure, str)
                else raw_structure
            )
        except Exception:
            return []
        contents: list[str] = []
        for node in self._walk_nodes(structure):
            text = (
                node.get("text")
                or node.get("summary")
                or node.get("prefix_summary")
                or node.get("title")
                or ""
            )
            if text:
                contents.append(str(text))
        return contents

    def _walk_nodes(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            nodes = [value]
            for child in value.get("nodes") or []:
                nodes.extend(self._walk_nodes(child))
            return nodes
        if isinstance(value, list):
            nodes: list[dict[str, Any]] = []
            for item in value:
                nodes.extend(self._walk_nodes(item))
            return nodes
        return []

    def _client(self, backend_kb_id: str):
        if backend_kb_id not in self._clients:
            self._configure_llm_env()
            client_class = self._client_class or _default_pageindex_client_class()
            workspace = str(self._kb_dir(backend_kb_id) / "workspace")
            self._clients[backend_kb_id] = self._create_client(
                client_class,
                workspace=workspace,
            )
        return self._clients[backend_kb_id]

    def _create_client(self, client_class: type, *, workspace: str):
        try:
            parameters = inspect.signature(client_class).parameters
        except (TypeError, ValueError):
            parameters = inspect.signature(client_class.__init__).parameters

        if "workspace" in parameters:
            return client_class(
                api_key=self.api_key,
                model=self._model_for_litellm(self.model),
                retrieve_model=self._model_for_litellm(self.retrieve_model),
                workspace=workspace,
            )

        if "storage_path" in parameters:
            client = client_class(
                model=self._model_for_litellm(self.model),
                retrieve_model=self._model_for_litellm(self.retrieve_model),
                storage_path=workspace,
            )
            if hasattr(client, "collection"):
                return _PageIndexCollectionClient(client.collection("default"))
            return client

        raise RuntimeError(
            "incompatible PageIndex SDK: this backend requires the local PageIndex "
            "client interface (workspace/index or storage_path/collection). "
            "The installed pageindex package appears to be the cloud-only API SDK."
        )

    def _model_for_litellm(self, model: str | None) -> str | None:
        return _litellm_gateway_model(model, self.base_url)

    def _configure_llm_env(self) -> None:
        if self.api_key:
            os.environ["OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            os.environ["OPENAI_BASE_URL"] = self.base_url
            os.environ["OPENAI_API_BASE"] = self.base_url

    def _ensure_kb(self, backend_kb_id: str) -> None:
        kb_dir = self._kb_dir(backend_kb_id)
        kb_dir.mkdir(parents=True, exist_ok=True)
        (kb_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (kb_dir / "converted").mkdir(parents=True, exist_ok=True)
        if not (kb_dir / _DOCS_INDEX).exists():
            self._save_docs(backend_kb_id, {})

    def _kb_dir(self, backend_kb_id: str) -> Path:
        return self.root / backend_kb_id

    def _docs_path(self, backend_kb_id: str) -> Path:
        return self._kb_dir(backend_kb_id) / _DOCS_INDEX

    def _load_docs(self, backend_kb_id: str) -> dict[str, dict[str, Any]]:
        path = self._docs_path(backend_kb_id)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_docs(self, backend_kb_id: str, docs: dict[str, dict[str, Any]]) -> None:
        path = self._docs_path(backend_kb_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
