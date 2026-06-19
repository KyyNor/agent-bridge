from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from agent_bridge.core.domain import AskResult, BackendDocStatus, RetrievalResult


_DOCS_INDEX = "documents.json"
_KB_META = "kb.json"
_DIRECT_EXTENSIONS = {".pdf", ".md", ".markdown"}
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


def _default_completion(model: str | None, prompt: str) -> str:
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError(
            "PageIndex ask requires litellm. Configure litellm for the internal LLM gateway."
        ) from exc
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return response.choices[0].message.content or ""


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text, flags=re.UNICODE)
        if token.strip()
    }


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
        completion: Callable[[str | None, str], str] | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key
        self.model = model
        self.retrieve_model = retrieve_model
        self._client_class = client_class
        self._markitdown_factory = markitdown_factory
        self._completion = completion or _default_completion
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
        if client is not None and hasattr(client, "documents"):
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
        chunks = self.retrieve(backend_kb_id, question, top_k=6)
        context = "\n\n".join(
            f"[{index}] {chunk.document_name}\n{chunk.content}"
            for index, chunk in enumerate(chunks, start=1)
        )
        prompt = (
            "Answer the question using only the provided PageIndex context. "
            "If the context is insufficient, say so.\n\n"
            f"Question:\n{question}\n\nContext:\n{context}"
        )
        self._configure_llm_env()
        answer = self._completion(self.retrieve_model or self.model, prompt)
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
            return file_path, "md", "markdown"
        if suffix in _MARKITDOWN_EXTENSIONS:
            converter = (
                self._markitdown_factory()
                if self._markitdown_factory is not None
                else _default_markitdown_factory()
            )
            result = converter.convert_local(str(file_path))
            markdown = getattr(result, "text_content", "")
            converted_path = self._kb_dir(backend_kb_id) / "converted" / f"{doc_slug}.md"
            converted_path.parent.mkdir(parents=True, exist_ok=True)
            converted_path.write_text(markdown, encoding="utf-8")
            return converted_path, "md", f"markitdown:{suffix.removeprefix('.')}"
        supported = ", ".join(sorted(_DIRECT_EXTENSIONS | _MARKITDOWN_EXTENSIONS))
        raise ValueError(
            f"unsupported PageIndex file format: {suffix or '<none>'}; supported: {supported}"
        )

    def _candidate_contents(self, client: Any, backend_doc_id: str) -> list[str]:
        try:
            raw_structure = client.get_document_structure(backend_doc_id)
            structure = json.loads(raw_structure)
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
            self._clients[backend_kb_id] = client_class(
                api_key=self.api_key,
                model=self.model,
                retrieve_model=self.retrieve_model,
                workspace=str(self._kb_dir(backend_kb_id) / "workspace"),
            )
        return self._clients[backend_kb_id]

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
