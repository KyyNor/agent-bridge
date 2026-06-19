from __future__ import annotations

import httpx
import respx

from agent_bridge.capability_hub.sources.openapi.http_client import OpenApiHttpClient


@respx.mock
def test_openapi_http_client_maps_path_query_headers_body_and_auth() -> None:
    route = respx.post("https://api.example.test/v1/pets/p1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = OpenApiHttpClient()

    result = client.call_tool(
        {
            "base_url": "https://api.example.test/v1",
            "headers": {"X-Static": "yes"},
            "auth_config": {"type": "bearer", "token": "token-1"},
        },
        {
            "method": "POST",
            "path": "/pets/{petId}",
            "request_mapping": {
                "path": {"petId": "petId"},
                "query": {"dry_run": "dryRun"},
                "headers": {"X-Request-ID": "requestId"},
                "body": "body",
            },
        },
        {"petId": "p1", "dryRun": True, "requestId": "r1", "body": {"name": "Ada"}},
    )

    assert result["status_code"] == 200
    assert result["body"] == {"ok": True}
    request = route.calls.last.request
    assert request.url.params["dry_run"] == "true"
    assert request.headers["authorization"] == "Bearer token-1"
    assert request.headers["x-static"] == "yes"
    assert request.headers["x-request-id"] == "r1"
    assert request.content == b'{"name":"Ada"}'


@respx.mock
def test_openapi_http_client_supports_api_key_header_and_text_response() -> None:
    route = respx.get("https://api.example.test/status").mock(return_value=httpx.Response(204, text=""))
    result = OpenApiHttpClient().call_tool(
        {
            "base_url": "https://api.example.test",
            "headers": {},
            "auth_config": {"type": "api_key", "header": "X-API-Key", "value": "key-1"},
        },
        {"method": "GET", "path": "/status", "request_mapping": {"path": {}, "query": {}, "headers": {}, "body": None}},
        {},
    )

    assert result["status_code"] == 204
    assert result["body"] == ""
    assert route.calls.last.request.headers["x-api-key"] == "key-1"
