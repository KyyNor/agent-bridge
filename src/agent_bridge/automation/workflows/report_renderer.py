"""HTML 报告渲染协调。

将工作流摘要运行的 Markdown 产物交给 ``design_html_report`` 能力，产出一份
合并的 HTML 报告并落盘为产物。失败不得影响主运行状态，由调度方捕获并记录。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agent_bridge.core.domain import ValidationError
from agent_bridge.automation.workflows.models import WorkflowType

logger = logging.getLogger(__name__)


def render_run_html_report(
    *,
    store: Any,
    agent_service: Any,
    skills: Any,
    save_artifact: Callable[..., dict[str, Any]],
    workflow_key: str,
    profile_key: str,
    run_id: str,
    actor: str,
) -> dict[str, Any]:
    """Generate a human-readable HTML report for a completed summary run.

    Only fires for ``workflow_type='summary'`` workflows that produced at
    least one Markdown artifact this run. The report is one consolidated
    HTML document stored at ``out/report.html`` (overwriting any previous
    report for the same task via the existing ``is_current`` mechanism).

    Failures here must NOT alter the main workflow run status — callers
    (the scheduler) wrap this in try/except and log a warning.
    """
    # Local import to avoid a module-load cycle (reporter -> models).
    from agent_bridge.automation.workflows.reporter import (
        HTML_MAX_BYTES,
        HTML_REPORT_SCHEMA,
        build_report_prompt,
        looks_like_html,
        summarize_agent_events,
    )

    workflow = store.get_workflow_definition(workflow_key)
    if workflow is None:
        return {"status": "skipped", "reason": "workflow not found"}
    if (workflow.get("workflow_type") or WorkflowType.operation.value) != WorkflowType.summary.value:
        return {"status": "skipped", "reason": "not a summary workflow"}

    markdown_items = store.search_workflow_artifacts(
        profile_key=profile_key,
        query=None,
        tags=[],
        path=None,
        workflow_key=workflow_key,
        run_id=run_id,
        include_history=False,
        format="markdown",
        limit=50,
    )
    if not markdown_items:
        return {"status": "no_markdown"}

    # Derive the task_key from the markdown artifacts so the HTML report
    # shares it (and thus participates in the same is_current overwrite).
    task_key = markdown_items[0].get("task_key")
    task_version = markdown_items[0].get("task_version") or ""

    run_logs = store.list_workflow_run_logs(run_id)
    agent_runs = store.agent_runs.list(
        workflow_key=workflow_key, workflow_run_id=run_id, limit=50
    )
    agent_events_summary = summarize_agent_events(agent_runs)

    if agent_service is None:
        raise ValidationError("agent service is not configured")
    if skills is None:
        raise ValidationError("skill service is not configured")

    skill_payload = skills.get_skill(actor, "design_html_report")
    skill_prompt = skill_payload["prompt"]
    run_row = store.get_workflow_run(run_id) or {}

    prompt = build_report_prompt(
        skill_name="design_html_report",
        skill_prompt=skill_prompt,
        workflow=workflow,
        run={"run_id": run_id, "task_key": task_key},
        markdown_artifacts=markdown_items,
        run_logs=run_logs,
        agent_events_summary=agent_events_summary,
    )

    import asyncio

    result = asyncio.run(
        agent_service.run(
            prompt=prompt,
            agent_name="workflow_html_reporter",
            profile=profile_key,
            output_schema=HTML_REPORT_SCHEMA,
            actor=actor,
            workflow_key=workflow_key,
            run_id=run_id,
            timeout=900,
        )
    )

    registry = getattr(agent_service, "control_registry", None)
    if registry is not None and registry.is_workflow_stop_requested(run_id):
        return {"status": "stopped"}
    if not result.ok:
        raise ValidationError(f"html report agent failed: {result.error or 'unknown'}")
    payload = result.result or {}
    if not isinstance(payload, dict):
        raise ValidationError("html report agent returned non-object result")

    html = str(payload.get("html") or "")
    if not looks_like_html(html):
        raise ValidationError("html report agent output is not a valid HTML document")
    if len(html.encode("utf-8")) > HTML_MAX_BYTES:
        raise ValidationError("html report exceeds size limit")

    title = str(payload.get("title") or "Workflow 报告")[:200]
    summary = str(payload.get("summary") or "")[:2000]
    source_ids = [str(x) for x in payload.get("source_artifact_ids") or [] if isinstance(x, str)]

    saved = save_artifact(
        workflow_key=workflow_key,
        profile_key=profile_key,
        run_id=run_id,
        task_key=task_key,
        task_version=task_version,
        title=title,
        path="out/report.html",
        tags=["html-report"],
        format="html",
        summary=summary,
        content=html,
        metadata={
            "derived_from_artifact_ids": source_ids,
            "report_kind": "human_html",
        },
    )
    logger.info(
        "Workflow HTML 报告已生成 run_id=%s workflow=%s bytes=%d",
        run_id,
        workflow_key,
        len(html.encode("utf-8")),
    )
    return {"status": "generated", "artifact": saved}
