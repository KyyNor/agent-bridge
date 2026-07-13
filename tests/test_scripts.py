from __future__ import annotations

import asyncio
import socket
import time
from contextlib import contextmanager
from dataclasses import replace

import pytest

from agent_bridge.core.domain import ValidationError
from agent_bridge.app.service import AgentBridgeService
from agent_bridge.runtime.server_process import start_server, stop_server
from agent_bridge.system_config.scripts.service import DEFAULT_SCRIPT_ACTOR


MAIN_SCRIPT = """
def main(envelope):
    print("hello from stdout")
    return {
        "run_type": envelope["run_type"],
        "script_key": envelope["script_key"],
        "params": envelope["script_params"],
}
"""

SCRIPT_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"repo": {"type": "string"}},
    "required": ["repo"],
}

PERMISSIVE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}


def _validation_workflow() -> dict:
    return {
        "workflow_key": "script-validation",
        "name": "Script Validation",
        "description": "",
        "profile_key": "script-validation",
        "definition": {"nodes": [], "edges": []},
        "status": "active",
        "workflow_type": "operation",
    }


def _ensure_validation_profile(service: AgentBridgeService) -> None:
    service.store.upsert_project_profile(
        profile_key="script-validation",
        name="Script Validation",
        created_by="root",
    )


def test_script_input_schema_round_trip_and_validation(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    saved = service.scripts.upsert_script(actor="root", script_key="schema-test", name="Schema", description="", language="python", code=MAIN_SCRIPT, input_schema=SCRIPT_INPUT_SCHEMA, status="active", owner_type="system", owner_key="")
    assert saved["input_schema"] == SCRIPT_INPUT_SCHEMA
    with pytest.raises(ValidationError, match="expected_schema="):
        service.scripts.test_script(actor="root", script_key="schema-test", script_params={"repo": 1}, timeout_seconds=10)


def test_script_output_schema_round_trip_and_validation(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    output_schema = {
        "type": "object",
        "required": ["items"],
        "properties": {"items": {"type": "array"}},
    }
    service.scripts.upsert_script(
        actor="root",
        script_key="schema.output",
        name="Output",
        description="",
        language="python",
        code="def main(envelope):\n    return {'items': []}\n",
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        output_schema=output_schema,
        status="active",
        owner_type="system",
        owner_key="",
    )
    script = service.scripts.get_script("root", "schema.output")
    assert script["output_schema"] == output_schema

    service.scripts.upsert_script(
        actor="root",
        script_key="schema.bad-output",
        name="Bad",
        description="",
        language="python",
        code="def main(envelope):\n    return {'items': 'bad'}\n",
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        output_schema=output_schema,
        status="active",
        owner_type="system",
        owner_key="",
    )
    with pytest.raises(ValidationError, match="output_schema"):
        service.scripts.test_script(
            actor="root",
            script_key="schema.bad-output",
            script_params={},
            timeout_seconds=10,
        )
    run = service.scripts.list_runs("root", "schema.bad-output")["runs"][0]
    assert run["status"] == "failed"
    assert "output_schema invalid" in run["error_message"]


def test_builtin_workflow_validator_script_can_be_overridden_and_reset(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    default = service.scripts.get_script("root", "system.validate_workflow")
    assert default["is_builtin"] is True
    assert default["source"] == "default"
    assert default["status"] == "active"
    assert default["language"] == "python"
    assert "workflow" in default["input_schema"]["required"]
    assert default["input_schema"]["properties"]["workflow"]["required"] == [
        "workflow_key",
        "name",
        "description",
        "profile_key",
        "definition",
        "status",
        "workflow_type",
    ]

    with pytest.raises(ValidationError, match="cannot delete built-in"):
        service.scripts.delete_script("root", "system.validate_workflow")
    with pytest.raises(ValidationError, match="cannot disable built-in"):
        service.scripts.upsert_script(
            actor="root",
            script_key="system.validate_workflow",
            name="ignored",
            description="ignored",
            language="python",
            code=default["code"],
            input_schema=default["input_schema"],
            output_schema=default["output_schema"],
            status="disabled",
            owner_type="system",
            owner_key="",
        )

    service.scripts.upsert_script(
        actor="root",
        script_key="system.validate_workflow",
        name="ignored",
        description="ignored",
        language="python",
        code=(
            "def main(envelope):\n"
            "    return {'valid': True, 'errors': [], 'warnings': [{'source': 'override'}]}\n"
        ),
        input_schema=default["input_schema"],
        output_schema=default["output_schema"],
        status="active",
        owner_type="system",
        owner_key="",
    )
    assert service.scripts.get_script("root", "system.validate_workflow")["source"] == "database"
    assert service.scripts.get_script("root", "system.validate_workflow")["is_builtin"] is True

    restored = service.scripts.reset_script("root", "system.validate_workflow")
    assert restored["source"] == "default"


def test_system_prefix_does_not_define_builtin_identity(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    custom = service.scripts.upsert_script(
        actor="root",
        script_key="system.user_owned",
        name="User Owned",
        description="",
        language="python",
        code="def main(envelope):\n    return {}\n",
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )

    assert custom["is_builtin"] is False
    assert {item["script_key"]: item["is_builtin"] for item in service.scripts.list_scripts("root")} == {
        "system.user_owned": False,
        "system.validate_workflow": True,
    }


def test_materialized_default_script_refreshes_after_repository_upgrade_and_preserves_runs(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    original = service.scripts.reset_script("root", "system.validate_workflow")
    historical = service.store.scripts.create_script_run(
        run_id="script_run_before_upgrade",
        script_key="system.validate_workflow",
        run_type="test",
        params={},
        result={"valid": True, "errors": [], "warnings": []},
        stdout="",
        stderr="",
        status="success",
        exit_code=0,
        error_message=None,
        duration_ms=1,
        actor="root",
    )
    upgraded_path = wm_paths.root / "upgraded_validate_workflow.py"
    upgraded_path.write_text(
        "# upgraded-default\n"
        "def main(envelope):\n"
        "    return {'valid': True, 'errors': [], 'warnings': []}\n",
        encoding="utf-8",
    )
    definition = service.scripts._builtins["system.validate_workflow"]
    service.scripts._builtins["system.validate_workflow"] = replace(
        definition,
        default_path=upgraded_path,
        input_schema={**definition.input_schema, "$comment": "input-schema-v2"},
        output_schema={**definition.output_schema, "$comment": "output-schema-v2"},
    )

    listed = {item["script_key"]: item for item in service.scripts.list_scripts("root")}
    refreshed = service.scripts.get_script("root", "system.validate_workflow")

    assert "upgraded-default" in listed["system.validate_workflow"]["code_preview"]
    assert "upgraded-default" in refreshed["code"]
    assert refreshed["updated_by"] == DEFAULT_SCRIPT_ACTOR
    assert refreshed["source"] == "default"
    assert refreshed["input_schema"]["$comment"] == "input-schema-v2"
    assert refreshed["output_schema"]["$comment"] == "output-schema-v2"
    assert service.scripts.get_run("root", historical["run_id"])["run_id"] == historical["run_id"]
    assert original["content_hash"] != refreshed["content_hash"]

    run_upgraded_path = wm_paths.root / "run_upgraded_validate_workflow.py"
    run_upgraded_path.write_text(
        "# run-upgraded-default\n"
        "def main(envelope):\n"
        "    return {'valid': True, 'errors': [], 'warnings': []}\n",
        encoding="utf-8",
    )
    service.scripts._builtins["system.validate_workflow"] = replace(
        service.scripts._builtins["system.validate_workflow"],
        default_path=run_upgraded_path,
    )

    run = service.scripts.test_script(
        actor="root",
        script_key="system.validate_workflow",
        script_params={"workflow": _validation_workflow()},
        timeout_seconds=10,
    )

    assert run["status"] == "success"
    assert "run-upgraded-default" in service.scripts.get_script("root", "system.validate_workflow")["code"]
    assert service.scripts.get_run("root", historical["run_id"])["run_id"] == historical["run_id"]


def test_user_builtin_override_is_not_replaced_by_repository_upgrade(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    default = service.scripts.get_script("root", "system.validate_workflow")
    override_code = (
        "def main(envelope):\n"
        "    return {'valid': True, 'errors': [], 'warnings': []}\n"
    )
    service.scripts.upsert_script(
        actor="root",
        script_key="system.validate_workflow",
        name="ignored",
        description="ignored",
        language="python",
        code=override_code,
        input_schema=default["input_schema"],
        output_schema=default["output_schema"],
        status="active",
        owner_type="system",
        owner_key="",
    )
    upgraded_path = wm_paths.root / "newer_validate_workflow.py"
    upgraded_path.write_text("# newer-default\n" + default["code"], encoding="utf-8")
    definition = service.scripts._builtins["system.validate_workflow"]
    service.scripts._builtins["system.validate_workflow"] = replace(
        definition,
        default_path=upgraded_path,
        input_schema={**definition.input_schema, "$comment": "must-not-replace-user-override"},
    )

    resolved = service.scripts.get_script("root", "system.validate_workflow")

    assert resolved["source"] == "database"
    assert resolved["updated_by"] == "root"
    assert resolved["code"] == override_code
    assert "$comment" not in resolved["input_schema"]


def test_script_with_run_history_cannot_be_deleted_but_can_be_disabled(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    script = service.scripts.upsert_script(
        actor="root",
        script_key="history.keep",
        name="History Keep",
        description="",
        language="python",
        code="def main(envelope):\n    return {}\n",
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )
    run = service.scripts.test_script(
        actor="root",
        script_key=script["script_key"],
        script_params={},
        timeout_seconds=10,
    )

    with pytest.raises(ValidationError, match="脚本已有运行历史，请改为 disabled，不能删除"):
        service.scripts.delete_script("root", script["script_key"])

    disabled = service.scripts.upsert_script(
        actor="root",
        script_key=script["script_key"],
        name=script["name"],
        description=script["description"],
        language=script["language"],
        code=script["code"],
        input_schema=script["input_schema"],
        output_schema=script["output_schema"],
        status="disabled",
        owner_type=script["owner_type"],
        owner_key=script["owner_key"],
    )
    assert disabled["status"] == "disabled"
    assert service.scripts.get_run("root", run["run_id"])["run_id"] == run["run_id"]


def test_script_without_run_history_can_be_deleted(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="history.empty",
        name="History Empty",
        description="",
        language="python",
        code="def main(envelope):\n    return {}\n",
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )

    assert service.scripts.delete_script("root", "history.empty") == {
        "script_key": "history.empty",
        "deleted": True,
    }
    assert service.store.scripts.get_script("history.empty") is None


def test_default_builtin_script_run_is_persisted_and_queryable(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    _ensure_validation_profile(service)

    with _started_server(wm_paths):
        result = service.scripts.test_script(
            actor="root",
            script_key="system.validate_workflow",
            script_params={"workflow": _validation_workflow()},
            timeout_seconds=10,
        )

    persisted = service.scripts.get_run("root", result["run_id"])
    listed = service.scripts.list_runs("root", "system.validate_workflow")["runs"]

    assert persisted["run_id"] == result["run_id"]
    assert persisted["status"] == "success"
    assert persisted["result"] == {"valid": True, "errors": [], "warnings": []}
    assert listed[0]["run_id"] == result["run_id"]
    assert service.scripts.get_script("root", "system.validate_workflow")["source"] == "default"


def test_reset_builtin_script_preserves_default_run_history(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    _ensure_validation_profile(service)

    with _started_server(wm_paths):
        result = service.scripts.test_script(
            actor="root",
            script_key="system.validate_workflow",
            script_params={"workflow": _validation_workflow()},
            timeout_seconds=10,
        )

    default = service.scripts.get_script("root", "system.validate_workflow")
    service.scripts.upsert_script(
        actor="root",
        script_key="system.validate_workflow",
        name="ignored",
        description="ignored",
        language="python",
        code=(
            "def main(envelope):\n"
            "    return {'valid': True, 'errors': [], 'warnings': [{'source': 'override'}]}\n"
        ),
        input_schema=default["input_schema"],
        output_schema=default["output_schema"],
        status="active",
        owner_type="system",
        owner_key="",
    )

    restored = service.scripts.reset_script("root", "system.validate_workflow")
    persisted = service.scripts.get_run("root", result["run_id"])
    listed = service.scripts.list_runs("root", "system.validate_workflow")["runs"]

    assert restored["source"] == "default"
    assert persisted["run_id"] == result["run_id"]
    assert listed[0]["run_id"] == result["run_id"]
    assert service.scripts.get_script("root", "system.validate_workflow")["source"] == "default"


def test_builtin_validate_workflow_tool_returns_structured_result(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    _ensure_validation_profile(service)
    provider = service.capabilities.builtin_providers["built-in"]

    tools = {tool.tool: tool for tool in provider.list_tools("root", None)}
    assert tools["validate_workflow"].input_schema["required"] == ["workflow"]
    assert tools["validate_workflow"].input_schema["properties"]["workflow"]["required"] == [
        "workflow_key",
        "name",
        "description",
        "profile_key",
        "definition",
        "status",
        "workflow_type",
    ]

    result = asyncio.run(
        service.capabilities.execute(
            "root",
            "built-in",
            "validate_workflow",
            {"workflow": _validation_workflow()},
        )
    )

    assert result["success"] is True
    assert result["result"] == {"valid": True, "errors": [], "warnings": []}


WORKFLOW_SCRIPT = """
from agent_bridge_runtime import workflow_get_task, workflow_set_task, workflow_run_log


def main(envelope):
    workflow_set_task([{"task_key": "page:a", "payload": {"page": "a"}}])
    leased = workflow_get_task()
    workflow_run_log(level="info", stage="lease", message="leased task", task_key=leased["task"]["task_key"])
    return {
        "task": leased["task"]["task_key"],
        "workflow": envelope["workflow"],
    }
"""


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _write_server_config(wm_paths, port: int) -> None:
    wm_paths.config_dir.mkdir(parents=True, exist_ok=True)
    wm_paths.server_config_path.write_text(
        f'host = "127.0.0.1"\nport = {port}\nadmins = ["root"]\n',
        encoding="utf-8",
    )


@contextmanager
def _started_server(wm_paths):
    port = _reserve_port()
    _write_server_config(wm_paths, port)
    start_server(wm_paths)
    try:
        yield port
    finally:
        stop_server(wm_paths)


def test_system_validate_workflow_default_script_runs_through_builtin_tool(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    _ensure_validation_profile(service)

    with _started_server(wm_paths):
        result = service.scripts.test_script(
            actor="root",
            script_key="system.validate_workflow",
            script_params={"workflow": _validation_workflow()},
            timeout_seconds=10,
        )

    assert result["status"] == "success"
    assert result["result"] == {"valid": True, "errors": [], "warnings": []}
    assert service.scripts.get_script("root", "system.validate_workflow")["source"] == "default"


def test_system_validate_workflow_returns_structured_parse_issues_for_malformed_definition(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    _ensure_validation_profile(service)
    workflow = _validation_workflow()
    workflow["definition"] = {
        "nodes": [
            {
                "id": "broken-agent",
                "type": "agent",
                "name": "Broken agent",
                "position": {"x": 0, "y": 0},
                "config": {"prompt": 42, "backend_key": "claude"},
            }
        ],
        "edges": [],
    }

    with _started_server(wm_paths):
        result = service.scripts.test_script(
            actor="root",
            script_key="system.validate_workflow",
            script_params={"workflow": workflow},
            timeout_seconds=10,
        )

    assert result["status"] == "success"
    assert result["result"] == {
        "valid": False,
        "errors": [
            {
                "scope": "node",
                "id": "broken-agent",
                "field": "prompt",
                "code": "invalid_type",
                "message": "字段类型不合法",
            }
        ],
        "warnings": [],
    }


def test_script_service_upserts_and_test_runs_python_script(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    script = service.scripts.upsert_script(
        actor="root",
        script_key="workflow.import_tasks",
        name="Import tasks",
        description="Create workflow tasks from upstream data",
        language="python",
        code=MAIN_SCRIPT,
        input_schema=PERMISSIVE_INPUT_SCHEMA,
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
    assert "hello from stdout" in result["stdout"]
    assert service.scripts.list_runs("root", "workflow.import_tasks")["runs"][0]["run_id"] == result["run_id"]


def test_builtin_run_script_executes_managed_script(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.echo",
        name="Echo",
        description="",
        language="python",
        code=MAIN_SCRIPT,
        input_schema=PERMISSIVE_INPUT_SCHEMA,
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


def test_platform_builtin_tool_schemas_include_chinese_descriptions(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    provider = service.capabilities.builtin_providers["built-in"]

    tools = {tool.tool: tool for tool in provider.list_tools("root", None)}

    assert tools["load_skill"].description == "加载一个由 Agent Bridge 管理的技能提示词。"
    assert tools["load_skill"].input_schema["properties"]["skill_name"]["description"] == "要加载的技能名称。"
    assert tools["run_script"].description == "运行托管的服务端 Python 脚本并返回 JSON 结果。"
    assert tools["run_script"].input_schema["properties"]["script_key"]["description"] == "要执行的脚本标识。"
    assert tools["run_script"].input_schema["properties"]["script_params"]["description"] == "传给脚本的 JSON 参数对象。"
    assert tools["run_script"].input_schema["properties"]["timeout_seconds"]["description"] == "脚本执行超时时间，单位为秒。"


def test_builtin_run_script_does_not_block_event_loop(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    def blocking_run_script(**kwargs):
        time.sleep(0.2)
        return {"ok": True}

    service.scripts.run_script = blocking_run_script

    async def run_concurrent_tasks():
        started = time.monotonic()
        execute_task = asyncio.create_task(
            service.capabilities.execute(
                "root",
                "built-in",
                "run_script",
                {"script_key": "system.slow"},
            )
        )
        await asyncio.sleep(0.01)
        elapsed = time.monotonic() - started
        await execute_task
        return elapsed

    assert asyncio.run(run_concurrent_tasks()) < 0.1


def test_script_run_requires_main_function(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.no_main",
        name="No Main",
        description="",
        language="python",
        code="print('still only logs')",
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )

    with pytest.raises(ValidationError, match="missing script main\\(envelope\\)"):
        service.scripts.test_script(
            actor="root",
            script_key="system.no_main",
            script_params={},
            timeout_seconds=10,
        )

    run = service.scripts.list_runs("root", "system.no_main")["runs"][0]
    assert run["status"] == "failed"
    assert "missing script main(envelope)" in run["error_message"]


def test_script_run_requires_main_to_return_object(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="system.bad_return",
        name="Bad Return",
        description="",
        language="python",
        code="def main(envelope):\n    return ['not', 'a', 'dict']\n",
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )

    with pytest.raises(ValidationError, match="script main\\(envelope\\) must return a JSON object"):
        service.scripts.test_script(
            actor="root",
            script_key="system.bad_return",
            script_params={},
            timeout_seconds=10,
        )


def test_runtime_helper_execute_signature_rejects_profile_override(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.scripts.upsert_script(
        actor="root",
        script_key="system.bad_helper_signature",
        name="Bad Helper Signature",
        description="",
        language="python",
        code=(
            "from agent_bridge_runtime import execute\n\n"
            "def main(envelope):\n"
            "    execute('built-in', 'load_skill', {'skill_name': 'design_workflow'}, profile_key='other')\n"
            "    return {}\n"
        ),
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )

    with _started_server(wm_paths):
        with pytest.raises(ValidationError, match="unexpected keyword argument 'profile_key'"):
            service.scripts.test_script(
                actor="root",
                script_key="system.bad_helper_signature",
                script_params={},
                timeout_seconds=10,
            )


def test_workflow_helpers_require_runtime_context(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.scripts.upsert_script(
        actor="root",
        script_key="system.workflow_missing_context",
        name="Workflow Missing Context",
        description="",
        language="python",
        code="from agent_bridge_runtime import workflow_get_task\n\ndef main(envelope):\n    workflow_get_task()\n    return {}\n",
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )

    with pytest.raises(ValidationError, match="workflow context is required"):
        service.scripts.test_script(
            actor="root",
            script_key="system.workflow_missing_context",
            script_params={},
            timeout_seconds=10,
        )


def test_script_workflow_helpers_round_trip_tasks_and_logs(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.store.upsert_project_profile(profile_key="report-plane", name="Report Plane", created_by="root")
    service.workflows.upsert_definition(
        actor="root",
        workflow_key="page-report",
        name="Page Report",
        description="",
        profile_key="report-plane",
        workflow_js="",
        status="active",
    )
    service.store.create_workflow_run(
        run_id="run_1",
        workflow_key="page-report",
        profile_key="report-plane",
        task_key=None,
        status="running",
        temp_dir="/tmp/workflow-run",
    )
    service.scripts.upsert_script(
        actor="root",
        script_key="system.workflow_roundtrip",
        name="Workflow Roundtrip",
        description="",
        language="python",
        code=WORKFLOW_SCRIPT,
        input_schema=PERMISSIVE_INPUT_SCHEMA,
        status="active",
        owner_type="system",
        owner_key="",
    )

    with _started_server(wm_paths):
        result = service.scripts.test_script(
            actor="root",
            script_key="system.workflow_roundtrip",
            script_params={},
            timeout_seconds=10,
            profile_key="report-plane",
            workflow_context={"workflow": True, "workflow_key": "page-report", "run_id": "run_1"},
        )

    assert result["result"]["task"] == "page:a"
    logs = service.workflows.list_run_logs("root", "run_1")
    assert logs[0]["message"] == "leased task"
