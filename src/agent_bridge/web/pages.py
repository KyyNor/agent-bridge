from __future__ import annotations

import json
from pathlib import Path


def capability_admin_page(default_user: str = "root") -> str:
    default_user_json = json.dumps(default_user, ensure_ascii=False)

    # Try loading Vue build output first
    build_dir = Path(__file__).parent.parent / "static" / "capabilities"
    index_path = build_dir / "index.html"

    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        injection = f'<script>window.AGENT_BRIDGE_DEFAULT_USER={default_user_json};</script>'
        html = html.replace("</head>", f"{injection}</head>")
        return html

    # Fallback: old static HTML
    return _legacy_page(default_user_json)


def _legacy_page(default_user_json: str) -> str:
    return """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Agent Bridge - Capability Console</title>
    <link rel="stylesheet" href="/static/capabilities/app.css" />
  </head>
  <body>
    <div id="app"></div>
    <script>window.AGENT_BRIDGE_DEFAULT_USER = __AGENT_BRIDGE_DEFAULT_USER__;</script>
    <script src="/static/capabilities/app.js" defer></script>
  </body>
</html>""".replace("__AGENT_BRIDGE_DEFAULT_USER__", default_user_json)
