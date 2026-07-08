"""HTTP client for Agent Bridge."""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Any

import httpx

from agent_bridge.core.config import default_user


class AgentBridgeClient:
    def __init__(self, base_url: str, linux_user: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.linux_user = linux_user

    @classmethod
    def from_config(cls) -> "AgentBridgeClient":
        return cls(base_url="http://127.0.0.1:8765", linux_user=default_user(getpass.getuser()))

    def _headers(self) -> dict[str, str]:
        return {"X-Agent-Bridge-User": self.linux_user}

    def _raise(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            payload = response.json()
        except ValueError:
            detail = response.text
        else:
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        raise RuntimeError(str(detail))

    def _request(self, method: str, path: str, *, timeout: float = 10.0, **kwargs: Any) -> httpx.Response:
        response = getattr(httpx, method.lower())(
            f"{self.base_url}{path}", headers=self._headers(), timeout=timeout, **kwargs,
        )
        self._raise(response)
        return response

    def init_system(self) -> None:
        self._request("POST", "/admin/init")

    def list_kbs(self) -> list[dict[str, Any]]:
        return self._request("GET", "/kbs").json()

    def create_kb(self, slug: str, name: str, description: str) -> dict[str, Any]:
        return self._request(
            "POST", "/kbs", json={"slug": slug, "name": name, "description": description},
        ).json()

    def grant_member(self, kb_slug: str, linux_user: str, role: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/kbs/{kb_slug}/members", json={"linux_user": linux_user, "role": role},
        ).json()

    def add_document(self, source: Path, kb_slugs: list[str], later: bool) -> dict[str, Any]:
        data = [("kb", kb) for kb in kb_slugs] + [("later", str(later).lower())]
        try:
            with source.open("rb") as handle:
                return self._request(
                    "POST", "/docs", data=data, files={"file": (source.name, handle)}, timeout=60.0,
                ).json()
        except OSError as exc:
            raise RuntimeError(f"cannot read source file: {exc}") from exc

    def update_document(self, doc_slug: str, source: Path, later: bool) -> dict[str, Any]:
        data = [("later", str(later).lower())]
        try:
            with source.open("rb") as handle:
                return self._request(
                    "POST", f"/docs/{doc_slug}/versions", data=data, files={"file": (source.name, handle)}, timeout=60.0,
                ).json()
        except OSError as exc:
            raise RuntimeError(f"cannot read source file: {exc}") from exc

    def list_backends(self) -> list[dict[str, Any]]:
        return self._request("GET", "/backends").json()

    def list_docs(self, kb_slug: str, backend: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {"kb": kb_slug}
        if backend:
            params["backend"] = backend
        return self._request("GET", "/docs", params=params).json()

    def get_doc(self, doc_slug: str, backend: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if backend:
            params["backend"] = backend
        return self._request("GET", f"/docs/{doc_slug}", params=params).json()

    def delete_document(self, doc_slug: str) -> dict[str, Any]:
        return self._request("POST", f"/docs/{doc_slug}/delete").json()

    def purge_document(self, doc_slug: str, confirm: bool) -> dict[str, Any]:
        return self._request("POST", f"/docs/{doc_slug}/purge", json={"confirm": confirm}).json()

    def status(self, backend: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if backend:
            params["backend"] = backend
        return self._request("GET", "/status", params=params).json()

    def sync(self, all_users: bool = False, backend: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if backend:
            params["backend"] = backend
        return self._request("POST", "/sync", json={"all_users": all_users}, params=params, timeout=60.0).json()

    def search(self, kb_slug: str, question: str, backend: str | None = None, top_k: int = 6) -> dict[str, Any]:
        params: dict[str, str] = {"kb": kb_slug, "q": question, "top_k": str(top_k)}
        if backend:
            params["backend"] = backend
        return self._request("GET", "/search", params=params, timeout=30.0).json()

    def ask(self, kb_slug: str, question: str, backend: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"kb": kb_slug, "question": question}
        if backend:
            payload["backend"] = backend
        if session_id:
            payload["session_id"] = session_id
        return self._request("POST", "/ask", json=payload, timeout=60.0).json()

    def upsert_profile(self, profile_key: str, name: str, description: str, status: str) -> dict[str, Any]:
        return self._request(
            "POST", "/capability-profiles",
            json={"profile_key": profile_key, "name": name, "description": description, "status": status},
        ).json()

    def list_profiles(self) -> list[dict[str, Any]]:
        return self._request("GET", "/capability-profiles").json()

    def get_profile(self, profile_key: str) -> dict[str, Any]:
        return self._request("GET", f"/capability-profiles/{profile_key}").json()

    def render_profile_doc(self, profile_key: str) -> dict[str, Any]:
        return self._request("POST", f"/capability-profiles/{profile_key}/doc/render").json()

    def refresh_profile_doc_context_file(self, profile_key: str) -> dict[str, Any]:
        return self._request("POST", f"/capability-profiles/{profile_key}/doc/context-file").json()

    def list_memory_blocks(self) -> list[dict[str, Any]]:
        return self._request("GET", "/memory/blocks").json()

    def create_memory_block(self, block_key: str, name: str, description: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/memory/blocks",
            json={"block_key": block_key, "name": name, "description": description},
        ).json()

    def get_profile_memory(self, profile_key: str) -> dict[str, Any]:
        return self._request("GET", f"/capability-profiles/{profile_key}/memory").json()

    def set_profile_memory(self, profile_key: str, block_key: str | None, enabled: bool = True) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/capability-profiles/{profile_key}/memory",
            json={"block_key": block_key, "enabled": enabled},
        ).json()

    def post_memory_hook(self, action: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        return self._request("POST", f"/memory/hooks/claude-code/{action}", json=payload, timeout=timeout).json()

    def refresh_profile_pin_cache(self, profile_key: str) -> dict[str, Any]:
        return self._request("POST", f"/capability-profiles/{profile_key}/pins/refresh").json()

    def replace_profile_rules(self, profile_key: str, rules: list[dict[str, str]]) -> dict[str, Any]:
        return self._request("PUT", f"/capability-profiles/{profile_key}/rules", json={"rules": rules}).json()
