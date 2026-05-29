from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MockBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def upsert_document(self, kb_slug: str, doc_slug: str, version_no: int, archive_path: str) -> str:
        kb_dir = self.root / kb_slug
        kb_dir.mkdir(parents=True, exist_ok=True)
        backend_doc_id = f"{kb_slug}:{doc_slug}"
        payload = {
            "backend_doc_id": backend_doc_id,
            "doc_slug": doc_slug,
            "version_no": version_no,
            "archive_path": archive_path,
            "status": "active",
        }
        (kb_dir / f"{doc_slug}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return backend_doc_id

    def delete_document(self, kb_slug: str, doc_slug: str) -> None:
        path = self.root / kb_slug / f"{doc_slug}.json"
        path.unlink(missing_ok=True)

    def read_document(self, kb_slug: str, doc_slug: str) -> dict[str, Any] | None:
        path = self.root / kb_slug / f"{doc_slug}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
