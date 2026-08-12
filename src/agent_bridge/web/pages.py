from __future__ import annotations

from pathlib import Path


def capability_admin_page() -> str:
    build_dir = Path(__file__).parent.parent / "static" / "capabilities"
    index_path = build_dir / "index.html"

    if not index_path.exists():
        raise FileNotFoundError(
            f"Vue build not found at {index_path}. Run: npm --prefix frontend/capabilities run build"
        )

    return index_path.read_text(encoding="utf-8")
