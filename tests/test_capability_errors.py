from agent_bridge.capability_hub.errors import capability_failure, failure_metadata, with_log_id
from agent_bridge.capability_hub.models import CallLogStatus, FailureOwner, FailureStage
from agent_bridge.core.domain import NotFound, ValidationError


def test_capability_failure_keeps_original_exception_unchanged() -> None:
    original = ValidationError("上游执行失败")

    typed = capability_failure(
        original,
        status=CallLogStatus.blocked.value,
        stage=FailureStage.profile_policy.value,
        owner=FailureOwner.policy.value,
        error_type="profile_policy_blocked",
        resource_type="wiki_kb",
        resource_key="docs",
    )

    assert typed is not original
    assert str(original) == "上游执行失败"
    assert not hasattr(original, "failure")
    assert isinstance(typed, ValidationError)
    assert failure_metadata(typed).resource_key == "docs"


def test_with_log_id_returns_same_domain_error_kind_and_preserves_metadata() -> None:
    typed = capability_failure(
        NotFound("工具不存在"),
        stage=FailureStage.capability_registry.value,
        owner=FailureOwner.platform.value,
        error_type="capability_registry_error",
    )

    enriched = with_log_id(typed, "call_123")

    assert isinstance(enriched, NotFound)
    assert str(typed) == "工具不存在"
    assert str(enriched) == "工具不存在 (log_id: call_123)"
    assert failure_metadata(enriched).stage == FailureStage.capability_registry.value
    assert failure_metadata(enriched).log_id == "call_123"
