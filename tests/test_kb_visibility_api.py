"""文档知识库可见范围更新接口的契约测试。

- 归属组与管理员可以在创建后把 KB 在 group/shared 之间切换
- 其他小组用户只能读共享 KB，不能改它的范围
- 范围切换沿用 defaults 编辑令牌做并发护栏
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def _client(wm_paths) -> TestClient:
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        '[backends.mock]\nbackend_type = "mock"\n', encoding="utf-8"
    )
    return TestClient(create_app(paths=wm_paths, admins={"root"}))


def _setup_groups(client: TestClient) -> None:
    root = {"X-Agent-Bridge-User": "root"}
    client.post("/api/v1/access/groups", headers=root, json={"group_key": "team-a", "name": "A 组"})
    client.post("/api/v1/access/groups", headers=root, json={"group_key": "team-b", "name": "B 组"})
    client.put(
        "/api/v1/access/memberships",
        headers=root,
        json={"user_id": "alice", "group_key": "team-a"},
    )
    client.put(
        "/api/v1/access/memberships",
        headers=root,
        json={"user_id": "bob", "group_key": "team-b"},
    )


def test_kb_visibility_update_and_cross_group_read(wm_paths, tmp_path: Path) -> None:
    client = _client(wm_paths)
    _setup_groups(client)
    root = {"X-Agent-Bridge-User": "root"}
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}

    assert client.post("/api/v1/admin/init", headers=root).status_code == 200
    created = client.post(
        "/api/v1/kbs",
        headers=alice,
        json={"slug": "handbook", "name": "手册", "visibility": "group"},
    )
    assert created.status_code == 200
    assert created.json()["visibility"] == "group"
    kb_id = created.json()["id"]
    alice_kbs = [kb for kb in client.get("/api/v1/kbs", headers=alice).json() if kb["slug"] == "handbook"]
    edit_token = alice_kbs[0]["edit_token"]

    # 组内资源对其他小组不可见
    assert all(kb["slug"] != "handbook" for kb in client.get("/api/v1/kbs", headers=bob).json())

    # 归属组成员切换为共享
    shared = client.put(
        "/api/v1/kbs/handbook/visibility",
        headers=alice,
        json={"visibility": "shared"},
    )
    assert shared.status_code == 200
    assert shared.json()["visibility"] == "shared"
    assert shared.json()["id"] == kb_id

    # 共享后其他小组可读，但仍不能修改范围
    bob_view = [kb for kb in client.get("/api/v1/kbs", headers=bob).json() if kb["slug"] == "handbook"]
    assert len(bob_view) == 1
    denied = client.put(
        "/api/v1/kbs/handbook/visibility",
        headers=bob,
        json={"visibility": "group"},
    )
    assert denied.status_code == 403

    # 改回组内后其他小组再次不可见
    reverted = client.put(
        "/api/v1/kbs/handbook/visibility",
        headers=alice,
        json={"visibility": "group", "expected_edit_token": edit_token},
    )
    assert reverted.status_code == 200
    assert reverted.json()["visibility"] == "group"
    assert all(kb["slug"] != "handbook" for kb in client.get("/api/v1/kbs", headers=bob).json())


def test_kb_visibility_update_rejects_stale_edit_token(wm_paths, tmp_path: Path) -> None:
    client = _client(wm_paths)
    _setup_groups(client)
    root = {"X-Agent-Bridge-User": "root"}
    alice = {"X-Agent-Bridge-User": "alice"}

    assert client.post("/api/v1/admin/init", headers=root).status_code == 200
    created = client.post("/api/v1/kbs", headers=alice, json={"slug": "handbook", "name": "手册"})
    assert created.status_code == 200

    # defaults 先被其他页面更新，旧的编辑令牌随之下发会冲突
    alice_kbs = [kb for kb in client.get("/api/v1/kbs", headers=alice).json() if kb["slug"] == "handbook"]
    stale_token = alice_kbs[0]["edit_token"]
    client.put(
        "/api/v1/kbs/handbook/defaults",
        headers=alice,
        json={"default_backend_slug": "mock"},
    )
    conflict = client.put(
        "/api/v1/kbs/handbook/visibility",
        headers=alice,
        json={"visibility": "shared", "expected_edit_token": stale_token},
    )
    assert conflict.status_code == 409


def test_kb_visibility_update_validates_payload(wm_paths, tmp_path: Path) -> None:
    client = _client(wm_paths)
    _setup_groups(client)
    root = {"X-Agent-Bridge-User": "root"}
    alice = {"X-Agent-Bridge-User": "alice"}

    assert client.post("/api/v1/admin/init", headers=root).status_code == 200
    assert (
        client.post("/api/v1/kbs", headers=alice, json={"slug": "handbook", "name": "手册"}).status_code
        == 200
    )
    # 非法取值在请求层直接拒绝
    assert (
        client.put(
            "/api/v1/kbs/handbook/visibility",
            headers=alice,
            json={"visibility": "public"},
        ).status_code
        == 422
    )
    missing = client.put(
        "/api/v1/kbs/unknown/visibility",
        headers=alice,
        json={"visibility": "shared"},
    )
    assert missing.status_code == 404
