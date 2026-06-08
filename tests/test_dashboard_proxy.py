from __future__ import annotations

from agent_bridge.api.dashboard_proxy import _match_dashboard_path, _repo_key_from_referer, _rewrite_location, _upstream_path
from agent_bridge.codegraph.dashboard_urls import external_dashboard_url


def test_external_dashboard_url_keeps_token_without_exposing_vite_port() -> None:
    url = external_dashboard_url("headroom", "http://127.0.0.1:48001/?token=abc")

    assert url == "/dashboard/headroom/?token=abc"


def test_dashboard_proxy_rewrites_vite_root_redirect_to_repo_base() -> None:
    location = _rewrite_location("/", "headroom", "http://127.0.0.1:48001/?token=abc")

    assert location == "/dashboard/headroom/"


def test_dashboard_proxy_does_not_double_rewrite_vite_base_redirect() -> None:
    location = _rewrite_location(
        "/dashboard/headroom/?token=abc&theme=dark",
        "headroom",
        "http://127.0.0.1:48001/dashboard/headroom/?token=abc",
    )

    assert location == "/dashboard/headroom/?token=abc&theme=dark"


def test_dashboard_proxy_rewrites_absolute_vite_asset_redirect() -> None:
    location = _rewrite_location(
        "http://127.0.0.1:48001/assets/app.js",
        "headroom",
        "http://127.0.0.1:48001/?token=abc",
    )

    assert location == "/dashboard/headroom/assets/app.js"


def test_dashboard_proxy_extracts_repo_and_strips_prefix() -> None:
    assert _match_dashboard_path("/dashboard/headroom/assets/app.js") == ("headroom", "/assets/app.js")
    assert _match_dashboard_path("/dashboard/headroom/") == ("headroom", "/")
    assert _match_dashboard_path("/code-repo/repositories") == (None, "")


def test_dashboard_proxy_keeps_vite_base_for_upstream_module_requests() -> None:
    assert _upstream_path("headroom", "/@vite/client") == "/dashboard/headroom/@vite/client"
    assert _upstream_path("headroom", "/src/main.tsx") == "/dashboard/headroom/src/main.tsx"


def test_dashboard_proxy_can_route_root_data_endpoints_from_referer() -> None:
    headers = [(b"referer", b"http://127.0.0.1:8765/dashboard/headroom/?token=abc&theme=dark")]

    assert _repo_key_from_referer(headers) == "headroom"
