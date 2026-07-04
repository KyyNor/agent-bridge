from __future__ import annotations

import json
from pathlib import Path


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _sample_run(tmp_path: Path) -> tuple[Path, str]:
    run_dir = tmp_path / "run"
    transcript_dir = tmp_path / ".claude" / "projects" / "p" / "session" / "subagents" / "workflows" / "wf_123"
    run_dir.mkdir()
    transcript_dir.mkdir(parents=True)
    task_id = "task_abc"
    agent_id = "agent-one"

    (run_dir / "stdout.log").write_text(
        json.dumps(
            {
                "type": "UserMessage",
                "content": [
                    "ToolResultBlock(tool_use_id='call_launch', content='Workflow launched in background. "
                    f"Task ID: {task_id}\\nSummary: demo\\nTranscript dir: {transcript_dir}\\nRun ID: wf_123\\n', "
                    "is_error=False)"
                ],
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "UserMessage",
                "content": [
                    "ToolResultBlock(tool_use_id='call_output', content='<retrieval_status>success</retrieval_status>\\n\\n"
                    f"<task_id>{task_id}</task_id>\\n\\n<status>completed</status>\\n\\n<output>\\n"
                    "{\"ok\": true, \"answer\": \"done\"}\\n</output>', is_error=None)"
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _jsonl(
        transcript_dir / "journal.jsonl",
        [
            {"type": "started", "key": "k1", "agentId": agent_id},
            {"type": "result", "key": "k1", "agentId": agent_id, "result": {"answer": "done"}},
        ],
    )
    _jsonl(
        transcript_dir / f"{agent_id}.jsonl",
        [
            {
                "type": "user",
                "agentId": agent_id,
                "timestamp": "2026-07-02T14:40:32.285Z",
                "message": {"role": "user", "content": "请调用工具并返回结果"},
            },
            {
                "type": "assistant",
                "agentId": agent_id,
                "timestamp": "2026-07-02T14:40:34.303Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "我需要调用工具"}],
                    "usage": {"input_tokens": 12, "output_tokens": 3},
                },
            },
            {
                "type": "assistant",
                "agentId": agent_id,
                "timestamp": "2026-07-02T14:40:34.405Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tool_1", "name": "demo_tool", "input": {"x": 1}}],
                },
            },
            {
                "type": "user",
                "agentId": agent_id,
                "timestamp": "2026-07-02T14:40:49.700Z",
                "message": {
                    "role": "user",
                    "content": [{"tool_use_id": "tool_1", "type": "tool_result", "content": "{\"value\": 2}"}],
                },
            },
            {
                "type": "assistant",
                "agentId": agent_id,
                "timestamp": "2026-07-02T14:41:07.120Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "完成"}]},
            },
        ],
    )
    return run_dir, task_id


def test_build_subagent_detail_reads_claude_transcript_and_task_output(tmp_path: Path) -> None:
    from agent_bridge.agent_runtime.subagent_details import build_subagent_detail

    run_dir, task_id = _sample_run(tmp_path)

    detail = build_subagent_detail(run_dir, task_id)

    assert detail["task_id"] == task_id
    assert detail["transcript_dir"].endswith("/subagents/workflows/wf_123")
    assert detail["agent_count"] == 1
    assert detail["task_output_status"] == "completed"
    assert detail["task_output"] == '{"ok": true, "answer": "done"}'
    assert detail["agents"][0]["index"] == 1
    assert detail["agents"][0]["label"] == "子 Agent #1"
    assert detail["agents"][0]["prompt_preview"] == "请调用工具并返回结果"
    assert detail["agents"][0]["agent_id"] == "agent-one"
    assert detail["agents"][0]["result"] == {"answer": "done"}
    events = detail["agents"][0]["events"]
    assert [event["kind"] for event in events] == ["prompt", "thinking", "tool_call", "tool_result", "text"]
    assert events[0]["content"] == "请调用工具并返回结果"
    assert events[1]["content"] == "我需要调用工具"
    assert events[2]["tool_name"] == "demo_tool"
    assert events[3]["content"] == '{"value": 2}'
    assert events[4]["content"] == "完成"


def test_agent_runs_api_returns_subagent_detail(wm_paths, tmp_path: Path) -> None:
    """The unified /agent-runs/{run_key}/subagent-detail endpoint serves any
    agent run's sub-agent transcript — workflow or otherwise."""
    from fastapi.testclient import TestClient

    from agent_bridge.api.app import create_app
    from agent_bridge.app.service import AgentBridgeService

    run_dir, task_id = _sample_run(tmp_path)
    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.store.init_schema()
    # Seed an agent_runs row whose cwd points at the run directory; this is
    # what AgentService persists for every run (workflow or otherwise).
    svc.store.agent_runs.create(
        run_key="workflow_runA",
        agent_name="workflow",
        workflow_key="page-report",
        workflow_run_id="run_1",
        cwd=str(run_dir),
        status="completed",
        ok=True,
        prompt="",
    )

    client = TestClient(create_app(wm_paths, {"root"}))
    response = client.get(
        f"/agent-runs/workflow_runA/subagent-detail?task_id={task_id}",
        headers={"X-Agent-Bridge-User": "root"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_id"] == task_id
    assert body["agents"][0]["events"][0]["content"] == "请调用工具并返回结果"

    # The old workflow-scoped endpoint is gone (404).
    old = client.get(
        f"/workflow-runs/run_1/subagent-detail?task_id={task_id}",
        headers={"X-Agent-Bridge-User": "root"},
    )
    assert old.status_code == 404

