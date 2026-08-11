from __future__ import annotations


def render_runner() -> str:
    return '''from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module(script_path: Path):
    spec = importlib.util.spec_from_file_location("managed_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load script module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    try:
        run_dir = Path.cwd()
        envelope = json.loads((run_dir / "envelope.json").read_text(encoding="utf-8"))
        module = _load_module(run_dir / "script.py")
        entry = getattr(module, "main", None)
        if entry is None or not callable(entry):
            raise RuntimeError("missing script main(envelope)")
        result = entry(envelope)
        if not isinstance(result, dict):
            raise RuntimeError("script main(envelope) must return a JSON object")
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''


def render_runtime_helper() -> str:
    return '''from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def _base_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Bridge-User": os.environ.get("AGENT_BRIDGE_USER", "root"),
    }
    profile = os.environ.get("AGENT_BRIDGE_PROFILE", "").strip()
    if profile:
        headers["X-Agent-Bridge-MetaMCP-Profile"] = profile
    if os.environ.get("AGENT_BRIDGE_WORKFLOW", "").strip().lower() == "true":
        headers["X-Agent-Bridge-Workflow"] = "true"
        headers["X-Agent-Bridge-Workflow-Key"] = os.environ.get("AGENT_BRIDGE_WORKFLOW_KEY", "")
        headers["X-Agent-Bridge-Workflow-Run-Id"] = os.environ.get("AGENT_BRIDGE_WORKFLOW_RUN_ID", "")
        capability = os.environ.get("AGENT_BRIDGE_WORKFLOW_CAPABILITY", "").strip()
        if capability:
            headers["X-Agent-Bridge-Workflow-Capability"] = capability
    return headers


def _base_url() -> str:
    base_url = os.environ.get("AGENT_BRIDGE_API_BASE", "").rstrip("/")
    if not base_url:
        raise RuntimeError("AGENT_BRIDGE_API_BASE is not configured")
    return base_url


def _require_workflow_context() -> None:
    enabled = os.environ.get("AGENT_BRIDGE_WORKFLOW", "").strip().lower() == "true"
    workflow_key = os.environ.get("AGENT_BRIDGE_WORKFLOW_KEY", "").strip()
    run_id = os.environ.get("AGENT_BRIDGE_WORKFLOW_RUN_ID", "").strip()
    if not enabled or not workflow_key or not run_id:
        raise RuntimeError("workflow context is required")


def _post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _base_url() + path,
        data=data,
        headers=_base_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Agent Bridge execute failed: HTTP {exc.code}: {body}") from exc


def execute(service: str, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return _post_json(
        "/capabilities/execute",
        {
            "service": service,
            "tool_name": tool_name,
            "params": params or {},
        },
    )


def workflow_get_task() -> dict[str, Any]:
    _require_workflow_context()
    return _post_json("/runtime/workflow/get-task", {})


def workflow_set_task(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    _require_workflow_context()
    return _post_json("/runtime/workflow/set-task", {"tasks": tasks})


def workflow_run_log(
    level: str = "info",
    stage: str = "",
    message: str = "",
    task_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _require_workflow_context()
    return _post_json(
        "/runtime/workflow/run-log",
        {
            "level": level,
            "stage": stage,
            "message": message,
            "task_key": task_key,
            "payload": payload or {},
        },
    )
'''
