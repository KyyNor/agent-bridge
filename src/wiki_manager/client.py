"""HTTP client for wiki-manager."""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Any

import httpx


class WikiManagerClient:
    def __init__(self, base_url: str, linux_user: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.linux_user = linux_user

    @classmethod
    def from_config(cls) -> "WikiManagerClient":
        return cls(base_url="http://127.0.0.1:8765", linux_user=getpass.getuser())

    def _headers(self) -> dict[str, str]:
        return {"X-Wiki-User": self.linux_user}

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

    def init_system(self) -> None:
        response = httpx.post(f"{self.base_url}/admin/init", headers=self._headers(), timeout=10.0)
        self._raise(response)

    def list_kbs(self) -> list[dict[str, Any]]:
        response = httpx.get(f"{self.base_url}/kbs", headers=self._headers(), timeout=10.0)
        self._raise(response)
        return response.json()

    def create_kb(self, slug: str, name: str, description: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/kbs",
            json={"slug": slug, "name": name, "description": description},
            headers=self._headers(),
            timeout=10.0,
        )
        self._raise(response)
        return response.json()

    def grant_member(self, kb_slug: str, linux_user: str, role: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/kbs/{kb_slug}/members",
            json={"linux_user": linux_user, "role": role},
            headers=self._headers(),
            timeout=10.0,
        )
        self._raise(response)
        return response.json()

    def add_document(self, source: Path, kb_slugs: list[str], later: bool) -> dict[str, Any]:
        data = [("kb", kb) for kb in kb_slugs] + [("later", str(later).lower())]
        try:
            with source.open("rb") as handle:
                response = httpx.post(
                    f"{self.base_url}/docs",
                    data=data,
                    files={"file": (source.name, handle)},
                    headers=self._headers(),
                    timeout=60.0,
                )
        except OSError as exc:
            raise RuntimeError(f"cannot read source file: {exc}") from exc
        self._raise(response)
        return response.json()

    def update_document(self, doc_slug: str, source: Path, later: bool) -> dict[str, Any]:
        data = [("later", str(later).lower())]
        try:
            with source.open("rb") as handle:
                response = httpx.post(
                    f"{self.base_url}/docs/{doc_slug}/versions",
                    data=data,
                    files={"file": (source.name, handle)},
                    headers=self._headers(),
                    timeout=60.0,
                )
        except OSError as exc:
            raise RuntimeError(f"cannot read source file: {exc}") from exc
        self._raise(response)
        return response.json()

    def list_backends(self) -> list[dict[str, Any]]:
        response = httpx.get(f"{self.base_url}/backends", headers=self._headers(), timeout=10.0)
        self._raise(response)
        return response.json()

    def list_docs(self, kb_slug: str, backend: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {"kb": kb_slug}
        if backend:
            params["backend"] = backend
        response = httpx.get(f"{self.base_url}/docs", params=params, headers=self._headers(), timeout=10.0)
        self._raise(response)
        return response.json()

    def get_doc(self, doc_slug: str, backend: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if backend:
            params["backend"] = backend
        response = httpx.get(f"{self.base_url}/docs/{doc_slug}", params=params, headers=self._headers(), timeout=10.0)
        self._raise(response)
        return response.json()

    def delete_document(self, doc_slug: str) -> dict[str, Any]:
        response = httpx.post(f"{self.base_url}/docs/{doc_slug}/delete", headers=self._headers(), timeout=10.0)
        self._raise(response)
        return response.json()

    def purge_document(self, doc_slug: str, confirm: bool) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/docs/{doc_slug}/purge",
            json={"confirm": confirm},
            headers=self._headers(),
            timeout=10.0,
        )
        self._raise(response)
        return response.json()

    def status(self, backend: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if backend:
            params["backend"] = backend
        response = httpx.get(f"{self.base_url}/status", params=params, headers=self._headers(), timeout=10.0)
        self._raise(response)
        return response.json()

    def sync(self, all_users: bool = False, backend: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if backend:
            params["backend"] = backend
        response = httpx.post(
            f"{self.base_url}/sync",
            json={"all_users": all_users},
            params=params,
            headers=self._headers(),
            timeout=60.0,
        )
        self._raise(response)
        return response.json()

    def search(self, kb_slug: str, question: str, backend: str | None = None, top_k: int = 6) -> dict[str, Any]:
        params: dict[str, str] = {"kb": kb_slug, "q": question, "top_k": str(top_k)}
        if backend:
            params["backend"] = backend
        response = httpx.get(
            f"{self.base_url}/search",
            params=params,
            headers=self._headers(),
            timeout=30.0,
        )
        self._raise(response)
        return response.json()

    def ask(self, kb_slug: str, question: str, backend: str | None = None, session_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"kb": kb_slug, "question": question}
        if backend:
            payload["backend"] = backend
        if session_id:
            payload["session_id"] = session_id
        response = httpx.post(
            f"{self.base_url}/ask",
            json=payload,
            headers=self._headers(),
            timeout=60.0,
        )
        self._raise(response)
        return response.json()
