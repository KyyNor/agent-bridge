"""HTTP execution client for saved OpenAPI tools."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote, urljoin

import httpx

logger = logging.getLogger(__name__)


class OpenApiHttpClient:
    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def call_tool(self, service: dict[str, Any], tool: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        service_key = str(service.get("service_key") or "")
        method = str(tool.get("method") or "GET").upper()
        mapping = tool.get("request_mapping") if isinstance(tool.get("request_mapping"), dict) else {}
        url = _build_url(str(service.get("base_url") or ""), str(tool.get("path") or ""), mapping.get("path"), params)
        query = _mapped_values(mapping.get("query"), params)
        headers = _headers(service, mapping.get("headers"), params)
        body_arg = mapping.get("body")
        json_body = params.get(body_arg) if isinstance(body_arg, str) and body_arg else None
        logger.debug("OpenAPI 调用开始 service=%s %s %s", service_key, method, url)
        try:
            response = httpx.request(method, url, params=query, headers=headers, json=json_body, timeout=self.timeout)
        except httpx.TimeoutException:
            logger.warning("OpenAPI 调用超时 service=%s %s %s timeout=%ss", service_key, method, url, self.timeout)
            raise
        except Exception as exc:
            logger.error(
                "OpenAPI 调用失败 service=%s %s %s 原因=%s",
                service_key,
                method,
                url,
                exc,
                exc_info=True,
            )
            raise
        logger.debug(
            "OpenAPI 调用完成 service=%s %s %s status=%d",
            service_key,
            method,
            url,
            response.status_code,
        )
        return {
            "status_code": response.status_code,
            "headers": {"content-type": response.headers.get("content-type", "")},
            "body": _response_body(response),
        }


def _build_url(base_url: str, path: str, path_mapping: Any, params: dict[str, Any]) -> str:
    rendered_path = path
    for token, arg_name in (path_mapping or {}).items():
        value = params.get(str(arg_name), "")
        rendered_path = rendered_path.replace("{" + str(token) + "}", quote(str(value), safe=""))
    return urljoin(base_url.rstrip("/") + "/", rendered_path.lstrip("/"))


def _mapped_values(mapping: Any, params: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    if not isinstance(mapping, dict):
        return values
    for name, arg_name in mapping.items():
        value = params.get(str(arg_name))
        if value is None:
            continue
        if isinstance(value, bool):
            values[str(name)] = "true" if value else "false"
        else:
            values[str(name)] = str(value)
    return values


def _headers(service: dict[str, Any], header_mapping: Any, params: dict[str, Any]) -> dict[str, str]:
    headers = {str(key): str(value) for key, value in (service.get("headers") or {}).items()}
    headers.update(_mapped_values(header_mapping, params))
    auth_config = service.get("auth_config") or {}
    if isinstance(auth_config, dict):
        auth_type = str(auth_config.get("type") or "").lower()
        if auth_type == "bearer" and auth_config.get("token"):
            headers["Authorization"] = f"Bearer {auth_config['token']}"
        elif auth_type == "api_key" and auth_config.get("header") and auth_config.get("value"):
            headers[str(auth_config["header"])] = str(auth_config["value"])
    return headers


def _response_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if response.content and re.search(r"(^|[+;/])json([;]|$)", content_type):
        return response.json()
    return response.text
