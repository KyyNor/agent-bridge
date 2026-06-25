"""Parse OpenAPI specs into editable Agent Bridge tool candidates."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import textwrap
from typing import Any

import yaml

from agent_bridge.capability_hub.models import ToolType
from agent_bridge.core.domain import ValidationError

logger = logging.getLogger(__name__)


HTTP_METHODS = {"get", "head", "post", "put", "patch", "delete", "options", "trace"}


def parse_openapi_operations(spec: dict[str, Any] | str) -> list[dict[str, Any]]:
    """把 OpenAPI 文档解析成可编辑的 Agent Bridge 工具候选列表。

    每个 operation 推断出一个默认 tool_type（仅 GET/HEAD 能被自动分类，
    其余方法保持 unconfigured 等待管理员手动配置）。返回的候选随后由
    ``CapabilityService.import_openapi_operations`` 持久化。
    """
    document = _load_spec(spec)
    paths = document.get("paths")
    if not isinstance(paths, dict):
        logger.warning("OpenAPI 解析失败：缺少 paths 字段")
        raise ValidationError("OpenAPI spec must contain paths")

    candidates: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_parameters = _parameters(path_item.get("parameters"))
        for method, operation in path_item.items():
            method_l = str(method).lower()
            if method_l not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "")
            base_name = _tool_name(operation_id) if operation_id else _fallback_tool_name(method_l, str(path))
            tool_name = _dedupe_name(base_name, used_names, method_l, str(path))
            parameters = [*path_parameters, *_parameters(operation.get("parameters"))]
            input_schema, request_mapping = _input_schema_and_mapping(parameters, operation)
            response_schema = _response_schema(operation)
            candidates.append(
                {
                    "tool_name": tool_name,
                    "operation_id": operation_id,
                    "method": method_l.upper(),
                    "path": str(path),
                    "display_name": str(operation.get("summary") or operation_id or tool_name).strip(),
                    "description": str(operation.get("description") or operation.get("summary") or "").strip(),
                    "input_schema": input_schema,
                    "request_mapping": request_mapping,
                    "response_schema": response_schema,
                    "tool_type": _default_tool_type(method_l, str(path)),
                    "tags": [str(tag) for tag in operation.get("tags", []) if str(tag).strip()],
                    "examples": [],
                }
            )
    logger.info("OpenAPI 解析完成 操作数=%d", len(candidates))
    return candidates


def _load_spec(spec: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(spec, dict):
        return spec
    text = textwrap.dedent(str(spec or "")).strip()
    if not text:
        raise ValidationError("OpenAPI spec content is required")
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise ValidationError(f"invalid OpenAPI spec: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("OpenAPI spec must be an object")
    return parsed


def _parameters(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and "$ref" not in item]


def _input_schema_and_mapping(parameters: list[dict[str, Any]], operation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    mapping: dict[str, Any] = {"path": {}, "query": {}, "headers": {}, "body": None}

    for parameter in parameters:
        name = str(parameter.get("name") or "").strip()
        location = str(parameter.get("in") or "").strip()
        if not name or location not in {"path", "query", "header"}:
            continue
        arg_name = _argument_name(name)
        schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {"type": "string"}
        properties[arg_name] = {**schema, "description": str(parameter.get("description") or "")}
        if parameter.get("required") or location == "path":
            required.append(arg_name)
        map_key = "headers" if location == "header" else location
        mapping[map_key][name] = arg_name

    body_schema = _request_body_schema(operation)
    if body_schema:
        properties["body"] = body_schema
        mapping["body"] = "body"
        if _request_body_required(operation):
            required.append("body")

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = sorted(set(required))
    return schema, mapping


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return {}
    content = request_body.get("content")
    if not isinstance(content, dict):
        return {}
    for mime_type in ("application/json", "application/*+json", "*/*"):
        media = content.get(mime_type)
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            return media["schema"]
    for media in content.values():
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            return media["schema"]
    return {}


def _request_body_required(operation: dict[str, Any]) -> bool:
    request_body = operation.get("requestBody")
    return isinstance(request_body, dict) and bool(request_body.get("required"))


def _response_schema(operation: dict[str, Any]) -> dict[str, Any]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return {}
    for status in ("200", "201", "202", "204", "default"):
        response = responses.get(status)
        schema = _schema_from_response(response)
        if schema:
            return schema
    for response in responses.values():
        schema = _schema_from_response(response)
        if schema:
            return schema
    return {}


def _schema_from_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    content = response.get("content")
    if not isinstance(content, dict):
        return {}
    for media in content.values():
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            return media["schema"]
    return {}


def _default_tool_type(method: str, path: str) -> str:
    """推断 operation 的默认 tool_type。

    关键决策：只有 GET/HEAD 能被自动分类——若路径最后一段是 ``{var}``
    形参则判为 detail（如 GET /users/{id}），否则判为 search（如 GET /users）。
    其余写操作（POST/PUT/...）一律保持 ``unconfigured``，等管理员在 UI
    里手动配置，避免误把副作用操作当成只读工具暴露给 agent。
    """
    if method not in {"get", "head"}:
        return ToolType.unconfigured.value
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if segments and re.fullmatch(r"\{[^{}]+\}", segments[-1]):
        return ToolType.detail.value
    return ToolType.search.value


def _tool_name(value: str) -> str:
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower()
    return name or "operation"


def _fallback_tool_name(method: str, path: str) -> str:
    segments = [method, *[segment.strip("{}") for segment in path.strip("/").split("/") if segment]]
    return _tool_name("_".join(segments))


def _dedupe_name(base_name: str, used_names: set[str], method: str, path: str) -> str:
    if base_name not in used_names:
        used_names.add(base_name)
        return base_name
    suffix = hashlib.sha1(f"{method}:{path}".encode("utf-8")).hexdigest()[:8]
    candidate = f"{base_name}_{suffix}"
    index = 2
    while candidate in used_names:
        candidate = f"{base_name}_{suffix}_{index}"
        index += 1
    used_names.add(candidate)
    return candidate


def _argument_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return name or "param"
