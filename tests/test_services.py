from __future__ import annotations

from pathlib import Path

import pytest

from wiki_manager.config import ensure_directories
from wiki_manager.domain import AccessDenied, KbRole, NotFound
from wiki_manager.services import WikiManagerService


def test_admin_creates_kb_and_grants_member(wm_paths) -> None:
    ensure_directories(wm_paths)
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    kb = service.create_kb(actor="root", slug="frontend-docs", name="Frontend Docs", description="")
    service.grant_kb_member(actor="root", kb_slug="frontend-docs", linux_user="alice", role=KbRole.contributor)
    assert kb["slug"] == "frontend-docs"
    assert service.list_kbs(actor="alice")[0]["slug"] == "frontend-docs"


def test_non_admin_cannot_create_kb(wm_paths) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    with pytest.raises(AccessDenied):
        service.create_kb(actor="alice", slug="frontend-docs", name="Frontend Docs", description="")


def test_contributor_adds_doc_to_multiple_kbs(wm_paths, tmp_path: Path) -> None:
    ensure_directories(wm_paths)
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.create_kb("root", "backend-docs", "Backend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    service.grant_kb_member("root", "backend-docs", "alice", KbRole.contributor)
    source = tmp_path / "接口说明.pdf"
    source.write_bytes(b"version one")
    doc = service.add_document(actor="alice", source=source, kb_slugs=["frontend-docs", "backend-docs"], later=True)
    assert doc["slug"] == "接口说明"
    assert doc["current_version_no"] == 1
    assert len(service.list_docs(actor="alice", kb_slug="frontend-docs")) == 1
    assert len(service.list_docs(actor="alice", kb_slug="backend-docs")) == 1


def test_update_document_creates_new_version(wm_paths, tmp_path: Path) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "alice", KbRole.contributor)
    v1 = tmp_path / "Guide.pdf"
    v2 = tmp_path / "Guide-v2.pdf"
    v1.write_bytes(b"one")
    v2.write_bytes(b"two")
    doc = service.add_document("alice", v1, ["frontend-docs"], later=True)
    updated = service.update_document("alice", doc["slug"], v2, later=True)
    assert updated["current_version_no"] == 2


def test_viewer_cannot_add_document(wm_paths, tmp_path: Path) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    service.grant_kb_member("root", "frontend-docs", "bob", KbRole.viewer)
    source = tmp_path / "Guide.pdf"
    source.write_bytes(b"one")
    with pytest.raises(AccessDenied):
        service.add_document("bob", source, ["frontend-docs"], later=True)


def test_invisible_kb_returns_not_found(wm_paths) -> None:
    service = WikiManagerService.create(wm_paths, admins={"root"})
    service.init_system()
    service.create_kb("root", "frontend-docs", "Frontend Docs", "")
    with pytest.raises(NotFound):
        service.list_docs(actor="alice", kb_slug="frontend-docs")
