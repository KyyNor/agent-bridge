from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from agent_bridge.core.domain import AskResult, BackendCapabilities, BackendDocStatus, RetrievalResult

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
        rerank_model_id: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.embedding_model_id = embedding_model_id
        self.summary_model_id = summary_model_id
        self.rerank_model_id = rerank_model_id
        self._model_name_to_id: dict[str, str] | None = None
        self._hybrid_agent_id: str | None = None

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
            logger.info("从 Weknora 获取了 %d 个模型", len(self._model_name_to_id))
        except Exception:
            logger.warning("从 Weknora 获取模型失败，使用原始 ID", exc_info=True)
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

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(supports_folders=True)

    @staticmethod
    def _normalise_remote_path(path: str | None) -> str:
        raw = (path or "").replace("\\", "/")
        parts: list[str] = []
        for part in raw.split("/"):
            if not part or part == ".":
                continue
            if part == ".." or any(ord(char) < 32 or ord(char) == 127 for char in part):
                raise ValueError("remote path is invalid")
            parts.append(part)
        return "/".join(parts)

    @classmethod
    def _remote_filename(cls, filename: str, remote_path: str | None) -> str:
        filename_path = cls._normalise_remote_path(remote_path or filename)
        if not filename_path:
            raise ValueError("filename is required")
        return filename_path

    def upload(
        self,
        backend_kb_id: str,
        doc_slug: str,
        file_path: Path,
        filename: str,
        remote_path: str | None = None,
    ) -> str:
        remote_filename = self._remote_filename(filename, remote_path)
        with file_path.open("rb") as file_handle:
            response = self._request(
                "POST",
                f"/api/v1/knowledge-bases/{backend_kb_id}/knowledge/file",
                data={"fileName": remote_filename, "channel": "api"},
                files={"file": (remote_filename, file_handle)},
            )
        self._raise(response)
        return self._data(response)["id"]

    def move(
        self,
        backend_kb_id: str,
        backend_doc_id: str,
        file_path: Path,
        filename: str,
        remote_path: str | None = None,
    ) -> str:
        self.delete(backend_kb_id, backend_doc_id)
        return self.upload(
            backend_kb_id=backend_kb_id,
            doc_slug=backend_doc_id,
            file_path=file_path,
            filename=filename,
            remote_path=remote_path,
        )

    def relocate(
        self,
        backend_kb_id: str,
        backend_doc_id: str,
        file_path: Path,
        filename: str,
        remote_path: str | None = None,
    ) -> str:
        return self.move(
            backend_kb_id,
            backend_doc_id,
            file_path,
            filename,
            remote_path,
        )

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
            self._retrieval_result(item, backend_kb_id)
            for item in items[:top_k]
        ]

    def ask(
        self,
        backend_kb_id: str,
        question: str,
        chat_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> tuple[AskResult, str]:
        if session_id is None:
            session_id = self._create_session()

        endpoint, body = self._build_chat_request(session_id, backend_kb_id, question, agent_id)
        try:
            answer, chunks = self._do_chat(endpoint, body, backend_kb_id)
        except RuntimeError as exc:
            if not (agent_id and self._is_missing_model_error(exc)):
                raise
            # Self-heal attempt: fill chat/rerank model from backend config, retry once.
            healed = False
            try:
                healed = self.ensure_agent_models(agent_id)
            except Exception:
                logger.warning("agent '%s' 模型自愈失败", agent_id, exc_info=True)
            if healed:
                # Retry once. If it STILL fails on a missing-model error (e.g. we
                # filled chat but rerank is still unset in backend config), surface
                # a friendly, actionable error rather than the raw SSE message.
                try:
                    answer, chunks = self._do_chat(endpoint, body, backend_kb_id)
                except RuntimeError as retry_exc:
                    if self._is_missing_model_error(retry_exc):
                        raise self._friendly_missing_model_error(retry_exc) from retry_exc
                    raise
            else:
                # Couldn't heal at all (backend has neither summary nor rerank configured).
                raise self._friendly_missing_model_error(exc) from exc
        return AskResult(answer=answer, chunks=chunks, session_id=session_id), (chat_id or "")

    def _build_chat_request(
        self, session_id: str, backend_kb_id: str, question: str, agent_id: str | None
    ) -> tuple[str, dict[str, Any]]:
        if agent_id is not None:
            endpoint = f"/api/v1/agent-chat/{session_id}"
            body: dict[str, Any] = {
                "query": question,
                "agent_enabled": True,
                "agent_id": agent_id,
                "knowledge_base_ids": [backend_kb_id],
                "web_search_enabled": False,
                "channel": "api",
            }
        else:
            endpoint = f"/api/v1/knowledge-chat/{session_id}"
            body = {
                "query": question,
                "knowledge_base_ids": [backend_kb_id],
                "disable_title": True,
                "channel": "api",
            }
        return endpoint, body

    def _do_chat(self, endpoint: str, body: dict[str, Any], backend_kb_id: str) -> tuple[str, list[RetrievalResult]]:
        """Stream the chat SSE response and parse incrementally.

        Weknora keeps the connection open even after emitting a terminal
        event (error or done), so reading ``response.text`` blocks until the
        httpx timeout. We stream instead and break on ``response_type=error``
        (surfacing the real error in ~1s instead of 120s, which unblocks the
        ask() self-heal path) or on ``response_type=answer`` with ``done=true``
        (the final answer chunk). Other ``done=true`` events (e.g. the initial
        ``agent_query`` ack) do NOT terminate the stream.
        """
        answer_parts: list[str] = []
        chunks: list[RetrievalResult] = []
        buffer = ""

        with self._request_stream("POST", endpoint, json=body) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"Weknora API error {response.status_code}")
            for chunk in response.iter_text():
                if not chunk:
                    continue
                buffer += chunk
                # SSE events are separated by a blank line.
                while "\n\n" in buffer:
                    raw_event, buffer = buffer.split("\n\n", 1)
                    message, is_terminal, is_error = self._parse_one_sse_event(
                        raw_event, backend_kb_id, answer_parts, chunks
                    )
                    if is_error:
                        raise RuntimeError(message)
                    if is_terminal:
                        return "".join(answer_parts), chunks
        # Stream ended without a terminal event — parse whatever remains.
        if buffer.strip():
            message, is_terminal, is_error = self._parse_one_sse_event(
                buffer, backend_kb_id, answer_parts, chunks
            )
            if is_error:
                raise RuntimeError(message)
        return "".join(answer_parts), chunks

    def _request_stream(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Like _request but returns a streaming response (caller uses `with`)."""
        headers = kwargs.pop("headers", None) or {}
        headers.update(self._headers())
        return httpx.stream(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )

    @classmethod
    def _parse_one_sse_event(
        cls, raw_event: str, backend_kb_id: str,
        answer_parts: list[str], chunks: list[RetrievalResult],
    ) -> tuple[str, bool, bool]:
        """Parse one SSE event into the accumulator lists.

        Returns ``(error_message_or_empty, is_terminal, is_error)``.
        ``is_terminal`` is True for error events and for the final answer
        chunk (``response_type=answer`` with ``done=true``). Other events
        (references, acks, intermediate answer deltas) do not terminate.
        """
        data_lines = [
            line.removeprefix("data:").strip()
            for line in raw_event.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            return "", False, False
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return "", False, False

        response_type = payload.get("response_type")
        done = bool(payload.get("done"))
        if response_type == "error":
            message = payload.get("content") or payload.get("error") or "Weknora SSE error"
            return str(message), True, True
        if response_type == "references":
            for ref in payload.get("knowledge_references") or []:
                chunks.append(cls._retrieval_result(ref, backend_kb_id, include_similarity_fallback=True))
            return "", False, False
        if response_type == "answer":
            answer_parts.append(str(payload.get("content") or ""))
            # A terminal answer chunk signals end-of-stream.
            return "", done, False
        # agent_query acks and other event types are non-terminal.
        return "", False, False

    @staticmethod
    def _is_missing_model_error(exc: Exception) -> bool:
        """True when Weknora reports the agent's model fields aren't configured.

        Matches both ``set model_id`` and ``set rerank_model_id`` on the agent.
        When rerank can't be self-healed (backend has no rerank_model_id configured),
        :meth:`_friendly_missing_model_error` rewrites this into a clear hint.
        """
        message = str(exc).lower()
        return ("model_id" in message and "not configured" in message) or (
            "rerank_model_id" in message and "not configured" in message
        )

    def _friendly_missing_model_error(self, exc: Exception) -> RuntimeError:
        """Turn Weknora's terse config error into an actionable message.

        - missing chat model    → tell user to set summary_model_id on the backend
        - missing rerank model  → tell user to set rerank_model_id on the backend
        """
        message = str(exc).lower()
        if "rerank_model_id" in message:
            hint = (
                "Weknora agent 报错：未配置 rerank 模型。"
                "请在系统配置里给该 backend 填上 rerank_model_id 后重试。"
            )
        elif "model_id" in message:
            hint = (
                "Weknora agent 报错：未配置 chat 模型。"
                "请在系统配置里给该 backend 填上 summary_model_id（LLM 模型）后重试。"
            )
        else:
            return RuntimeError(str(exc))
        return RuntimeError(hint)

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents from Weknora."""
        response = self._request("GET", "/api/v1/agents")
        self._raise(response)
        return self._data(response) or []

    def get_type_presets(self) -> list[dict[str, Any]]:
        """Get agent type presets from Weknora."""
        response = self._request("GET", "/api/v1/agents/type-presets")
        self._raise(response)
        return self._data(response) or []

    def create_agent(self, name: str, preset_config: dict[str, Any]) -> dict[str, Any]:
        """Create a new agent in Weknora from a type preset."""
        i18n = preset_config.get("i18n", {})
        zh_cn = i18n.get("zh-CN", {}) if isinstance(i18n, dict) else {}
        body = {
            "name": name,
            "description": zh_cn.get("description", ""),
            "is_builtin": False,
            "config": preset_config.get("config", {}),
        }
        response = self._request("POST", "/api/v1/agents", json=body)
        self._raise(response)
        return self._data(response)

    def ensure_hybrid_agent(self) -> str:
        """Ensure a hybrid-rag-wiki agent exists, creating one if needed. Returns the agent ID."""
        if self._hybrid_agent_id is not None:
            return self._hybrid_agent_id

        agents = self.list_agents()
        for agent in agents:
            config = agent.get("config", {})
            if config.get("agent_type") == "hybrid-rag-wiki" or config.get("system_prompt_id") == "hybrid_rag_wiki_agent":
                self._hybrid_agent_id = agent["id"]
                return self._hybrid_agent_id

        presets = self.get_type_presets()
        preset = next((p for p in presets if p.get("id") == "hybrid-rag-wiki"), None)
        if preset is None:
            raise RuntimeError("hybrid-rag-wiki preset not found in Weknora type presets")

        created = self.create_agent("AgentBridge混合智能体", preset)
        self._hybrid_agent_id = created["id"]
        return self._hybrid_agent_id

    def _get_agent(self, agent_id: str) -> dict[str, Any]:
        """GET a single agent's full payload (including config)."""
        response = self._request("GET", f"/api/v1/agents/{agent_id}")
        self._raise(response)
        return self._data(response) or {}

    def _update_agent(self, agent_id: str, agent_payload: dict[str, Any]) -> dict[str, Any]:
        """PUT (full overwrite) an agent. Caller must GET first and merge changes.

        Weknora's PUT replaces the entire resource — sending a partial body
        silently nulls every omitted field (we learned this the hard way).
        """
        response = self._request("PUT", f"/api/v1/agents/{agent_id}", json=agent_payload)
        self._raise(response)
        return self._data(response) or {}

    def ensure_agent_models(self, agent_id: str) -> bool:
        """Fill empty model_id / rerank_model_id on an agent from backend config.

        Returns True if the agent was patched, False if nothing changed.
        Skips silently when the backend has no corresponding model configured
        (e.g. rerank_model_id unset) — the caller decides how to surface that.
        """
        agent = self._get_agent(agent_id)
        if not agent:
            return False
        config: dict[str, Any] = dict(agent.get("config") or {})
        changed = False

        if not config.get("model_id") and self.summary_model_id:
            resolved = self._resolve_model_id(self.summary_model_id)
            if resolved:
                config["model_id"] = resolved
                changed = True

        if not config.get("rerank_model_id") and self.rerank_model_id:
            resolved_rerank = self._resolve_model_id(self.rerank_model_id)
            if resolved_rerank:
                config["rerank_model_id"] = resolved_rerank
                changed = True

        if not changed:
            return False

        # Preserve every top-level field — PUT is a full overwrite.
        patched = dict(agent)
        patched["config"] = config
        self._update_agent(agent_id, patched)
        logger.info("agent '%s' 缺失模型已自动补全 (model_id/rerank_model_id)", agent_id)
        return True

    def ensure_all_agent_models(self) -> int:
        """Run ensure_agent_models across every agent. Returns patch count.

        Best-effort: a single agent failure does not abort the rest.
        """
        try:
            agents = self.list_agents()
        except Exception:
            logger.warning("列出 agent 失败，跳过模型自愈", exc_info=True)
            return 0
        patched = 0
        for agent in agents:
            agent_id = agent.get("id") if isinstance(agent, dict) else None
            if not agent_id:
                continue
            try:
                if self.ensure_agent_models(agent_id):
                    patched += 1
            except Exception:
                logger.warning("agent '%s' 模型自愈失败", agent_id, exc_info=True)
        return patched

    def _create_session(self) -> str:
        response = self._request(
            "POST",
            "/api/v1/sessions",
            json={"title": "agent-bridge", "description": "agent-bridge API session"},
        )
        self._raise(response)
        return self._data(response)["id"]

    @staticmethod
    def _retrieval_result(
        item: dict[str, Any], backend_kb_id: str, *, include_similarity_fallback: bool = False
    ) -> RetrievalResult:
        score = item.get("score")
        if not score and include_similarity_fallback:
            score = item.get("similarity")
        return RetrievalResult(
            chunk_id=str(item.get("id", "")),
            content=str(item.get("content", "")),
            document_name=str(
                item.get("knowledge_title")
                or item.get("knowledge_filename")
                or ""
            ),
            similarity=float(score or 0.0),
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
                    chunks.append(cls._retrieval_result(ref, backend_kb_id, include_similarity_fallback=True))
            if response_type == "answer":
                answer_parts.append(str(payload.get("content") or ""))

        return "".join(answer_parts), chunks
