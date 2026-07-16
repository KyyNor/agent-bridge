from __future__ import annotations

import asyncio
import socket
import time
from contextlib import contextmanager

import pytest

from agent_bridge.core.domain import ValidationError
from agent_bridge.app.service import AgentBridgeService
from agent_bridge.runtime.server_process import start_server, stop_server


MAIN_SCRIPT = """
def main(envelope):
    print("hello from stdout")
    return {
        "run_type": envelope["run_type"],
        "script_key": envelope["script_key"],
        "params": envelope["script_params"],
}
"""


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


def test_script_service_upserts_and_test_runs_python_script(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    script = service.scripts.upsert_script(
        actor="root",
        script_key="workflow.import_tasks",
        name="Import tasks",
        description="Create workflow tasks from upstream data",
        language="python",
        code=MAIN_SCRIPT,
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


@pytest.mark.process
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


@pytest.mark.process
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
