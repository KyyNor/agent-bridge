from __future__ import annotations

import asyncio
import json

import pytest

from agent_bridge.core.domain import ValidationError
from agent_bridge.app.service import AgentBridgeService


SCRIPT_CODE = """
import json
import sys

envelope = json.load(sys.stdin)
print(json.dumps({
    "run_type": envelope["run_type"],
    "script_key": envelope["script_key"],
    "params": envelope["script_params"],
}, ensure_ascii=False))
"""


def test_script_service_upserts_and_test_runs_python_script(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    script = service.scripts.upsert_script(
        actor="root",
        script_key="workflow.import_tasks",
        name="Import tasks",
        description="Create workflow tasks from upstream data",
        language="python",
        code=SCRIPT_CODE,
        status="active",
        owner_type="system",
        owner_key="",
    )
    result = service.scripts.test_script(
        actor="root",
        script_key="workflow.import_tasks",
        script_params={"limit": 3},
        timeout_seconds=10,
    )

    assert script["script_key"] == "workflow.import_tasks"
    assert script["content_hash"]
    assert result["status"] == "success"
    assert result["result"] == {
        "run_type": "test",
        "script_key": "workflow.import_tasks",
        "params": {"limit": 3},
    }
    assert service.scripts.list_runs("root", "workflow.import_tasks")["runs"][0]["run_id"] == result["run_id"]


def test_builtin_run_script_executes_managed_script(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.echo",
        name="Echo",
        description="",
        language="python",
        code=SCRIPT_CODE,
        status="active",
        owner_type="system",
        owner_key="",
    )

    result = asyncio.run(
        service.capabilities.execute(
            "root",
            "built-in",
            "run_script",
            {"script_key": "system.echo", "script_params": {"name": "Ada"}},
        )
    )

    assert result["service"] == "built-in"
    assert result["tool_name"] == "run_script"
    assert result["success"] is True
    assert result["result"]["run_type"] == "mcp"
    assert result["result"]["result"]["params"] == {"name": "Ada"}


def test_script_run_requires_json_object_stdout(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.bad_stdout",
        name="Bad stdout",
        description="",
        language="python",
        code="print('not json')",
        status="active",
        owner_type="system",
        owner_key="",
    )

    with pytest.raises(ValidationError, match="script run failed"):
        service.scripts.test_script(
            actor="root",
            script_key="system.bad_stdout",
            script_params={},
            timeout_seconds=10,
        )

    run = service.scripts.list_runs("root", "system.bad_stdout")["runs"][0]
    assert run["status"] == "failed"
    assert "script stdout must be a JSON object" in run["error_message"]
