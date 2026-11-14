"""管理员后端配置与原生插件的受限 Hindsight HTTP 网关。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from agent_bridge.core.domain import AccessDenied, ValidationError, require_admin_user
from agent_bridge.knowledge_management.memory.native import allowed_gateway_path

logger = logging.getLogger(__name__)


class MemoryBackendConfigRequest(BaseModel):
    backend: str
    url: str = ""
    api_token: str | None = None
    clear_api_token: bool = False
    expected_edit_token: str | None = None


def create_memory_backend_routes(service, actor):
    router = APIRouter()

    @router.get("/memory/backend-config")
    def config(current_actor: str = Depends(actor)):
        require_admin_user(current_actor, service.admins)
        return service.memory.backend_config.public()

    @router.put("/memory/backend-config")
    def save(payload: MemoryBackendConfigRequest, current_actor: str = Depends(actor)):
        require_admin_user(current_actor, service.admins)
        return service.memory.backend_config.save(actor=current_actor, **payload.model_dump())

    @router.post("/memory/backend-config/test")
    def connection_test(current_actor: str = Depends(actor)):
        require_admin_user(current_actor, service.admins)
        service.memory.backends.require("hindsight").request("GET", "/v1/version")
        return {"status": "ok"}

    @router.api_route("/memory/hindsight/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def gateway(path: str, request: Request):
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AccessDenied("Hindsight 网关需要工作台 capability")
        capability = service.require_workspace_capability(token)
        grant = service.memory.native.require_grant(capability)
        upstream_path = "/" + path
        raw_path = request.scope.get("raw_path", b"").lower()
        if (any(part in raw_path for part in (b"%2f", b"%5c", b"%25", b"%2e")) or
                not allowed_gateway_path(request.method, upstream_path, grant.bank_id)):
            raise AccessDenied("Hindsight 网关拒绝未授权的 bank 或操作")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2 * 1024 * 1024:
                return JSONResponse({"detail": "记忆请求超过 2 MiB 限制"}, status_code=413)
        import json
        try:
            payload = json.loads(body) if body else None
        except ValueError as exc:
            raise ValidationError("记忆请求必须为 JSON") from exc
        # 每个请求都校验 capability 与现行绑定。仅服务器持有上游管理凭据。
        import httpx
        grant = service.memory.native.require_grant(service.require_workspace_capability(token))
        settings = grant.settings
        headers = {"Authorization": "Bearer " + settings["api_token"]} if settings["api_token"] else {}
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                response = await client.request(request.method, settings["url"] + upstream_path,
                                                params=request.query_params, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("Hindsight 网关连接失败 block=%s 类型=%s", grant.block_key, type(exc).__name__)
            return JSONResponse({"detail": "Hindsight 服务暂时不可用"}, status_code=502)
        if response.status_code >= 400 or response.is_redirect:
            logger.warning("Hindsight 网关上游失败 block=%s 状态码=%s", grant.block_key, response.status_code)
            status = response.status_code if response.status_code in {404, 429} else 502
            return JSONResponse({"detail": "Hindsight 上游请求失败"}, status_code=status)
        # bank config 可能包含上游模型凭据。插件只需要 append 能力标记。
        if upstream_path.endswith("/config"):
            config = response.json().get("config", {})
            return {"config": {"store_document_text": config.get("store_document_text", True)}}
        if upstream_path in {"/version", "/v1/version"}:
            return {"api_version": response.json().get("api_version", "")}
        return Response(response.content, status_code=response.status_code, media_type="application/json")

    return router
