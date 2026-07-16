from agent_bridge.agent_runtime.control import RunControlRegistry


def test_stop_before_register_is_replayed_to_later_run() -> None:
    registry = RunControlRegistry(tombstone_ttl_seconds=60)

    assert registry.request_stop("design_script_client_key") is True
    control = registry.register("design_script_client_key")

    assert control.stop_requested.is_set()
    assert registry.is_active("design_script_client_key") is True


def test_finish_removes_active_control_and_repeated_stop_is_idempotent() -> None:
    registry = RunControlRegistry(tombstone_ttl_seconds=60)
    registry.register("agent_run_1")

    assert registry.request_stop("agent_run_1") is True
    registry.finish("agent_run_1")

    assert registry.is_active("agent_run_1") is False
    assert registry.request_stop("agent_run_1") is True


def test_workflow_stop_cancels_all_attached_agents() -> None:
    registry = RunControlRegistry(tombstone_ttl_seconds=60)
    registry.register_workflow("workflow_run_1")
    first = registry.register("agent_1", workflow_run_id="workflow_run_1")
    second = registry.register("agent_2", workflow_run_id="workflow_run_1")

    assert registry.request_workflow_stop("workflow_run_1") is True
    assert first.stop_requested.is_set()
    assert second.stop_requested.is_set()


def test_tombstone_expires_and_is_not_replayed() -> None:
    registry = RunControlRegistry(tombstone_ttl_seconds=0)

    registry.request_stop("expired")
    control = registry.register("expired")

    assert control.stop_requested.is_set() is False
