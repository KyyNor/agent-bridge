from __future__ import annotations

from datetime import timedelta

import jwt
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver
from agent_bridge.api.app import create_app
from agent_bridge.core.timeutil import utc_now


def _token(secret: str, *, user_id: str = "alice", user_name: str = "Alice") -> str:
    now = utc_now()
    return jwt.encode(
        {
            "user_id": user_id,
            "user_name": user_name,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )


def _write_identity_config(wm_paths, secret: str) -> None:
    wm_paths.server_config_path.parent.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        "\n".join(
            [
                'host = "127.0.0.1"',
                "port = 8765",
                'admins = ["root"]',
                "",
                "[identity]",
                f'sso_secret = "{secret}"',
            ]
        ),
        encoding="utf-8",
    )


def test_identity_resolver_accepts_internal_cli_header() -> None:
    resolver = RequestIdentityResolver(IdentityConfig())
    app = FastAPI()

    @app.get("/actor")
    def actor(request: Request):
        identity = resolver.resolve(request)
        return {"user_id": identity.user_id, "source": identity.source}

    response = TestClient(app).get("/actor", headers={"X-Agent-Bridge-User": "alice"})

    assert response.json() == {"user_id": "alice", "source": "linux_cli"}


def test_sso_callback_sets_http_only_cookie_and_strips_token(wm_paths) -> None:
    secret = "test-sso-secret"
    _write_identity_config(wm_paths, secret)
    client = TestClient(create_app(paths=wm_paths, admins={"root"}))

    response = client.get(
        "/api/v1/auth/sso/callback",
        params={"token": _token(secret), "next": "/agent-bridge/"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/agent-bridge/"
    assert "agent_bridge_sso=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_sso_cookie_supplies_actor_to_api(wm_paths) -> None:
    secret = "test-sso-secret"
    _write_identity_config(wm_paths, secret)
    client = TestClient(create_app(paths=wm_paths, admins={"alice"}))
    client.cookies.set("agent_bridge_sso", _token(secret))

    response = client.get("/api/v1/capabilities/mcp-services")

    assert response.status_code == 200
