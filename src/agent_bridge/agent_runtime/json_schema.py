"""Claude Agent SDK compatible JSON Schema helpers.

Claude Code structured output validates schemas as JSON Schema draft-07.  The
workflow editor historically accepted draft 2020-12 schemas, so normalize the
small compatible subset at the agent boundary instead of sending a mixed
schema dialect to the SDK.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

DRAFT7_SCHEMA_URI = "http://json-schema.org/draft-07/schema#"
_SUPPORTED_SOURCE_SCHEMA_URIS = frozenset(
    {
        DRAFT7_SCHEMA_URI,
        "https://json-schema.org/draft-07/schema#",
        "https://json-schema.org/draft/2019-09/schema",
        "https://json-schema.org/draft/2020-12/schema",
    }
)

# These keywords either have no Draft-07 equivalent or would require changing
# validation semantics.  Reject them explicitly: Draft7Validator otherwise
# treats unknown keywords as annotations and would silently weaken validation.
_UNSUPPORTED_DRAFT7_KEYWORDS = frozenset(
    {
        "$anchor",
        "$dynamicAnchor",
        "$dynamicRef",
        "$recursiveAnchor",
        "$recursiveRef",
        "$vocabulary",
        "contentSchema",
        "dependentRequired",
        "dependentSchemas",
        "maxContains",
        "minContains",
        "prefixItems",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


def normalize_draft7_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a Draft-07 schema suitable for Claude structured output.

    ``$defs`` and local ``$ref`` paths are the only newer constructs converted
    automatically.  Every other semantic Draft 2019-09/2020-12 keyword is
    rejected so output validation cannot become weaker without notice.
    """
    source_dialect = schema.get("$schema")
    if source_dialect is not None and (
        not isinstance(source_dialect, str) or source_dialect not in _SUPPORTED_SOURCE_SCHEMA_URIS
    ):
        raise SchemaError(f"不支持的 JSON Schema 草案：{source_dialect}")

    normalized = _normalize_value(schema, path="$")
    if not isinstance(normalized, dict):  # Defensive: callers declare dict.
        raise SchemaError("JSON Schema 根节点必须是对象")
    if "$schema" in schema:
        normalized["$schema"] = DRAFT7_SCHEMA_URI
    Draft7Validator.check_schema(normalized)
    return normalized


def _normalize_value(value: Any, *, path: str) -> Any:
    if isinstance(value, list):
        return [_normalize_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return value

    if "$defs" in value and "definitions" in value:
        raise SchemaError(f"{path} 不能同时声明 $defs 与 definitions")

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in _UNSUPPORTED_DRAFT7_KEYWORDS:
            raise SchemaError(f"{path}.{key} 仅受 JSON Schema Draft 2019-09/2020-12 支持")
        if key == "$schema":
            continue
        target_key = "definitions" if key == "$defs" else key
        if key == "$ref" and isinstance(item, str):
            normalized[target_key] = item.replace("#/$defs/", "#/definitions/")
            continue
        normalized[target_key] = _normalize_value(item, path=f"{path}.{key}")
    return normalized
