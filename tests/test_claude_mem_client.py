from __future__ import annotations

import httpx

from agent_bridge.memory_management.claude_mem.client import ClaudeMemClient


def _response(url: str, payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("GET", url))


def test_claude_mem_timeline_uses_observations_endpoint_with_offset(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _response(
            url,
            {
                "items": [
                    {
                        "id": 42,
                        "type": "observation",
                        "summary": "remembered",
                        "created_at": "2026-06-23T14:00:00Z",
                    }
                ],
                "hasMore": True,
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    result = ClaudeMemClient("http://worker").timeline(10, "20")

    assert calls[0]["url"] == "http://worker/api/observations"
    assert calls[0]["params"] == {"limit": 10, "offset": 20}
    assert result["next_cursor"] == "30"
    assert result["items"][0]["id"] == "42"


def test_claude_mem_search_raises_mcp_style_error(monkeypatch):
    def fake_get(url, **kwargs):
        return _response(url, {"content": [{"type": "text", "text": "bad query"}], "isError": True})

    monkeypatch.setattr(httpx, "get", fake_get)

    try:
        ClaudeMemClient("http://worker").search("test", 3)
    except RuntimeError as exc:
        assert str(exc) == "bad query"
    else:
        raise AssertionError("expected search error")
