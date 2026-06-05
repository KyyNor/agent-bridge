"""Integration tests for RagFlowBackend against a live RagFlow instance.

Requires a running RagFlow server at localhost:9380.  Run with:

    uv run pytest -m ragflow -s

The tests use session-based auth (email/password) and clean up all created
resources on both success and failure.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from agent_bridge.knowledge.backends.ragflow import RagFlowBackend
from agent_bridge.core.domain import RetrievalResult, AskResult

# ---------------------------------------------------------------------------
# RagFlow connection details
# ---------------------------------------------------------------------------
RAGFLOW_URL = "http://localhost:9380"
RAGFLOW_EMAIL = "admin@wiki.local"
RAGFLOW_PASSWORD = "admin123"
RAGFLOW_API_KEY = "ragflow-wP5FuWWM0ihndQLfVyZnq3HeoUI9WI9GaFJcTHhc7Aw"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique_slug() -> str:
    return f"inttest-{uuid.uuid4().hex[:12]}"


def _make_backend() -> RagFlowBackend:
    return RagFlowBackend(
        base_url=RAGFLOW_URL,
        email=RAGFLOW_EMAIL,
        password=RAGFLOW_PASSWORD,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Fixture-style helper — yields a KB id and guarantees cleanup
# ---------------------------------------------------------------------------

class _KBGuard:
    """Creates a KB on enter and deletes it on exit."""

    def __init__(self, backend: RagFlowBackend) -> None:
        self.backend = backend
        self.slug = _unique_slug()
        self.kb_id: str | None = None

    def __enter__(self) -> _KBGuard:
        self.kb_id = self.backend.create_kb(self.slug, self.slug)
        return self

    def __exit__(self, *exc: object) -> None:
        if self.kb_id is not None:
            try:
                self.backend.delete_kb(self.kb_id)
            except Exception:
                pass  # best-effort cleanup


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.ragflow
def test_session_auth_login() -> None:
    """Verify that session-based login succeeds and returns a client."""
    backend = _make_backend()
    try:
        client = backend._get_client()
        assert client is not None
        # A second call should return the same client (cookie reuse).
        assert backend._get_client() is client
    finally:
        backend.close()


@pytest.mark.ragflow
def test_bearer_token_auth() -> None:
    """Verify that Bearer-token auth still works (backward compat)."""
    # First log in via session to obtain a token.
    session_backend = _make_backend()
    try:
        client = session_backend._get_client()
        resp = client.post(
            f"{RAGFLOW_URL}/api/v1/system/tokens",
            json={"name": "inttest-bearer"},
        )
        assert resp.status_code < 400, f"Token creation failed: {resp.text}"
        token = resp.json()["data"]["token"]
    finally:
        session_backend.close()

    # Use the Bearer token to create and delete a KB.
    backend = RagFlowBackend(base_url=RAGFLOW_URL, api_key=token, timeout=60)
    slug = _unique_slug()
    kb_id = backend.create_kb(slug, slug)
    assert kb_id
    backend.delete_kb(kb_id)


@pytest.mark.ragflow
def test_create_and_delete_kb() -> None:
    """Create a knowledge base and then delete it."""
    backend = _make_backend()
    try:
        slug = _unique_slug()
        kb_id = backend.create_kb(slug, slug)
        assert kb_id, "Expected a non-empty KB id"
        backend.delete_kb(kb_id)
    finally:
        backend.close()


@pytest.mark.ragflow
def test_upload_get_status_delete_document(tmp_path: Path) -> None:
    """Full document lifecycle: upload -> check status -> delete."""
    backend = _make_backend()
    try:
        with _KBGuard(backend) as guard:
            # Create a small text file.
            test_file = tmp_path / "hello.txt"
            test_file.write_text("Hello from wiki-manager integration test.\n")

            # Upload.
            doc_id = backend.upload(
                guard.kb_id,
                "hello-txt",
                test_file,
                "hello.txt",
            )
            assert doc_id, "Expected a non-empty document id"

            # Poll status a few times.  In a bare RagFlow install without
            # an embedding model the doc stays at "pending" (UNSTART), so we
            # just verify the status endpoint works rather than demanding a
            # specific terminal state.
            import time

            status = None
            for _ in range(5):
                status = backend.get_status(guard.kb_id, doc_id)
                if status.status in ("completed", "error"):
                    break
                time.sleep(2)

            assert status is not None, "Failed to fetch document status"
            assert status.status in (
                "pending",
                "parsing",
                "completed",
                "error",
            ), f"Unexpected doc status: {status.status}"

            # Delete the document.
            backend.delete(guard.kb_id, doc_id)
    finally:
        backend.close()


@pytest.mark.ragflow
def test_close_is_idempotent() -> None:
    """Calling close() multiple times should not raise."""
    backend = _make_backend()
    backend._get_client()  # force login
    backend.close()
    backend.close()  # second call should be a no-op


# ---------------------------------------------------------------------------
# Phase 3: retrieval and Q&A e2e tests (require data in RagFlow)
# ---------------------------------------------------------------------------

@pytest.mark.ragflow
def test_ragflow_retrieve_returns_results() -> None:
    """Verify retrieve() returns RetrievalResult objects with data."""
    backend = RagFlowBackend(base_url=RAGFLOW_URL, api_key=RAGFLOW_API_KEY)
    datasets_resp = backend._request("GET", f"{RAGFLOW_URL}/api/v1/datasets")
    datasets = datasets_resp.json()["data"]
    if not datasets:
        pytest.skip("no datasets available in RagFlow")

    dataset_id = datasets[0]["id"]
    results = backend.retrieve(dataset_id, "test query", top_k=3)
    assert isinstance(results, list)
    for r in results:
        assert isinstance(r, RetrievalResult)
        assert r.chunk_id
        assert r.content


@pytest.mark.ragflow
def test_ragflow_ask_returns_answer() -> None:
    """Verify ask() returns an AskResult and a chat_id."""
    backend = RagFlowBackend(base_url=RAGFLOW_URL, api_key=RAGFLOW_API_KEY)

    datasets_resp = backend._request("GET", f"{RAGFLOW_URL}/api/v1/datasets")
    datasets = datasets_resp.json()["data"]
    if not datasets:
        pytest.skip("no datasets available in RagFlow")

    dataset_id = datasets[0]["id"]
    result, chat_id = backend.ask(dataset_id, "what is this about?")
    assert isinstance(result, AskResult)
    assert chat_id
    assert isinstance(result.answer, str)
