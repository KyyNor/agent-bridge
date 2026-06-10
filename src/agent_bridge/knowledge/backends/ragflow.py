from __future__ import annotations

import base64
import uuid
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from agent_bridge.core.domain import AskResult, BackendDocStatus, RetrievalResult

# Default RagFlow RSA public key (from /ragflow/conf/public.pem inside the
# Docker container).  Kept as a module constant so callers do not need to
# extract it from the container at runtime.  If the deployment uses a
# different key, pass it explicitly via the *public_key_pem* parameter.
_DEFAULT_PUBLIC_KEY_PEM = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArq9XTUSeYr2+N1h3Afl/
z8Dse/2yD0ZGrKwx+EEEcdsBLca9Ynmx3nIB5obmLlSfmskLpBo0UACBmB5rEjBp
2Q2f3AG3Hjd4B+gNCG6BDaawuDlgANIhGnaTLrIqWrrcm4EMzJOnAOI1fgzJRsOO
UEfaS318Eq9OVO3apEyCCt0lOQK6PuksduOjVxtltDav+guVAA068NrPYmRNabVK
RNLJpL8w4D44sfth5RvZ3q9t+6RTArpEtc5sh5ChzvqPOzKGMXW83C95TxmXqpbK
6olN4RevSfVjEAgCydH6HN6OhtOQEcnrU97r9H0iZOWwbw3pVrZiUkuRD1R56Wzs
2wIDAQAB
-----END PUBLIC KEY-----"""


def _rsa_encrypt(password: str, public_key_pem: str) -> str:
    """Encrypt *password* with the RSA public key and return base64-encoded ciphertext."""
    public_key = load_pem_public_key(public_key_pem.encode())
    b64_password = base64.b64encode(password.encode()).decode()
    encrypted = public_key.encrypt(b64_password.encode(), asym_padding.PKCS1v15())
    return base64.b64encode(encrypted).decode()


def _map_run_to_status(run: str) -> str:
    """Map RagFlow's ``run`` field to a simplified status string."""
    mapping = {
        "UNSTART": "pending",
        "RUNNING": "parsing",
        "CANCEL": "cancelled",
        "DONE": "completed",
        "FAIL": "error",
    }
    return mapping.get(run, run.lower())


class RagFlowBackend:
    """Adapter for RagFlow's REST API.

    Authentication modes (mutually exclusive):

    * **api_key** -- classic ``Authorization: Bearer <token>`` header.
      Pass an API key (created via ``POST /api/v1/system/tokens``).

    * **email / password** -- session-based authentication.  The adapter
      logs in on first use, obtains a session cookie, and reuses the
      ``httpx.Client`` for all subsequent requests.  The password is
      RSA-encrypted with the server's public key before transmission.

    At least one auth method must be provided.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        email: str | None = None,
        password: str | None = None,
        public_key_pem: str | None = None,
        timeout: int = 120,
    ) -> None:
        if not api_key and not (email and password):
            raise ValueError("Provide either api_key or both email and password")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.email = email
        self.password = password
        self.public_key_pem = public_key_pem or _DEFAULT_PUBLIC_KEY_PEM
        self.timeout = timeout

        # Lazy-initialised for session auth.
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Return headers for Bearer-token auth (used when api_key is set)."""
        return {"Authorization": f"Bearer {self.api_key}"}

    def _get_client(self) -> httpx.Client:
        """Return an ``httpx.Client`` with a valid session cookie.

        On the first call this performs the RSA-encrypted login and stores
        the client for reuse.
        """
        if self._client is not None:
            return self._client

        if not self.email or not self.password:
            raise RuntimeError("Session auth requires email and password")

        client = httpx.Client(timeout=self.timeout)
        encrypted_pw = _rsa_encrypt(self.password, self.public_key_pem)
        resp = client.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": self.email, "password": encrypted_pw},
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"RagFlow login failed {resp.status_code}: {resp.text}"
            )
        self._client = client
        return self._client

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Issue a request using Bearer token (if available) or session cookies."""
        if self.api_key:
            headers = kwargs.pop("headers", None) or {}
            headers.update(self._headers())
            return httpx.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs  # type: ignore[arg-type]
            )
        client = self._get_client()
        return client.request(method, url, **kwargs)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise RuntimeError(
                f"RagFlow API error {response.status_code}: {response.text}"
            )
        try:
            payload = response.json()
        except ValueError:
            return
        if isinstance(payload, dict) and payload.get("code") not in (None, 0):
            message = payload.get("message") or payload.get("error") or response.text
            raise RuntimeError(f"RagFlow API error {payload.get('code')}: {message}")

    # ------------------------------------------------------------------
    # Public API (BackendAdapter protocol)
    # ------------------------------------------------------------------

    def create_kb(self, slug: str, name: str) -> str:
        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/datasets",
            json={"name": slug},
        )
        self._raise(response)
        return response.json()["data"]["id"]

    def delete_kb(self, backend_kb_id: str) -> None:
        response = self._request(
            "DELETE",
            f"{self.base_url}/api/v1/datasets",
            json={"ids": [backend_kb_id]},
        )
        self._raise(response)

    def upload(
        self,
        backend_kb_id: str,
        doc_slug: str,
        file_path: Path,
        filename: str,
    ) -> str:
        with file_path.open("rb") as f:
            response = self._request(
                "POST",
                f"{self.base_url}/api/v1/datasets/{backend_kb_id}/documents",
                files={"file": (filename, f)},
            )
        self._raise(response)
        data = response.json()["data"]
        # RagFlow returns a list of uploaded documents.
        if isinstance(data, list):
            return data[0]["id"]
        return data["id"]

    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
        response = self._request(
            "DELETE",
            f"{self.base_url}/api/v1/datasets/{backend_kb_id}/documents",
            json={"ids": [backend_doc_id]},
        )
        self._raise(response)

    def get_status(
        self, backend_kb_id: str, backend_doc_id: str
    ) -> BackendDocStatus:
        # RagFlow v0.25+ returns the raw file content from
        # GET /api/v1/documents/{id}.  The correct metadata endpoint is the
        # dataset-scoped document list with an id filter.
        response = self._request(
            "GET",
            f"{self.base_url}/api/v1/datasets/{backend_kb_id}/documents",
            params={"id": backend_doc_id},
        )
        self._raise(response)
        docs = response.json()["data"]["docs"]
        if not docs:
            raise RuntimeError(f"Document {backend_doc_id} not found")
        data = docs[0]
        # RagFlow uses "run" (UNSTART / RUNNING / CANCEL / DONE / FAIL)
        # rather than "status" for processing state.
        run = data.get("run", "UNSTART")
        status = _map_run_to_status(run)
        return BackendDocStatus(
            status=status,
            chunk_count=data.get("chunk_count"),
            progress=data.get("progress"),
            error_message=data.get("error_message"),
        )

    def retrieve(
        self, backend_kb_id: str, question: str, top_k: int = 6
    ) -> list[RetrievalResult]:
        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/retrieval",
            json={
                "question": question,
                "dataset_ids": [backend_kb_id],
                "top_k": top_k,
            },
        )
        self._raise(response)
        chunks = response.json()["data"]["chunks"]
        return [
            RetrievalResult(
                chunk_id=c["id"],
                content=c["content"],
                document_name=c.get("document_keyword", ""),
                similarity=c.get("similarity", 0.0),
                dataset_id=c.get("dataset_id", ""),
            )
            for c in chunks
        ]

    def ask(
        self,
        backend_kb_id: str,
        question: str,
        chat_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> tuple[AskResult, str]:
        if chat_id is None:
            chat_id = self._create_chat_assistant(backend_kb_id)

        if session_id is None:
            session_id = self._create_session(chat_id)

        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/chat/completions",
            json={
                "chat_id": chat_id,
                "session_id": session_id,
                "question": question,
                "stream": False,
            },
        )
        self._raise(response)
        data = response.json()["data"]
        chunks = self._extract_chunks(data)
        result = AskResult(
            answer=data["answer"],
            chunks=chunks,
            session_id=session_id,
        )
        return result, chat_id

    def _create_chat_assistant(self, backend_kb_id: str) -> str:
        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/chats",
            json={
                "name": f"wiki-mgr-{backend_kb_id[:8]}-{uuid.uuid4().hex[:8]}",
                "dataset_ids": [backend_kb_id],
                "llm": {"model_name": "default"},
            },
        )
        self._raise(response)
        return response.json()["data"]["id"]

    def _create_session(self, chat_id: str) -> str:
        response = self._request(
            "POST",
            f"{self.base_url}/api/v1/chats/{chat_id}/sessions",
            json={"name": "wiki-session"},
        )
        self._raise(response)
        return response.json()["data"]["id"]

    @staticmethod
    def _extract_chunks(data: dict) -> list[RetrievalResult]:
        chunks = []
        for ref in data.get("reference", {}).get("chunks", []):
            chunks.append(
                RetrievalResult(
                    chunk_id=ref.get("id", ""),
                    content=ref.get("content", ""),
                    document_name=ref.get("document_keyword", ""),
                    similarity=ref.get("similarity", 0.0),
                    dataset_id=ref.get("dataset_id", ""),
                )
            )
        return chunks

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying ``httpx.Client`` (session auth mode)."""
        if self._client is not None:
            self._client.close()
            self._client = None
