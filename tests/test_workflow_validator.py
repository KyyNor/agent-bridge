from __future__ import annotations

import pytest

from agent_bridge.app.service import AgentBridgeService


def _workflow(definition, **overrides):
    return {
        "workflow_key": "validator-test",
        "name": "Validator Test",
        "description": "",
        "profile_key": "default",
        "status": "active",
        "workflow_type": "operation",
        "definition": definition,
        **overrides,
    }


def _agent(node_id, *, prompt="", result_mode="text", output_schema=None):
    return {
        "id": node_id,
        "type": "agent",
        "name": node_id,
        "position": {"x": 0, "y": 0},
        "config": {
            "prompt": prompt,
            "backend_key": "codex",
            "result_mode": result_mode,
            "output_schema": output_schema,
        },
    }


def test_validator_returns_stable_code_for_invalid_ancestor_reference(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    result = service.workflows.validator.validate(
        actor="root",
        workflow={
            "workflow_key": "bad-ref",
            "name": "Bad Ref",
            "description": "",
            "profile_key": "default",
            "status": "active",
            "workflow_type": "operation",
            "definition": {
                "nodes": [
                    {
                        "id": "a",
                        "type": "agent",
                        "name": "A",
                        "position": {"x": 0, "y": 0},
                        "config": {"prompt": "", "backend_key": "codex"},
                    },
                    {
                        "id": "b",
                        "type": "agent",
                        "name": "B",
                        "position": {"x": 1, "y": 0},
                        "config": {"prompt": "{{ nodes.c.output.text }}", "backend_key": "codex"},
                    },
                ],
                "edges": [{"id": "a-b", "source": "a", "target": "b", "condition": None}],
            },
        },
    )

    assert result.valid is False
    assert result.errors[0].code == "invalid_reference"
    assert result.errors[0].scope == "node"
    assert result.errors[0].id == "b"


def test_validator_requires_complete_workflow_metadata(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    result = service.workflows.validator.validate(
        actor="root",
        workflow={"definition": {"nodes": [], "edges": []}},
    )

    assert result.valid is False
    assert {
        (issue.field, issue.code, issue.message)
        for issue in result.errors
    } >= {
        ("workflow_key", "missing_field", "缺少必填字段"),
        ("name", "missing_field", "缺少必填字段"),
        ("description", "missing_field", "缺少必填字段"),
        ("profile_key", "missing_field", "缺少必填字段"),
        ("status", "missing_field", "缺少必填字段"),
        ("workflow_type", "missing_field", "缺少必填字段"),
    }


@pytest.mark.parametrize(
    ("node_type", "config", "expected_field"),
    [
        ("get_task", {"unexpected": True}, "unexpected"),
        ("agent", {"prompt": 3, "backend_key": "claude"}, "prompt"),
        (
            "agent",
            {
                "prompt": "return json",
                "backend_key": "claude",
                "result_mode": "json",
                "output_schema": [],
            },
            "output_schema",
        ),
        ("script", {"script_key": "x", "params": []}, "params"),
        (
            "output",
            {
                "format": "markdown",
                "title": "Report",
                "path": "report.md",
                "prompt": 3,
                "backend_key": "claude",
            },
            "prompt",
        ),
    ],
)
def test_validator_parse_errors_locate_node_config_fields_without_union_segments(
    wm_paths,
    node_type,
    config,
    expected_field,
):
    service = AgentBridgeService.create(wm_paths, {"root"})

    result = service.workflows.validator.validate(
        actor="root",
        workflow=_workflow(
            {
                "nodes": [
                    {
                        "id": f"broken-{node_type}",
                        "type": node_type,
                        "name": "Broken",
                        "position": {"x": 0, "y": 0},
                        "config": config,
                    }
                ],
                "edges": [],
            }
        ),
    )

    assert [(issue.scope, issue.id, issue.field) for issue in result.errors] == [
        ("node", f"broken-{node_type}", expected_field)
    ]


def test_validator_rejects_mcp_for_backend_without_runtime_support(wm_paths):
    from agent_bridge.agent_runtime.adapters import CodexCodingAgent
    from agent_bridge.agent_runtime.registry import CodingAgentRegistry

    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.upsert_project_profile(profile_key="default", name="Default", created_by="root")
    service.agents.coding_agents = CodingAgentRegistry(
        default_backend="codex",
        agents=[CodexCodingAgent()],
    )

    result = service.workflows.validator.validate(
        actor="root",
        workflow=_workflow(
            {
                "nodes": [
                    {
                        **_agent("needs-mcp"),
                        "config": {
                            **_agent("needs-mcp")["config"],
                            "backend_key": "codex",
                            "mcp_enabled": True,
                        },
                    }
                ],
                "edges": [],
            }
        ),
    )

    assert any(
        issue.id == "needs-mcp"
        and issue.field == "config.mcp_enabled"
        and issue.code == "unsupported_mcp"
        for issue in result.errors
    )


@pytest.mark.parametrize("backend_key", ["claude", "opencode"])
def test_validator_accepts_mcp_for_backend_with_runtime_support(wm_paths, backend_key):
    from agent_bridge.agent_runtime.adapters import OpenCodeCodingAgent

    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.upsert_project_profile(profile_key="default", name="Default", created_by="root")
    if backend_key == "opencode":
        service.agents.coding_agents.register(OpenCodeCodingAgent())

    result = service.workflows.validator.validate(
        actor="root",
        workflow=_workflow(
            {
                "nodes": [
                    {
                        **_agent("uses-mcp"),
                        "config": {
                            **_agent("uses-mcp")["config"],
                            "backend_key": backend_key,
                            "mcp_enabled": True,
                        },
                    }
                ],
                "edges": [],
            }
        ),
    )

    assert not any(issue.code == "unsupported_mcp" for issue in result.errors)


def test_validator_resolves_default_builtin_script_before_materialization(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.upsert_project_profile(profile_key="default", name="Default", created_by="root")
    nested_workflow = _workflow({"nodes": [], "edges": []}, workflow_key="nested")

    result = service.workflows.validator.validate(
        actor="root",
        workflow=_workflow(
            {
                "nodes": [
                    {
                        "id": "validate",
                        "type": "script",
                        "name": "Validate",
                        "position": {"x": 0, "y": 0},
                        "config": {
                            "script_key": "system.validate_workflow",
                            "params": {"workflow": nested_workflow},
                        },
                    }
                ],
                "edges": [],
            }
        ),
    )

    assert result.valid is True
    assert result.errors == []
    assert service.store.scripts.get_script("system.validate_workflow") is None


def test_validator_checks_literal_and_nested_script_param_types_but_allows_references(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="typed.params",
        name="Typed Params",
        description="",
        language="python",
        code="def main(envelope):\n    return {}\n",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "count": {"type": "integer"},
                "options": {
                    "type": "object",
                    "properties": {"enabled": {"type": "boolean"}},
                    "required": ["enabled"],
                },
                "from_ref": {"type": "integer"},
            },
            "required": ["count", "options", "from_ref"],
        },
        status="active",
        owner_type="system",
        owner_key="",
    )

    result = service.workflows.validator.validate(
        actor="root",
        workflow=_workflow(
            {
                "nodes": [
                    {
                        "id": "typed",
                        "type": "script",
                        "name": "Typed",
                        "position": {"x": 0, "y": 0},
                        "config": {
                            "script_key": "typed.params",
                            "params": {
                                "count": "3",
                                "options": {"enabled": "yes"},
                                "from_ref": "{{ input.count }}",
                            },
                        },
                    }
                ],
                "edges": [],
            }
        ),
    )

    type_issues = [issue for issue in result.errors if issue.code == "invalid_script_param_type"]
    assert [(issue.field, issue.message) for issue in type_issues] == [
        ("config.params.count", "脚本参数类型不匹配，期望 integer"),
        ("config.params.options.enabled", "脚本参数类型不匹配，期望 boolean"),
    ]
    assert not any(issue.field == "config.params.from_ref" for issue in result.errors)


def test_validator_still_reports_missing_required_script_param(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    service.scripts.upsert_script(
        actor="root",
        script_key="required.params",
        name="Required Params",
        description="",
        language="python",
        code="def main(envelope):\n    return {}\n",
        input_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
        status="active",
        owner_type="system",
        owner_key="",
    )

    result = service.workflows.validator.validate(
        actor="root",
        workflow=_workflow(
            {
                "nodes": [
                    {
                        "id": "required",
                        "type": "script",
                        "name": "Required",
                        "position": {"x": 0, "y": 0},
                        "config": {"script_key": "required.params", "params": {}},
                    }
                ],
                "edges": [],
            }
        ),
    )

    assert any(
        issue.field == "config.params.count"
        and issue.code == "missing_script_param"
        and issue.message == "缺少脚本必填参数"
        for issue in result.errors
    )


def test_validator_rejects_invalid_agent_output_schema(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})

    result = service.workflows.validator.validate(
        actor="root",
        workflow=_workflow(
            {
                "nodes": [
                    _agent(
                        "invalid-schema",
                        result_mode="json",
                        output_schema={"type": "not-a-json-schema-type"},
                    )
                ],
                "edges": [],
            }
        ),
    )

    assert any(
        issue.field == "config.output_schema"
        and issue.code == "invalid_output_schema"
        and issue.message == "JSON 输出 Schema 不合法"
        for issue in result.errors
    )


def test_validator_limits_template_namespaces_ancestors_and_known_output_fields(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    output_schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "details": {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
            },
        },
    }
    graph = {
        "nodes": [
            _agent("source", result_mode="json", output_schema=output_schema),
            _agent("sibling"),
            _agent(
                "target",
                prompt=(
                    "{{ input.topic }} {{ task.payload.dynamic.deep }} "
                    "{{ task.unknown }} "
                    "{{ secrets.token }} {{ nodes.source.output.missing }} "
                    "{{ nodes.sibling.output.text }}"
                ),
            ),
        ],
        "edges": [{"id": "source-target", "source": "source", "target": "target"}],
    }

    result = service.workflows.validator.validate(actor="root", workflow=_workflow(graph))

    assert any(
        issue.code == "invalid_reference_namespace"
        and issue.message == "引用命名空间只允许 input、task、nodes"
        for issue in result.errors
    )
    assert any(
        issue.code == "invalid_reference_path"
        and issue.message == "引用字段不存在: nodes.source.output.missing"
        for issue in result.errors
    )
    assert any(
        issue.code == "invalid_reference_path"
        and issue.message == "引用字段不存在: task.unknown"
        for issue in result.errors
    )
    assert any(
        issue.code == "invalid_reference"
        and issue.message == "节点引用必须来自祖先节点: sibling"
        for issue in result.errors
    )
    assert not any("task.payload.dynamic.deep" in issue.message for issue in result.errors)


def test_validator_rejects_condition_non_node_namespace_and_unknown_output_field(wm_paths):
    service = AgentBridgeService.create(wm_paths, {"root"})
    source = _agent(
        "source",
        result_mode="json",
        output_schema={"type": "object", "properties": {"category": {"type": "string"}}},
    )
    graph = {
        "nodes": [
            source,
            _agent("input-target"),
            _agent("field-target"),
            _agent("middle"),
            _agent("ancestor-target"),
        ],
        "edges": [
            {
                "id": "bad-namespace",
                "source": "source",
                "target": "input-target",
                "condition": {"field": "input.category", "operator": "equals", "value": "bug"},
            },
            {
                "id": "bad-field",
                "source": "source",
                "target": "field-target",
                "condition": {"field": "nodes.source.output.missing", "operator": "exists"},
            },
            {"id": "source-middle", "source": "source", "target": "middle"},
            {
                "id": "valid-ancestor",
                "source": "middle",
                "target": "ancestor-target",
                "condition": {"field": "nodes.source.output.category", "operator": "equals", "value": "bug"},
            },
        ],
    }

    result = service.workflows.validator.validate(actor="root", workflow=_workflow(graph))

    assert any(
        issue.id == "bad-namespace"
        and issue.field == "condition.field"
        and issue.code == "invalid_reference_namespace"
        and issue.message == "条件字段只能引用来源节点或其祖先节点输出"
        for issue in result.errors
    )
    assert not any(issue.id == "valid-ancestor" for issue in result.errors)
    assert any(
        issue.id == "bad-field"
        and issue.field == "condition.field"
        and issue.code == "invalid_reference_path"
        and issue.message == "引用字段不存在: nodes.source.output.missing"
        for issue in result.errors
    )
