from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wiki_manager.domain import BackendDocStatus


class MockBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create_kb(self, slug: str, name: str) -> str:
        kb_dir = self.root / slug
        kb_dir.mkdir(parents=True, exist_ok=True)
        return slug

    def delete_kb(self, backend_kb_id: str) -> None:
        pass

    def upload(self, backend_kb_id: str, doc_slug: str, file_path: Path, filename: str) -> str:
        kb_dir = self.root / backend_kb_id
        kb_dir.mkdir(parents=True, exist_ok=True)
        backend_doc_id = f"{backend_kb_id}:{doc_slug}"
        payload = {
            "backend_doc_id": backend_doc_id,
            "doc_slug": doc_slug,
            "filename": filename,
            "status": "active",
        }
        (kb_dir / f"{doc_slug}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return backend_doc_id

    def delete(self, backend_kb_id: str, backend_doc_id: str) -> None:
        doc_slug = backend_doc_id.split(":")[-1]
        path = self.root / backend_kb_id / f"{doc_slug}.json"
        path.unlink(missing_ok=True)

    def get_status(self, backend_kb_id: str, backend_doc_id: str) -> BackendDocStatus:
        doc_slug = backend_doc_id.split(":")[-1]
        path = self.root / backend_kb_id / f"{doc_slug}.json"
        if not path.exists():
            return BackendDocStatus(status="not_found")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == "active":
            return BackendDocStatus(status="completed", chunk_count=1, progress=1.0)
        return BackendDocStatus(status=data.get("status", "unknown"))
