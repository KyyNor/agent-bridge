from __future__ import annotations

from fastapi.testclient import TestClient
from agent_bridge.api.routes.retrieval_probe import RetrievalProbeRequest

from agent_bridge.api.app import create_app
from agent_bridge.client import AgentBridgeClient
from agent_bridge.knowledge_management.retrieval_probe.models import (
    KeywordProbeResult,
    ProbeResponse,
    ProbeStatus,
    ProbeTarget,
    TargetProbeSummary,
)


def _probe_response() -> ProbeResponse:
    target = ProbeTarget("wiki", "data-platform", "数据平台", "wiki_ask")
    keyword_hit = KeywordProbeResult(
        target=target,
        keyword="订单",
        status=ProbeStatus.hit,
        candidate_keys=("chunk-1",),
        count=1,
        duration_ms=8,
    )
    return ProbeResponse(
        probe_id="probe_test",
        profile_key="dev",
        session_id="session-1",
        keywords=("订单",),
        source_statuses={
            "wiki": ProbeStatus.hit,
            "codegraph": ProbeStatus.not_configured,
            "memory": ProbeStatus.not_configured,
            "artifact": ProbeStatus.no_hit,
        },
        targets=(
            TargetProbeSummary(
                target=target,
                status=ProbeStatus.hit,
                unique_hit_count=1,
                keyword_hits=(keyword_hit,),
            ),
        ),
        duration_ms=12,
    )


def test_retrieval_probe_api_returns_structured_payload(wm_paths, monkeypatch) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    captured = {}

    async def fake_probe(**kwargs):
        captured.update(kwargs)
        return _probe_response()

    monkeypatch.setattr(
        app.state.agent_bridge_service.retrieval_probe,
        "probe",
        fake_probe,
    )
    response = TestClient(app).post(
        "/api/v1/retrieval/probe",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "profile_key": "dev",
            "prompt": "订单",
            "session_id": "session-1",
            "keyword_limit": 8,
            "result_limit": 3,
            "timeout_seconds": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["probe_id"] == "probe_test"
    assert response.json()["targets"][0]["suggested_tool"] == "wiki_ask"
    assert captured == {
        "actor": "root",
        "profile_key": "dev",
        "prompt": "订单",
        "session_id": "session-1",
        "keyword_limit": 8,
        "result_limit": 3,
        "timeout_seconds": 10.0,
    }


def test_retrieval_probe_api_validates_required_fields_and_bounds(wm_paths) -> None:
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))
    headers = {"X-Agent-Bridge-User": "root"}

    missing = client.post(
        "/api/v1/retrieval/probe",
        headers=headers,
        json={"prompt": "订单"},
    )
    invalid = client.post(
        "/api/v1/retrieval/probe",
        headers=headers,
        json={
            "profile_key": "dev",
            "prompt": "订单",
            "keyword_limit": 0,
            "result_limit": 21,
            "timeout_seconds": 31,
        },
    )

    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert RetrievalProbeRequest(profile_key="dev", prompt="订单", keyword_limit=0).keyword_limit == 0


def test_retrieval_probe_hook_api_forwards_standard_hook_request(wm_paths, monkeypatch) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    captured = {}
    expected = {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}

    async def fake_handle(**kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        app.state.agent_bridge_service.retrieval_probe,
        "handle_claude_code_hook",
        fake_handle,
    )
    raw_payload = {"hook_event_name": "UserPromptSubmit", "prompt": "订单", "cwd": "/repo"}
    response = TestClient(app).post(
        "/api/v1/retrieval/hooks/claude-code/full-probe",
        headers={"X-Agent-Bridge-User": "root"},
        json={
            "profile_key": "dev",
            "event_name": "UserPromptSubmit",
            "matcher": None,
            "payload": raw_payload,
            "hook_timeout_seconds": 12,
        },
    )

    assert response.status_code == 200
    assert response.json() == expected
    assert captured == {
        "actor": "root",
        "profile_key": "dev",
        "event_name": "UserPromptSubmit",
        "matcher": None,
        "payload": raw_payload,
        "timeout_seconds": 12,
    }


def test_retrieval_probe_hook_api_validates_timeout_bounds(wm_paths) -> None:
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))
    response = client.post(
        "/api/v1/retrieval/hooks/claude-code/full-probe",
        headers={"X-Agent-Bridge-User": "root"},
        json={"profile_key": "dev", "payload": {}, "hook_timeout_seconds": 301},
    )

    assert response.status_code == 422


def test_client_posts_retrieval_probe_with_explicit_timeout(monkeypatch) -> None:
    client = AgentBridgeClient("http://bridge.example", "root")
    captured = {}

    class FakeResponse:
        def json(self):
            return {"probe_id": "probe_test"}

    def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(client, "_request", fake_request)
    payload = {"profile_key": "dev", "prompt": "订单"}

    result = client.probe_retrieval(payload, timeout=12.0)

    assert result == {"probe_id": "probe_test"}
    assert captured == {
        "method": "POST",
        "path": "/api/v1/retrieval/probe",
        "json": payload,
        "timeout": 12.0,
    }


def test_client_posts_retrieval_probe_hook_with_explicit_timeout(monkeypatch) -> None:
    client = AgentBridgeClient("http://bridge.example", "root")
    captured = {}

    class FakeResponse:
        def json(self):
            return {"stdout": '{"continue":true}', "stderr": "", "exit_code": 0, "status": "ok"}

    def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(client, "_request", fake_request)
    payload = {"profile_key": "dev", "payload": {"prompt": "订单"}}

    result = client.post_retrieval_probe_hook(payload, timeout=12.0)

    assert result["status"] == "ok"
    assert captured == {
        "method": "POST",
        "path": "/api/v1/retrieval/hooks/claude-code/full-probe",
        "json": payload,
        "timeout": 12.0,
    }
