"""单节点设计协议；模型只填写内容，平台保留执行配置并检查引用。"""
from __future__ import annotations

import json
from jsonschema import Draft7Validator
from pydantic import ValidationError

from agent_bridge.agent_runtime.json_schema import normalize_draft7_schema
from agent_bridge.automation.workflows.definition import AgentNode, WorkflowGraph
from agent_bridge.automation.workflows.references import REFERENCE_RE

NODE_DESIGN_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "additionalProperties": False,
    "required": ["summary", "notes", "name", "prompt", "result_mode", "output_schema_json"],
    "properties": {
        "summary": {"type": "string"}, "notes": {"type": "array", "items": {"type": "string"}},
        "name": {"type": "string"}, "prompt": {"type": "string"},
        "result_mode": {"type": "string", "enum": ["text", "json"]}, "output_schema_json": {"type": "string"},
    },
}


def prepare_node_design(current):
    try:
        return AgentNode.model_validate(current.get("node"))
    except ValidationError as exc:
        from agent_bridge.core.domain import ValidationError as DomainValidationError
        raise DomainValidationError("请先添加或选择一个 Agent 节点") from exc


def finish_node_design(service, actor, current, node, result):
    Draft7Validator(NODE_DESIGN_SCHEMA).validate(result)
    if not result["prompt"].strip() or not result["name"].strip():
        raise ValueError("节点名称和提示词不能为空")
    schema = None
    if result["result_mode"] == "json":
        schema = json.loads(result["output_schema_json"])
        if not isinstance(schema, dict):
            raise ValueError("输出 Schema 必须为 JSON 对象")
        schema = normalize_draft7_schema(schema)
        Draft7Validator.check_schema(schema)
        if schema.get("type") != "object":
            raise ValueError("节点结构化输出必须为 object Schema")
    service.agents.coding_agents.get(node.config.backend_key)
    for key in node.config.skill_names:
        service.skills.get_skill(actor, key)
    allowed = {item["path"] for item in current.get("references", []) if isinstance(item, dict) and isinstance(item.get("path"), str)}
    graph = WorkflowGraph.model_validate(current.get("graph", {"nodes": [], "edges": []}))
    incoming = {n.id: [e for e in graph.edges if e.target == n.id] for n in graph.nodes}

    def possibly_skipped(key, seen=None):
        seen = set() if seen is None else seen
        if key in seen:
            return True
        seen = seen | {key}
        return any(edge.condition is not None or possibly_skipped(edge.source, seen) for edge in incoming.get(key, []))

    for path in REFERENCE_RE.findall(result["prompt"]):
        if not any(path == key or (path.startswith(key + ".") and key in {"task.payload", "input"}) for key in allowed):
            raise ValueError(f"节点引用不在可用字段中：{path}")
        if path.startswith("nodes.") and possibly_skipped(path.split(".")[1]):
            raise ValueError(f"不能直接引用可能被跳过的节点：{path}")
    updated = node.model_dump(mode="json")
    updated["name"] = result["name"]
    updated["config"].update(prompt=result["prompt"], result_mode=result["result_mode"], output_schema=schema)
    parsed = AgentNode.model_validate(updated)
    return {"summary": result["summary"], "notes": result["notes"], "node": parsed.model_dump(mode="json")}
