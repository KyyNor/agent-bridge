"""通过实际同步入口和 Weknora HTTP 契约验证状态回写。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app
from agent_bridge.core.domain import SyncJobStatus

_BASE_URL = "http://weknora.test"
_REMOTE_KB = f"{_BASE_URL}/api/v1/knowledge-bases/kb-remote"
_ACTOR = {"X-Agent-Bridge-User": "root"}
_CONTENT = b"# Upload receipt\n"


@pytest.fixture
def knowledge(wm_paths, tmp_path: Path, respx_mock):
    app = create_app(wm_paths, {"root"})
    service = app.state.agent_bridge_service
    respx_mock.post(f"{_BASE_URL}/api/v1/knowledge-bases").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"id": "kb-remote"}})
    )
    service.add_backend("root", "remote", "weknora", base_url=_BASE_URL, api_key="test-key")
    service.create_kb("root", "guides", "Guides", "")
    source = tmp_path / "guide.md"
    source.write_bytes(_CONTENT)
    doc = service.add_document("root", source, ["guides"], later=True, relative_path="team/guide.md")
    return TestClient(app), service, source, doc


def _document_status(client: TestClient) -> str:
    response = client.get("/api/v1/docs?kb=guides", headers=_ACTOR)
    assert response.status_code == 200
    return response.json()[0]["sync_status"]


def _sync(client: TestClient) -> dict[str, int]:
    response = client.post("/api/v1/kbs/guides/sync?backend=remote", headers=_ACTOR)
    assert response.status_code == 200, response.text
    return response.json()


def _remote_document(doc_id: str, content: bytes = _CONTENT, folder: str = "team") -> dict:
    return {
        "id": doc_id,
        "knowledge_base_id": "kb-remote",
        "file_name": "guide.md",
        "folder_path": folder,
        "file_size": len(content),
        "file_hash": hashlib.md5(content, usedforsecurity=False).hexdigest(),
        "parse_status": "completed",
    }


def _list_response(*documents: dict) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": documents, "total": len(documents)})


def test_background_tracks_parse_failure_and_manual_reparse_without_reupload(knowledge, respx_mock):
    client, service, _, _ = knowledge
    upload = respx_mock.post(f"{_REMOTE_KB}/knowledge/file").mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": "file-1"}}),
    )
    remote = respx_mock.get(f"{_BASE_URL}/api/v1/knowledge/file-1").mock(side_effect=[
        httpx.Response(200, json={"data": {"parse_status": "failed"}}),
        httpx.Response(503),
        httpx.Response(200, json={"data": {"parse_status": "completed"}}),
    ])

    assert _sync(client)["succeeded"] == 1
    assert _document_status(client) == "parsing"
    service.doc_sync_scheduler._run_status_reconciliation()
    assert _document_status(client) == "parse_failed"

    service.update_kb_sync_policy("root", "guides", backend_sync_policies={"remote": "paused"})
    service.doc_sync_scheduler._run_status_reconciliation()
    assert remote.call_count == 1
    assert _document_status(client) == "parse_failed"

    service.update_kb_sync_policy("root", "guides", backend_sync_policies={"remote": "scheduled"})
    service.doc_sync_scheduler._run_status_reconciliation()
    assert _document_status(client) == "parse_failed"
    service.doc_sync_scheduler._run_status_reconciliation()
    assert _document_status(client) == "synced"
    assert upload.call_count == 1


@pytest.mark.parametrize("background", [True, False], ids=["background", "manual-sync"])
def test_upload_500_with_remote_receipt_is_reconciled_without_second_post(knowledge, respx_mock, background):
    client, service, _, doc = knowledge
    upload = respx_mock.post(f"{_REMOTE_KB}/knowledge/file").mock(return_value=httpx.Response(500))
    lookup = respx_mock.get(host="weknora.test", path="/api/v1/knowledge-bases/kb-remote/knowledge")
    lookup.mock(return_value=_list_response(
        _remote_document("same-name", content=b"different content"),
        _remote_document("different-folder", folder="other"),
        _remote_document("right-file"),
    ))
    assert _sync(client)["failed"] == 1
    assert _document_status(client) == "sync_unconfirmed"

    if background:
        service.doc_sync_scheduler._run_status_reconciliation()
    else:
        assert _sync(client)["succeeded"] == 1
    assert _document_status(client) == "synced"
    assert lookup.call_count == 1
    assert upload.call_count == 1
    assert service.list_docs("root", "guides", backend="remote")[0]["sync_status"] == "synced"
    assert service.get_doc("root", doc["slug"])["sync_states"][0]["backend_doc_id"] == "right-file"
    assert service.kb_status("root", "guides")["jobs"][0]["status"] == "succeeded"


def test_retry_requires_unambiguous_result_and_cannot_write_back_an_old_version(knowledge, respx_mock):
    client, service, source, doc = knowledge
    upload = respx_mock.post(f"{_REMOTE_KB}/knowledge/file").mock(return_value=httpx.Response(500))
    lookup = respx_mock.get(host="weknora.test", path="/api/v1/knowledge-bases/kb-remote/knowledge")
    lookup.mock(return_value=_list_response(_remote_document("copy-1"), _remote_document("copy-2")))
    assert _sync(client)["failed"] == 1
    assert _sync(client)["failed"] == 1
    assert upload.call_count == 1
    assert _document_status(client) == "sync_unconfirmed"

    def changed_while_checking(_request: httpx.Request) -> httpx.Response:
        source.write_bytes(b"# New version\n")
        service.update_document("root", doc["slug"], source, later=True)
        return _list_response(_remote_document("copy-1"))

    lookup.mock(side_effect=changed_while_checking)
    service.doc_sync_scheduler._run_status_reconciliation()
    assert _document_status(client) == "not_synced"
    assert service.get_doc("root", doc["slug"])["sync_states"][0]["backend_doc_id"] is None


def test_interrupted_upload_uses_persisted_fingerprint_after_restart(knowledge, respx_mock):
    client, service, _, _ = knowledge
    upload = respx_mock.post(f"{_REMOTE_KB}/knowledge/file").mock(return_value=httpx.Response(500))
    respx_mock.get(host="weknora.test", path="/api/v1/knowledge-bases/kb-remote/knowledge").mock(
        return_value=_list_response(_remote_document("received-before-restart")),
    )
    assert _sync(client)["failed"] == 1
    job_id = service.kb_status("root", "guides")["jobs"][0]["id"]
    # 模拟已记录发送指纹，但进程来不及保存请求结果。
    with service.store.connect() as conn:
        conn.execute("DELETE FROM sync_states WHERE backend_slug = 'remote'")
    service.store.update_job_status(job_id, SyncJobStatus.running)

    assert service.store.recover_interrupted_document_uploads() == 1
    assert service.store.recover_interrupted_document_uploads() == 0
    assert _document_status(client) == "sync_unconfirmed"
    service.doc_sync_scheduler._run_status_reconciliation()
    assert _document_status(client) == "synced"
    assert upload.call_count == 1
