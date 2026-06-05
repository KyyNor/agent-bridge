"""Integration test for WeknoraBackend against a live Weknora instance.

Requires Weknora at http://localhost. Run with:

    uv run pytest tests/test_weknora_integration.py -v -m weknora --tb=short

The test bootstraps a wiki-manager tenant through HTTP APIs only, creates or
reuses required models, writes local registration details under DockerData, and
cleans up temporary KB resources.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_bridge.knowledge.backends.weknora import WeknoraBackend


WEKNORA_URL = os.environ.get("WEKNORA_URL", "http://localhost").rstrip("/")
GOWIKI_CONFIG = Path("/Users/kyynor/.config/gowiki/config.yaml")
WEKNORA_ENV_DOC = Path("/Users/kyynor/DockerData/wiki-manager-weknora-env.md")
WEKNORA_EMAIL = os.environ.get("WEKNORA_EMAIL", "wiki-manager@local.test")
WEKNORA_USERNAME = os.environ.get("WEKNORA_USERNAME", "wiki-manager")
WEKNORA_PASSWORD = os.environ.get("WEKNORA_PASSWORD", "wiki-manager-phase4-local")


def _read_simple_yaml(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _request(
    method: str,
    path: str,
    *,
    api_key: str | None = None,
    **kwargs: Any,
) -> httpx.Response:
    headers = kwargs.pop("headers", None) or {}
    if api_key:
        headers["X-API-Key"] = api_key
    response = httpx.request(
        method,
        f"{WEKNORA_URL}{path}",
        headers=headers,
        timeout=120,
        **kwargs,
    )
    return response


def _raise(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise RuntimeError(f"Weknora HTTP {response.status_code}: {response.text}")
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(payload.get("message") or payload.get("error") or response.text)


def _register_or_login() -> dict[str, Any]:
    payload = {
        "username": WEKNORA_USERNAME,
        "email": WEKNORA_EMAIL,
        "password": WEKNORA_PASSWORD,
    }
    response = _request("POST", "/api/v1/auth/register", json=payload)
    if response.status_code in {400, 409}:
        response = _request(
            "POST",
            "/api/v1/auth/login",
            json={"email": WEKNORA_EMAIL, "password": WEKNORA_PASSWORD},
        )
    _raise(response)
    data = response.json()
    tenant = data.get("tenant") or data["active_tenant"]
    return {
        "user": data["user"],
        "tenant": tenant,
        "api_key": tenant["api_key"],
        "token": data.get("token"),
    }


def _find_model(api_key: str, *, name: str, model_type: str, provider: str) -> str | None:
    response = _request("GET", "/api/v1/models", api_key=api_key)
    _raise(response)
    for item in response.json().get("data", []):
        parameters = item.get("parameters") or {}
        if (
            item.get("name") == name
            and item.get("type") == model_type
            and parameters.get("provider") == provider
        ):
            return item["id"]
    return None


def _ensure_model(
    api_key: str,
    *,
    name: str,
    model_type: str,
    base_url: str,
    provider: str,
    upstream_api_key: str,
    description: str,
    dimension: int | None = None,
) -> str:
    existing = _find_model(api_key, name=name, model_type=model_type, provider=provider)
    if existing:
        return existing

    parameters: dict[str, Any] = {
        "base_url": base_url,
        "api_key": upstream_api_key,
        "provider": provider,
    }
    if dimension is not None:
        parameters["embedding_parameters"] = {
            "dimension": dimension,
            "truncate_prompt_tokens": 0,
        }
    response = _request(
        "POST",
        "/api/v1/models",
        api_key=api_key,
        json={
            "name": name,
            "type": model_type,
            "source": "remote",
            "description": description,
            "parameters": parameters,
        },
    )
    _raise(response)
    return response.json()["data"]["id"]


def _write_env_doc(
    *,
    registration: dict[str, Any],
    chat_model_name: str,
    chat_model_id: str,
    embedding_model_name: str,
    embedding_model_id: str,
) -> None:
    tenant = registration["tenant"]
    user = registration["user"]
    WEKNORA_ENV_DOC.parent.mkdir(parents=True, exist_ok=True)
    WEKNORA_ENV_DOC.write_text(
        "\n".join([
            "# wiki-manager Weknora Registration",
            "",
            f"- base_url: `{WEKNORA_URL}`",
            f"- username: `{user.get('username', WEKNORA_USERNAME)}`",
            f"- email: `{user.get('email', WEKNORA_EMAIL)}`",
            f"- password: `{WEKNORA_PASSWORD}`",
            f"- tenant_id: `{tenant.get('id')}`",
            f"- tenant_name: `{tenant.get('name')}`",
            f"- tenant_api_key: `{registration['api_key']}`",
            f"- chat_model: `{chat_model_name}`",
            f"- chat_model_id: `{chat_model_id}`",
            f"- embedding_model: `{embedding_model_name}`",
            f"- embedding_model_id: `{embedding_model_id}`",
            "",
            "This file is local-only and must not be committed.",
            "",
        ]),
        encoding="utf-8",
    )
    WEKNORA_ENV_DOC.chmod(0o600)


@pytest.mark.weknora
def test_weknora_full_backend_flow(tmp_path: Path) -> None:
    if not GOWIKI_CONFIG.exists():
        pytest.skip(f"missing {GOWIKI_CONFIG}")

    gowiki = _read_simple_yaml(GOWIKI_CONFIG)
    registration = _register_or_login()
    api_key = registration["api_key"]

    chat_model_name = gowiki["generate_model"]
    chat_model_id = _ensure_model(
        api_key,
        name=chat_model_name,
        model_type="KnowledgeQA",
        base_url=gowiki["base_url"],
        provider="deepseek",
        upstream_api_key=gowiki["api_key"],
        description="wiki-manager DeepSeek chat model",
    )
    embedding_model_name = gowiki["embed_model"]
    embedding_model_id = _ensure_model(
        api_key,
        name=embedding_model_name,
        model_type="Embedding",
        base_url=gowiki["embed_base_url"],
        provider="siliconflow",
        upstream_api_key=gowiki["embed_api_key"],
        description="wiki-manager SiliconFlow embedding model",
        dimension=1024,
    )
    _write_env_doc(
        registration=registration,
        chat_model_name=chat_model_name,
        chat_model_id=chat_model_id,
        embedding_model_name=embedding_model_name,
        embedding_model_id=embedding_model_id,
    )

    backend = WeknoraBackend(
        base_url=WEKNORA_URL,
        api_key=api_key,
        embedding_model_id=embedding_model_id,
        summary_model_id=chat_model_id,
        timeout=120,
    )
    kb_id: str | None = None
    doc_id: str | None = None
    try:
        suffix = uuid.uuid4().hex[:8]
        kb_id = backend.create_kb(f"weknora-int-{suffix}", f"weknora-int-{suffix}")
        test_file = tmp_path / "weknora-phase4.md"
        test_file.write_text(
            "# Weknora Phase 4\n\n"
            "The wiki-manager Weknora integration acceptance phrase is phase-four-weknora-api-only.\n",
            encoding="utf-8",
        )
        doc_id = backend.upload(kb_id, "weknora-phase4", test_file, "weknora-phase4.md")

        status = None
        for _ in range(30):
            status = backend.get_status(kb_id, doc_id)
            if status.status in {"completed", "failed", "not_found"}:
                break
            time.sleep(2)
        assert status is not None
        assert status.status == "completed", status

        results = backend.retrieve(kb_id, "phase-four-weknora-api-only", top_k=3)
        assert results
        assert any("phase-four-weknora-api-only" in item.content for item in results)

        answer, _ = backend.ask(kb_id, "What is the Weknora integration acceptance phrase?")
        assert answer.session_id
        assert answer.answer.strip()
    finally:
        if doc_id:
            backend.delete(kb_id or "", doc_id)
        if kb_id:
            backend.delete_kb(kb_id)
