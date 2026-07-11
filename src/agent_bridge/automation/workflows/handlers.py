from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_bridge.automation.workflows.definition import AgentNode, GetTaskNode, ScriptNode, WorkflowGraph, WorkflowNode
from agent_bridge.automation.workflows.references import render_text, render_value


class NodeExecutionError(RuntimeError):
    def __init__(self, node_id: str, message: str) -> None:
        super().__init__(message)
        self.node_id = node_id


@dataclass
class NodeExecutionContext:
    actor: str
    workflow: dict[str, Any]
    run_id: str
    input: dict[str, Any]
    task: dict[str, Any] | None
    nodes: dict[str, dict[str, Any]]
    graph: WorkflowGraph

    def template_context(self) -> dict[str, Any]:
        return {"input": self.input, "task": self.task, "nodes": self.nodes}


@dataclass(frozen=True)
class NodeExecutionResult:
    status: Literal["completed", "warning"] = "completed"
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    agent_run_key: str | None = None
    script_run_id: str | None = None
    artifact_ids: list[str] = field(default_factory=list)


def build_agent_prompt(
    *, skill_service: Any, actor: str, skill_names: list[str], prompt: str, context: dict[str, Any]
) -> str:
    blocks: list[str] = []
    for skill_name in skill_names:
        skill = skill_service.get_skill(actor, skill_name)
        blocks.append(f"[技能：{skill_name}]\n{skill['prompt']}")
    blocks.append(f"[任务指令]\n{render_text(prompt, context)}")
    return "\n\n".join(blocks)


class WorkflowNodeHandlers:
    def __init__(
        self,
        *,
        agent_service: Any,
        scripts: Any,
        skill_service: Any,
        workflow_service: Any | None = None,
        output_handler: Any | None = None,
    ) -> None:
        self.agent_service = agent_service
        self.scripts = scripts
        self.skill_service = skill_service
        self.workflow_service = workflow_service
        self.output_handler = output_handler

    async def execute(self, node: WorkflowNode, context: NodeExecutionContext) -> NodeExecutionResult:
        if node.type == "get_task":
            return self._get_task(node, context)
        if node.type == "agent":
            return await self._agent(node, context)
        if node.type == "script":
            return await self._script(node, context)
        if node.type == "output" and self.output_handler is not None:
            return await self.output_handler.execute(node, context)
        raise NodeExecutionError(node.id, f"unsupported workflow node type: {node.type}")

    def _get_task(self, node: GetTaskNode, context: NodeExecutionContext) -> NodeExecutionResult:
        if self.workflow_service is None:
            raise NodeExecutionError(node.id, "workflow service is not configured")
        result = self.workflow_service.get_task_for_agent(
            profile_key=context.workflow["profile_key"], workflow_key=context.workflow["workflow_key"], run_id=context.run_id
        )
        task = result.get("task")
        context.task = task
        return NodeExecutionResult(output={"task": task})

    async def _agent(self, node: AgentNode, context: NodeExecutionContext) -> NodeExecutionResult:
        config = node.config
        output_schema = config.output_schema if config.result_mode == "json" else None
        result = await self.agent_service.run(
            prompt=build_agent_prompt(
                skill_service=self.skill_service,
                actor=context.actor,
                skill_names=config.skill_names,
                prompt=config.prompt,
                context=context.template_context(),
            ),
            agent_name=f"workflow_{node.id}",
            profile=context.workflow["profile_key"] if config.mcp_enabled else None,
            workflow_key=context.workflow["workflow_key"],
            run_id=context.run_id,
            output_schema=output_schema,
            backend_key=config.backend_key,
            skills=None,
            actor=context.actor,
        )
        if not result.ok:
            raise NodeExecutionError(node.id, result.error or "agent node failed")
        output = result.result if config.result_mode == "json" else {"text": str(result.result or "")}
        if not isinstance(output, dict):
            raise NodeExecutionError(node.id, "agent JSON output must be an object")
        return NodeExecutionResult(output=output, agent_run_key=result.run_key)

    async def _script(self, node: ScriptNode, context: NodeExecutionContext) -> NodeExecutionResult:
        config = node.config
        try:
            run = await asyncio.to_thread(
                self.scripts.run_script,
                actor=context.actor,
                script_key=config.script_key,
                script_params=render_value(config.params, context.template_context()),
                timeout_seconds=config.timeout_seconds,
                profile_key=context.workflow["profile_key"],
                workflow_context={"workflow": True, "workflow_key": context.workflow["workflow_key"], "run_id": context.run_id},
                run_type="mcp",
            )
        except Exception as exc:
            raise NodeExecutionError(node.id, str(exc)) from exc
        output = run.get("result") or {}
        return NodeExecutionResult(output=output, script_run_id=run.get("run_id"))
