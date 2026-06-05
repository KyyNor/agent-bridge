from __future__ import annotations

import json
from pathlib import Path


def capability_admin_page(default_user: str = "root") -> str:
    default_user_json = json.dumps(default_user, ensure_ascii=False)
    build_dir = Path(__file__).parent.parent / "static" / "capabilities"
    index_path = build_dir / "index.html"

    if not index_path.exists():
        raise FileNotFoundError(
            f"Vue build not found at {index_path}. Run: npm --prefix frontend/capabilities run build"
        )

    html = index_path.read_text(encoding="utf-8")
    injection = f'<script>window.AGENT_BRIDGE_DEFAULT_USER={default_user_json};</script>'
    return html.replace("</head>", f"{injection}</head>")
