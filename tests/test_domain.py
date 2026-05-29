from wiki_manager.domain import KbRole, SyncJobStatus


def test_domain_enums_have_expected_values() -> None:
    assert KbRole.viewer.value == "viewer"
    assert KbRole.contributor.value == "contributor"
    assert KbRole.admin.value == "admin"
    assert SyncJobStatus.pending.value == "pending"
