from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


@pytest.fixture
def api_setup(wm_paths):
    app = create_app(wm_paths, {'root'})
    svc = app.state.agent_bridge_service
    svc.access.upsert_group(actor='root', group_key='team-a', name='A')
    svc.access.upsert_group(actor='root', group_key='team-b', name='B')
    svc.access.set_user_group(actor='root', user_id='alice', group_key='team-a')
    svc.access.set_user_group(actor='root', user_id='bob', group_key='team-b')
    svc.create_kb('alice', 'docs-a', 'A', '')
    svc.create_kb('alice', 'docs-a2', 'A2', '')
    svc.create_kb('bob', 'docs-b', 'B', '')
    return TestClient(app), svc


def key(client, user):
    response = client.post('/api/v1/personal-api-key/reset', headers={'X-Agent-Bridge-User': user})
    assert response.status_code == 200, response.text
    return response.json()['api_key']


def auth(token):
    return {'Authorization': 'Bearer ' + token}


def test_key_lifecycle_private_hash_only_and_no_global_access(api_setup):
    client, svc = api_setup
    alice = key(client, 'alice');bob = key(client, 'bob')
    assert alice != bob
    status = client.get('/api/v1/personal-api-key', headers={'X-Agent-Bridge-User': 'alice'}).json()
    assert status['has_key'] and 'api_key' not in status
    assert alice not in json.dumps(status)
    with svc.store.connect() as conn:
        row = dict(conn.execute("SELECT * FROM personal_api_keys WHERE user_id='alice'").fetchone())
    assert alice not in json.dumps(row)
    assert len(row['token_hash']) == 64
    assert client.get('/api/v1/backends', headers=auth(alice)).status_code == 401
    assert client.post('/api/v1/personal-api-key/reset', headers=auth(alice)).status_code == 401
    replacement = key(client, 'alice')
    path = '/api/v1/knowledge-api/kbs/docs-a/documents'
    assert client.get(path, headers=auth(alice)).status_code == 401
    assert client.get(path, headers=auth(replacement)).status_code == 200
    with svc.store.connect() as conn:
        conn.execute("UPDATE access_users SET status='inactive' WHERE user_id='alice'")
    assert client.get(path, headers=auth(replacement)).status_code == 401


def test_document_real_service_roundtrip_and_scoped_deletion(api_setup):
    client, svc = api_setup
    token = key(client, 'alice');headers = auth(token)
    path = '/api/v1/knowledge-api/kbs/docs-a/documents'
    response = client.post(path, headers=headers, files={'file': ('example.md', b'# Hello\nSynthetic document', 'text/markdown')})
    assert response.status_code == 202, response.text
    slug = response.json()['documents'][0]['slug']
    kb2 = svc.store.get_kb_by_slug('docs-a2')
    folder = svc.store.ensure_root_folder(kb2['id'])
    svc.attach_document('alice', slug, 'docs-a2', folder['id'])
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['slug'] == slug
    assert 'status' in response.json()['items'][0]
    assert client.delete(path + '/' + slug, headers=headers).status_code == 200
    assert client.get(path, headers=headers).json()['total'] == 0
    assert client.get('/api/v1/knowledge-api/kbs/docs-a2/documents', headers=headers).json()['total'] == 1
    assert client.delete(path + '/' + slug, headers=headers).status_code == 404


def test_cross_group_permissions_and_auth_no_header_fallback(api_setup):
    client, svc = api_setup
    token = key(client, 'alice');path = '/api/v1/knowledge-api/kbs/docs-b/documents'
    assert client.get(path, headers=auth(token)).status_code == 403
    assert client.post(path, headers=auth(token), files={'file': ('a.md', b'a')}).status_code == 403
    assert client.delete(path + '/unknown', headers=auth(token)).status_code == 403
    assert client.get(path, headers={'X-Agent-Bridge-User': 'root'}).status_code == 401
    assert client.get(path, headers={**auth('bad'), 'X-Agent-Bridge-User': 'root'}).status_code == 401
    svc.update_kb_visibility('bob', 'docs-b', visibility='shared')
    assert client.get(path, headers=auth(token)).status_code == 200
    assert client.post(path, headers=auth(token), files={'file': ('a.md', b'a')}).status_code == 403
    assert client.delete(path + '/unknown', headers=auth(token)).status_code == 403
    svc.access.set_user_group(actor='root', user_id='alice', group_key='team-b')
    assert client.get(path, headers=auth(token)).status_code == 200
    assert client.get('/api/v1/knowledge-api/kbs/docs-a/documents', headers=auth(token)).status_code == 403


def test_schema_has_exact_three_operations_and_limits(api_setup):
    client, _ = api_setup
    response = client.get('/api/v1/kbs/docs-a/openapi', headers={'X-Agent-Bridge-User': 'alice'})
    assert response.status_code == 200
    schema = response.json()
    operations = [op for path in schema['paths'].values() for op in path.values()]
    assert len(operations) == 3
    assert all(op['security'] == [{'PersonalApiKey': []}] for op in operations)
    assert 'personal-api-key' not in json.dumps(schema)
    assert client.get('/api/v1/kbs/docs-a/openapi', headers={'X-Agent-Bridge-User': 'bob'}).status_code == 403
    token = key(client, 'alice');path = '/api/v1/knowledge-api/kbs/docs-a/documents'
    assert client.get(path + '?limit=0', headers=auth(token)).status_code == 422
    assert client.post(path, headers=auth(token)).status_code == 422
    assert client.post(path, headers=auth(token), files={'file': ('bad.exe', b'x')}).status_code == 400
    response = client.post(path, headers=auth(token), files={'file': ('large.md', b'x' * (32 * 1024 * 1024 + 1))})
    assert response.status_code == 413


def test_logged_in_document_download_returns_current_original_from_requested_kb(api_setup):
    client, svc = api_setup
    alice = {"X-Agent-Bridge-User": "alice"}
    bob = {"X-Agent-Bridge-User": "bob"}
    uploaded = client.post(
        "/api/v1/docs", headers=alice, data={"kb": "docs-a"},
        files={"file": ("项目手册.md", b"# First version", "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    slug = uploaded.json()["slug"]
    path = f"/api/v1/kbs/docs-a/docs/{slug}/download"

    original = client.get(path, headers=alice)
    assert original.content == b"# First version"
    assert unquote(original.headers["content-disposition"]) == "attachment; filename*=utf-8''项目手册.md"
    assert original.headers["cache-control"] == "private, no-store"
    assert client.get(f"/api/v1/kbs/docs-a2/docs/{slug}/download", headers=alice).status_code == 404
    assert client.get(path, headers=auth(key(client, "alice"))).status_code == 401

    svc.update_kb_visibility("alice", "docs-a", visibility="shared")
    assert client.get(f"/api/v1/docs/{slug}", headers=bob).status_code == 200
    assert client.get(path, headers=bob).status_code == 403
    updated = client.post(
        f"/api/v1/docs/{slug}/versions", headers=alice,
        files={"file": ("项目手册-v2.md", b"# Current version", "text/markdown")},
    )
    assert updated.status_code == 200, updated.text
    current = client.get(path, headers=alice)
    assert current.content == b"# Current version"
    assert "项目手册-v2.md" in unquote(current.headers["content-disposition"])

    version = svc.store.get_current_document_version(uploaded.json()["id"])
    archive_path = Path(version["archive_path"])
    archive_path.unlink()
    assert client.get(path, headers=alice).status_code == 404
    original_archive = svc.store.list_versions(uploaded.json()["id"])[0]["archive_path"]
    archive_path.symlink_to(original_archive)
    assert client.get(path, headers=alice).status_code == 404


def test_admin_elevation_cannot_create_shared_or_persistent_admin_key(api_setup):
    client, svc = api_setup
    alice_headers = {'X-Agent-Bridge-User': 'alice'}
    password = 'synthetic-admin-password'
    login = client.post('/api/v1/auth/admin/session', headers=alice_headers, json={'password': password})
    assert login.status_code == 200
    assert login.json()['subject_user_id'] == 'alice'
    alice_token = key(client, 'alice')
    alice_status = client.get('/api/v1/personal-api-key', headers=alice_headers).json()
    with svc.store.connect() as conn:
        assert conn.execute("SELECT user_id FROM personal_api_keys").fetchall()[0][0] == 'alice'
        assert conn.execute("SELECT 1 FROM personal_api_keys WHERE user_id='root'").fetchone() is None
    path_a = '/api/v1/knowledge-api/kbs/docs-a/documents'
    path_b = '/api/v1/knowledge-api/kbs/docs-b/documents'
    # 进入、退出维护模式都不能令个人 Key 获得 root 权限。
    assert client.get(path_b, headers=auth(alice_token)).status_code == 403
    assert client.post(path_b, headers=auth(alice_token), files={'file': ('x.md', b'x')}).status_code == 403
    assert client.delete(path_b + '/unknown', headers=auth(alice_token)).status_code == 403
    client.delete('/api/v1/auth/admin/session')
    assert client.get(path_a, headers=auth(alice_token)).status_code == 200
    assert client.get(path_b, headers=auth(alice_token)).status_code == 403
    # 另一个人在维护模式下重置，只能影响本人。
    bob = TestClient(client.app)
    assert bob.post('/api/v1/auth/admin/session', headers={'X-Agent-Bridge-User': 'bob'}, json={'password': password}).status_code == 200
    bob_token = key(bob, 'bob')
    assert bob_token != alice_token
    assert client.get('/api/v1/personal-api-key', headers=alice_headers).json() == alice_status
    assert client.get(path_a, headers=auth(alice_token)).status_code == 200
    assert client.get(path_b, headers=auth(bob_token)).status_code == 200
    svc.access.set_user_group(actor='root', user_id='alice', group_key='team-b')
    assert client.get(path_a, headers=auth(alice_token)).status_code == 403
    assert client.get(path_b, headers=auth(alice_token)).status_code == 200
    with svc.store.connect() as conn:
        conn.execute("UPDATE access_users SET status='inactive' WHERE user_id='alice'")
    assert client.get(path_b, headers=auth(alice_token)).status_code == 401


def test_anonymous_maintenance_session_cannot_manage_personal_keys(api_setup):
    client, svc = api_setup
    login = client.post('/api/v1/auth/admin/session', json={'password': 'synthetic-admin-password'})
    assert login.status_code == 200
    assert login.json()['subject_user_id'] == 'anonymous'
    # 维护功能仍可用，但维护 Cookie 不能代替个人身份签发凭据。
    assert client.get('/api/v1/backends').status_code == 200
    assert client.get('/api/v1/personal-api-key').status_code == 401
    assert client.post('/api/v1/personal-api-key/reset').status_code == 401
    with svc.store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM personal_api_keys').fetchone()[0] == 0


def test_sso_personal_subject_is_preserved_during_admin_mode(api_setup):
    from datetime import timedelta
    import jwt
    from agent_bridge.core.timeutil import utc_now

    _, svc = api_setup
    secret = 'synthetic-sso-secret-at-least-32-characters'
    svc.paths.server_config_path.write_text(
        'admins = ["root"]\n[identity]\nsso_secret = "' + secret + '"\nallow_cli_header = false\n', encoding='utf-8',
    )
    app = create_app(svc.paths, {'root'})
    client = TestClient(app)
    token = jwt.encode({'user_id': 'alice', 'exp': int((utc_now() + timedelta(hours=1)).timestamp())}, secret, algorithm='HS256')
    client.cookies.set('agent_bridge_sso', token)
    login = client.post('/api/v1/auth/admin/session', json={'password': 'synthetic-admin-password'})
    assert login.status_code == 200
    response = client.post('/api/v1/personal-api-key/reset')
    assert response.status_code == 200
    personal = response.json()['api_key']
    client.cookies.delete('agent_bridge_sso')
    assert client.get('/api/v1/backends').status_code == 200
    assert client.post('/api/v1/personal-api-key/reset').status_code == 401
    assert client.get('/api/v1/knowledge-api/kbs/docs-b/documents', headers=auth(personal)).status_code == 403
    with svc.store.connect() as conn:
        assert conn.execute('SELECT user_id FROM personal_api_keys').fetchone()[0] == 'alice'
