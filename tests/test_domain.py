from __future__ import annotations

import pytest

from wiki_manager.domain import (
    AccessDenied,
    BackendDocStatus,
    DocumentStatus,
    KbRole,
    Operation,
    SyncJobStatus,
    SyncStateStatus,
    can_manage_kb,
    can_view_kb,
    can_write_own_doc,
    require_admin_user,
)
from wiki_manager.slug import make_slug, unique_slug


def test_domain_enums_have_expected_values() -> None:
    assert KbRole.viewer.value == "viewer"
    assert KbRole.contributor.value == "contributor"
    assert KbRole.admin.value == "admin"
    assert DocumentStatus.active.value == "active"
    assert DocumentStatus.deleted.value == "deleted"
    assert SyncJobStatus.pending.value == "pending"
    assert SyncStateStatus.delete_failed.value == "delete_failed"


def test_slug_generation_keeps_readable_ascii_and_chinese() -> None:
    assert make_slug("API 说明 v2.pdf") == "api-说明-v2"
    assert make_slug("  Front End Guide.docx ") == "front-end-guide"


def test_slug_generation_falls_back_to_document() -> None:
    assert make_slug("!!!.pdf") == "document"


def test_unique_slug_adds_numeric_suffix() -> None:
    assert unique_slug("guide", {"guide", "guide-2"}) == "guide-3"


def test_permissions_by_role() -> None:
    assert can_view_kb(KbRole.viewer)
    assert can_view_kb(KbRole.contributor)
    assert can_view_kb(KbRole.admin)
    assert not can_write_own_doc(KbRole.viewer)
    assert can_write_own_doc(KbRole.contributor)
    assert can_manage_kb(KbRole.admin)


def test_global_admin_required() -> None:
    require_admin_user("root", {"root"})
    with pytest.raises(AccessDenied):
        require_admin_user("alice", {"root"})


def test_operation_values_are_stable() -> None:
    assert Operation.create.value == "create"
    assert Operation.update.value == "update"
    assert Operation.delete.value == "delete"


def test_backend_adapter_protocol_is_defined():
    from wiki_manager.domain import BackendAdapter

    assert BackendAdapter is not None


def test_backend_doc_status_defaults():
    status = BackendDocStatus(status="completed", chunk_count=5, progress=1.0, error_message=None)
    assert status.status == "completed"
    assert status.chunk_count == 5
