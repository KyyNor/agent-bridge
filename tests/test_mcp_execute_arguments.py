"""execute 兼容必须穿过 FastMCP 参数解析入口，不能只测归一化函数。"""

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_bridge.capability_hub.execute_arguments import validate_execute_params
from agent_bridge.capability_hub.gateway.metamcp import create_mcp_server
from agent_bridge.core.domain import ValidationError

SCHEMA = {
    "type": "object", "required": ["code"], "additionalProperties": False,
    "properties": {
        "code": {"type": "string"},
        "options": {"type": "object", "required": ["mode"], "properties": {
            "mode": {"enum": ["read", "write"]}, "limit": {"type": "integer", "minimum": 1},
        }, "additionalProperties": False},
        "service": {"type": "string"},
    },
}


class Capabilities:
    def __init__(self):
        self.calls = []

    def validate_execution_params(self, *, params, **kwargs):
        validate_execute_params(params, SCHEMA)

    async def execute_mcp(self, **kwargs):
        self.validate_execution_params(**kwargs)
        return await self.execute(**kwargs)

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "result": kwargs["params"]}


@pytest.fixture
def endpoint():
    class Service:
        capabilities = Capabilities()
    service = Service()
    return create_mcp_server(service), service.capabilities


@pytest.mark.parametrize("arguments", [
    {"params": {"code": "print(1)"}},
    {"code": "print(1)"},
    {"params": {}, "code": "print(1)"},
    {"params": {"code": "print(1)"}, "code": "print(1)"},
])
def test_real_fastmcp_entry_preserves_flat_and_standard_params(endpoint, arguments):
    mcp, caps = endpoint
    _, result = asyncio.run(mcp.call_tool("execute", {"service": "python", "tool_name": "run", **arguments}))
    assert result["result"] == {"code": "print(1)"}
    assert len(caps.calls) == 1


def test_merges_nested_values_without_changing_them(endpoint):
    mcp, caps = endpoint
    options = {"mode": "read", "limit": 4}
    asyncio.run(mcp.call_tool("execute", {"service": "python", "tool_name": "run",
        "params": {"code": "print(1)", "service": "business-service"}, "options": options}))
    assert caps.calls[0]["params"] == {"code": "print(1)", "service": "business-service", "options": options}


@pytest.mark.parametrize("payload,match", [
    ({"params": {"code": "secret-a"}, "code": "secret-b"}, "冲突"),
    ({"params": {"options": {"limit": True}}, "options": {"limit": 1}}, "冲突"),
    ({"params": None}, "JSON 对象"),
    ({"params": '{"code":"secret"}'}, "JSON 对象"),
    ({"path": {"code": "secret"}}, "包装字段"),
    ({"code": 1}, "type"),
    ({"code": "secret", "options": {"mode": "invalid"}}, "enum"),
    ({"code": "secret", "options": {"mode": "read", "limit": "1"}}, "type"),
    ({"code": "secret", "unexpected": 1}, "additionalProperties"),
    ({}, "缺少字段"),
])
def test_invalid_calls_never_execute_or_echo_values(endpoint, payload, match):
    mcp, caps = endpoint
    with pytest.raises((ValidationError, ToolError), match=match) as exc:
        asyncio.run(mcp.call_tool("execute", {"service": "python", "tool_name": "run", **payload}))
    assert caps.calls == []
    assert "secret" not in str(exc.value)


def test_valid_object_parameter_is_not_unwrapped():
    schema = {"type": "object", "required": ["path"], "properties": {"path": {"type": "object"}}}
    validate_execute_params({"path": {"code": "legal business object"}}, schema)


def test_local_refs_work_and_remote_refs_do_not_fetch():
    schema = {"type": "object", "properties": {"code": {"$ref": "#/definitions/code"}},
              "definitions": {"code": {"type": "string"}}}
    validate_execute_params({"code": "ok"}, schema)
    with pytest.raises(ValidationError, match="引用"):
        validate_execute_params({"code": "ok"}, {"$ref": "https://untrusted.test/schema"})


def test_tools_list_still_advertises_only_canonical_fields(endpoint):
    mcp, _ = endpoint
    execute = next(tool for tool in asyncio.run(mcp.list_tools()) if tool.name == "execute")
    assert set(execute.inputSchema["properties"]) == {"service", "tool_name", "params"}


def test_protocol_handler_returns_tool_error_without_execution(endpoint):
    from mcp.types import CallToolRequest, CallToolRequestParams

    mcp, caps = endpoint
    handler = mcp._mcp_server.request_handlers[CallToolRequest]
    request = CallToolRequest(params=CallToolRequestParams(
        name="execute", arguments={"service": "python", "tool_name": "run", "params": None},
    ))
    result = asyncio.run(handler(request))
    assert result.root.isError is True
    assert "JSON 对象" in result.root.content[0].text
    assert caps.calls == []


def test_production_capability_service_uses_visible_schema(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    service = AgentBridgeService.create(wm_paths, {"root"})
    service.store.init_schema()
    service.store.create_mcp_service(service_key="python", name="Python", endpoint_url="https://unused.test/mcp",
                                     headers={}, description="", tags=[], created_by="root")
    service.store.update_mcp_service_status("python", "enabled")
    service.store.upsert_mcp_tool(service_key="python", tool_name="run", description="测试",
                                  input_schema=SCHEMA, display_name="Run", tool_type="search", tags=[], examples=[])
    service.capabilities.validate_execution_params("root", "python", "run", {"code": "ok"})
    with pytest.raises(ValidationError, match="type"):
        service.capabilities.validate_execution_params("root", "python", "run", {"code": 123})
    with pytest.raises(ValidationError, match="log_id"):
        asyncio.run(service.capabilities.execute_mcp(actor="root", service="python", tool_name="run", params={"code": 123}))
    logs = service.governance.list_logs(actor="root", entrypoint="metamcp_execute")
    assert len(logs) == 1
    assert logs[0]["status"] != "success"


def test_invalid_schema_fails_closed():
    with pytest.raises(ValidationError, match="Schema 无效"):
        validate_execute_params({"code": "private-value"}, {"type": "not-a-type"})
