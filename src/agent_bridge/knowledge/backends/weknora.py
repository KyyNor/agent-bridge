from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from agent_bridge.core.domain import AskResult, BackendDocStatus, RetrievalResult

logger = logging.getLogger(__name__)

# Default parser engine rules — tell Weknora which engine to use per file type
_PARSER_ENGINE_RULES = [
    {"file_types": ["pdf"], "engine": "builtin"},
    {"file_types": ["docx", "doc"], "engine": "builtin"},
    {"file_types": ["pptx", "ppt"], "engine": "markitdown"},
    {"file_types": ["xlsx", "xls"], "engine": "builtin"},
    {"file_types": ["csv"], "engine": "simple"},
    {"file_types": ["md", "markdown"], "engine": "builtin"},
    {"file_types": ["txt"], "engine": "simple"},
    {"file_types": ["json"], "engine": "simple"},
    {"file_types": ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp"], "engine": "builtin"},
    {"file_types": ["mp3", "wav", "m4a", "flac", "ogg"], "engine": "simple"},
]


def _map_parse_status(status: str) -> str:
    mapping = {
        "pending": "pending",
        "processing": "parsing",
        "finalizing": "parsing",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "failed",
    }
    return mapping.get(status, status or "unknown")


class WeknoraBackend:
    """Adapter for Weknora's REST API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: int = 120,
        embedding_model_id: str | None = None,
        summary_model_id: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.embedding_model_id = embedding_model_id
        self.summary_model_id = summary_model_id
        self._model_name_to_id: dict[str, str] | None = None

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    def _fetch_models(self) -> dict[str, str]:
        """Fetch models from Weknora and return a name→UUID mapping. Cached after first call."""
        if self._model_name_to_id is not None:
            return self._model_name_to_id
        try:
            response = self._request("GET", "/api/v1/models")
            self._raise(response)
            models = self._data(response) or []
            self._model_name_to_id = {m["name"]: m["id"] for m in models if isinstance(m, dict) and m.get("name") and m.get("id")}
            logger.info("Fetched %d models from Weknora", len(self._model_name_to_id))
        except Exception:
            logger.warning("Failed to fetch models from Weknora, using raw IDs", exc_info=True)
            self._model_name_to_id = {}
        return self._model_name_to_id

    def _resolve_model_id(self, raw: str | None) -> str | None:
        """Resolve a model name or UUID to a UUID using Weknora's model list."""
        if not raw:
            return None
        models = self._fetch_models()
        if raw in models:
            return models[raw]
        return raw

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.pop("headers", None) or {}
        headers.update(self._headers())
        return httpx.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise RuntimeError(f"Weknora API error {response.status_code}: {response.text}")
        try:
            payload = response.json()
        except ValueError:
            return
        if isinstance(payload, dict) and payload.get("success") is False:
            message = payload.get("message") or payload.get("error") or response.text
            raise RuntimeError(f"Weknora API error: {message}")

    @staticmethod
    def _data(response: httpx.Response) -> Any:
        payload = response.json()
        return payload.get("data") if isinstance(payload, dict) else payload

    def create_kb(self, slug: str, name: str) -> str:
        embedding_id = self._resolve_model_id(self.embedding_model_id)
        summary_id = self._resolve_model_id(self.summary_model_id)
        body: dict[str, Any] = {
            "name": name,
            "description": slug,
            "type": "document",
            "chunking_config": {
                "chunk_size": 512,
                "chunk_overlap": 80,
                "separators": ["\n\n", "\n", "。", "！", "？", ";", "；"],
                "enable_parent_child": True,
                "parent_chunk_size": 4096,
                "child_chunk_size": 384,
                "strategy": "auto",
                "parser_engine_rules": _PARSER_ENGINE_RULES,
            },
            "embedding_model_id": embedding_id,
            "summary_model_id": summary_id,
            "storage_provider_config": {"provider": "local"},
            "indexing_strategy": {
                "vector_enabled": True,
                "keyword_enabled": True,
                "wiki_enabled": True,
                "graph_enabled": False,
            },
            "question_generation_config": {"enabled": True, "question_count": 3},
        }
        if summary_id:
            body["wiki_config"] = {
                "synthesis_model_id": summary_id,
                "max_pages_per_ingest": 0,
                "extraction_granularity": "standard",
            }
        response = self._request("POST", "/api/v1/knowledge-bases", json=body)
        self._raise(response)
        return self._data(response)["id"]

    def delete_kb(self, backend_kb_id: str) -> None:
        response = self._request("DELETE", f"/api/v1/knowledge-bases/{backend_kb_id}")
        if response.status_code == 404:
            return
        self._raise(response)

    def upload(
        self,
        backend_kb_id: str,
        doc_slug: str,
        file_path: Path,
        filename: str,
    ) -> str:
        with file_path.open("rb") as file_handle:
            response = self._request(
                "POST",
                f"/api/v1/knowledge-bases/{backend_kb_id}/knowledge/file",
                data={"fileName": filename, "channel": "api"},
                files={"file": (filename, file_handle)},
            )
        self._raise(response)
        return self._data(response)["id"]

    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
        response = self._request("DELETE", f"/api/v1/knowledge/{backend_doc_id}")
        if response.status_code == 404:
            return
        self._raise(response)

    def get_status(
        self, backend_kb_id: str, backend_doc_id: str
    ) -> BackendDocStatus:
        response = self._request("GET", f"/api/v1/knowledge/{backend_doc_id}")
        if response.status_code == 404:
            return BackendDocStatus(status="not_found")
        self._raise(response)
        data = self._data(response) or {}
        raw_status = data.get("parse_status", "pending")
        status = _map_parse_status(raw_status)
        progress = 1.0 if status == "completed" else None
        return BackendDocStatus(
            status=status,
            chunk_count=data.get("chunk_count"),
            progress=progress,
            error_message=data.get("error_message") or None,
        )

    def retrieve(
        self, backend_kb_id: str, question: str, top_k: int = 6
    ) -> list[RetrievalResult]:
        response = self._request(
            "POST",
            "/api/v1/knowledge-search",
            json={"query": question, "knowledge_base_id": backend_kb_id},
        )
        self._raise(response)
        items = self._data(response) or []
        return [
            self._retrieval_result_from_search_item(item, backend_kb_id)
            for item in items[:top_k]
        ]

    def ask(
        self,
        backend_kb_id: str,
        question: str,
        chat_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[AskResult, str]:
        if session_id is None:
            session_id = self._create_session()

        response = self._request(
            "POST",
            f"/api/v1/knowledge-chat/{session_id}",
            json={
                "query": question,
                "knowledge_base_ids": [backend_kb_id],
                "disable_title": True,
                "channel": "api",
            },
        )
        self._raise(response)
        answer, chunks = self._parse_sse_response(response.text, backend_kb_id)
        return AskResult(answer=answer, chunks=chunks, session_id=session_id), (chat_id or "")

    def _create_session(self) -> str:
        response = self._request(
            "POST",
            "/api/v1/sessions",
            json={"title": "agent-bridge", "description": "agent-bridge API session"},
        )
        self._raise(response)
        return self._data(response)["id"]

    @staticmethod
    def _retrieval_result_from_search_item(
        item: dict[str, Any], backend_kb_id: str
    ) -> RetrievalResult:
        return RetrievalResult(
            chunk_id=str(item.get("id", "")),
            content=str(item.get("content", "")),
            document_name=str(
                item.get("knowledge_title")
                or item.get("knowledge_filename")
                or ""
            ),
            similarity=float(item.get("score") or 0.0),
            dataset_id=str(item.get("knowledge_base_id") or backend_kb_id),
        )

    @classmethod
    def _retrieval_result_from_reference(
        cls, item: dict[str, Any], backend_kb_id: str
    ) -> RetrievalResult:
        return RetrievalResult(
            chunk_id=str(item.get("id", "")),
            content=str(item.get("content", "")),
            document_name=str(
                item.get("knowledge_title")
                or item.get("knowledge_filename")
                or ""
            ),
            similarity=float(item.get("score") or item.get("similarity") or 0.0),
            dataset_id=str(item.get("knowledge_base_id") or backend_kb_id),
        )

    @classmethod
    def _parse_sse_response(
        cls, text: str, backend_kb_id: str
    ) -> tuple[str, list[RetrievalResult]]:
        answer_parts: list[str] = []
        chunks: list[RetrievalResult] = []

        for event in text.split("\n\n"):
            data_lines = [
                line.removeprefix("data:").strip()
                for line in event.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue

            response_type = payload.get("response_type")
            if response_type == "error":
                message = payload.get("content") or payload.get("error") or "Weknora SSE error"
                raise RuntimeError(str(message))
            if response_type == "references":
                for ref in payload.get("knowledge_references") or []:
                    chunks.append(cls._retrieval_result_from_reference(ref, backend_kb_id))
            if response_type == "answer":
                answer_parts.append(str(payload.get("content") or ""))

        return "".join(answer_parts), chunks
