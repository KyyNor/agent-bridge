from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def external_dashboard_url(repo_key: str, internal_url: str | None) -> str | None:
    if not internal_url:
        return None
    parts = urlsplit(internal_url)
    return urlunsplit(("", "", f"/dashboard/{repo_key}/", parts.query, parts.fragment))
