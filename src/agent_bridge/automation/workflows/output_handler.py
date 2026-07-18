from __future__ import annotations

import json
from typing import Any

from agent_bridge.automation.workflows.definition import OutputNode
from agent_bridge.automation.workflows.handlers import (
    NodeExecutionContext,
    NodeExecutionError,
    NodeExecutionResult,
    build_agent_prompt,
)
from agent_bridge.automation.workflows.references import render_text

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "summary", "content"],
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "content": {"type": "string"}},
}
HTML_MAX_BYTES = 5 * 1024 * 1024


class OutputHandler:
    def __init__(self, *, agent_service: Any, skill_service: Any, workflow_service: Any) -> None:
        self.agent_service = agent_service
        self.skill_service = skill_service
        self.workflow_service = workflow_service

    async def execute(self, node: OutputNode, context: NodeExecutionContext) -> NodeExecutionResult:
        try:
            output = await self._generate(node, context)
            artifact = self.workflow_service.save_artifact(
                workflow_key=context.workflow["workflow_key"], profile_key=context.workflow["profile_key"],
                run_id=context.run_id, task_key=(context.task or {}).get("task_key"),
                task_version=str((context.task or {}).get("task_version") or ""), title=output["title"],
                path=render_text(node.config.path, context.template_context()), tags=node.config.tags,
                format=node.config.format, summary=output["summary"], content=output["content"], metadata={"node_id": node.id},
                producer_node_id=node.id, producer_node_fingerprint=context.node_fingerprint,
            )
            output["artifact_ids"] = [artifact["artifact_id"]]
            return NodeExecutionResult(output=output, agent_run_key=output.pop("_agent_run_key"), artifact_ids=output["artifact_ids"])
        except Exception as exc:
            error = exc if isinstance(exc, NodeExecutionError) else NodeExecutionError(node.id, str(exc))
            if node.config.format == "html":
                return NodeExecutionResult(status="warning", error=str(error))
            if isinstance(exc, NodeExecutionError):
                raise
            raise error from exc

    async def _generate(self, node: OutputNode, context: NodeExecutionContext) -> dict[str, Any]:
        config = node.config
        prompt = build_agent_prompt(
            skill_service=self.skill_service, actor=context.actor, skill_names=config.skill_names,
            prompt=config.prompt, context=context.template_context(),
        )
        if config.format == "markdown":
            ancestor_ids = self._ancestors(node.id, context)
            ancestors = {
                key: context.nodes[key].get("output", {})
                for key in sorted(ancestor_ids)
                if key in context.nodes
            }
            prompt += "\n\n[上游节点输出]\n" + json.dumps(ancestors, ensure_ascii=False, sort_keys=True)
        else:
            direct_sources = {
                edge.source for edge in context.graph.edges if edge.target == node.id
            }
            markdown = next(
                (
                    context.nodes[source].get("output", {})
                    for source in sorted(direct_sources)
                    if source in context.nodes
                    and context.nodes[source].get("type") == "output"
                    and context.nodes[source].get("format") == "markdown"
                ),
                None,
            )
            if not markdown:
                raise NodeExecutionError(node.id, "HTML 输出缺少 Markdown 主产物")
            prompt += "\n\n[Markdown 主产物]\n" + json.dumps(markdown, ensure_ascii=False)
        result = await self.agent_service.run(
            prompt=prompt, agent_name=f"workflow_{node.id}", profile=context.workflow["profile_key"] if config.mcp_enabled else None,
            workflow_key=context.workflow["workflow_key"], run_id=context.run_id, output_schema=OUTPUT_SCHEMA,
            backend_key=config.backend_key, skills=None, actor=context.actor,
        )
        if not result.ok or not isinstance(result.result, dict):
            raise NodeExecutionError(node.id, result.error or "output agent failed")
        output = dict(result.result)
        if not all(isinstance(output.get(key), str) for key in ("title", "summary", "content")):
            raise NodeExecutionError(node.id, "输出结果不符合固定结构")
        content = output["content"].strip()
        if not content:
            raise NodeExecutionError(node.id, "输出内容不能为空")
        if config.format == "html" and ("<html" not in content.lower() and "<body" not in content.lower()):
            raise NodeExecutionError(node.id, "HTML 输出必须包含 html 或 body 标签")
        if config.format == "html" and len(content.encode("utf-8")) > HTML_MAX_BYTES:
            raise NodeExecutionError(node.id, "HTML 输出超过 5 MiB")
        output["content"] = content
        output["_agent_run_key"] = result.run_key
        return output

    @staticmethod
    def _ancestors(node_id: str, context: NodeExecutionContext) -> set[str]:
        incoming: dict[str, list[str]] = {node.id: [] for node in context.graph.nodes}
        for edge in context.graph.edges:
            incoming[edge.target].append(edge.source)
        ancestors: set[str] = set()
        pending = list(incoming[node_id])
        while pending:
            parent = pending.pop()
            if parent not in ancestors:
                ancestors.add(parent)
                pending.extend(incoming[parent])
        return ancestors
