"""仅通过个人 Key 开放当前知识库的文档列表、上传和移除。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from agent_bridge.access_control.personal_api_keys import PersonalApiKeys

logger = logging.getLogger(__name__)


class DocumentItem(BaseModel):
    slug: str
    title: str
    status: str
    sync_status: str | None = None
    current_version_no: int | None = None


class DocumentPage(BaseModel):
    items: list[DocumentItem]
    total: int
    limit: int
    offset: int


class UploadResult(BaseModel):
    accepted: bool
    documents: list[DocumentItem]
    skipped_count: int = 0


class RemoveResult(BaseModel):
    slug: str
    status: str


class KeyStatus(BaseModel):
    has_key: bool
    masked_key: str
    updated_at: str | None


class KeyCreated(KeyStatus):
    api_key: str


def create_knowledge_public_routes(service, actor, save_upload, upload_filename):
    router = APIRouter()
    public = APIRouter(prefix="/knowledge-api", tags=["知识库文档 API"])
    keys = PersonalApiKeys(service.store.access_control)
    bearer = HTTPBearer(auto_error=False, scheme_name="PersonalApiKey", description="个人 API Key；在知识库页面生成或重置。")

    def api_actor(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        # 只接受Key；Cookie与可信CLI Header都不能代替此入口的Bearer凭据。
        return keys.authenticate(credentials.credentials if credentials else "")

    @router.get("/personal-api-key", response_model=KeyStatus)
    def key_status(current_actor: str = Depends(actor)):
        return keys.status(current_actor)

    @router.post("/personal-api-key/reset", response_model=KeyCreated)
    def key_reset(current_actor: str = Depends(actor)):
        return keys.reset(current_actor)

    @public.get("/kbs/{kb_slug}/documents", response_model=DocumentPage, summary="列出文档及处理状态")
    def documents(kb_slug: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), current_actor: str = Depends(api_actor)):
        rows = service.list_docs(current_actor, kb_slug)
        return {"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset}

    @public.post("/kbs/{kb_slug}/documents", response_model=UploadResult, status_code=202, summary="新增文档",
                 description="multipart文件上传，单文件最多32 MiB。接收成功不等于可检索，后续处理遵循知识库同步策略；通过列表查看状态。支持现有上传格式，ZIP可展开多个文档。")
    def upload(kb_slug: str, file: UploadFile = File(), current_actor: str = Depends(api_actor)):
        # 写权限在保存临时文件前校验，后续入库仍由既有服务再次校验。
        service._require_kb_admin_visible(current_actor, kb_slug)
        path = save_upload(file, max_bytes=32 * 1024 * 1024)
        try:
            result = service.add_document(current_actor, path, [kb_slug], True, original_filename=upload_filename(file))
        finally:
            path.unlink(missing_ok=True)
        docs = result.get("documents", [result])
        logger.info("知识库 API 上传 user=%s kb=%s 文档数=%s 状态=已接收", current_actor, kb_slug, len(docs))
        return {"accepted": True, "documents": docs, "skipped_count": result.get("skipped_count", 0)}

    @public.delete("/kbs/{kb_slug}/documents/{doc_slug}", response_model=RemoveResult, summary="从当前知识库删除文档",
                   description="只移除当前知识库的文档关联，保留其他知识库关联；沿用平台文档回收规则。")
    def remove(kb_slug: str, doc_slug: str, current_actor: str = Depends(api_actor)):
        result = service.remove_document_from_kb(current_actor, kb_slug, doc_slug)
        logger.info("知识库 API 删除 user=%s kb=%s doc=%s 状态=成功", current_actor, kb_slug, doc_slug)
        return result

    @router.get("/kbs/{kb_slug}/openapi")
    def specification(kb_slug: str, current_actor: str = Depends(actor)):
        service._require_kb_visible(current_actor, kb_slug)
        schema = get_openapi(title="知识库文档 API", version="1.0.0", routes=public.routes,
                             description="使用个人API Key，权限与当前用户一致。只提供列出、新增和删除文档。")
        schema["servers"] = [{"url": "/api/v1"}]
        for path in schema["paths"].values():
            for operation in path.values():
                for parameter in operation.get("parameters", []):
                    if parameter["name"] == "kb_slug":
                        parameter["schema"]["default"] = kb_slug
                operation.setdefault("responses", {}).update({str(code): {"description": text} for code, text in
                    [(400, "输入无效"), (401, "API Key无效或已重置"), (403, "没有知识库操作权限"), (404, "资源不存在"), (413, "上传超过32 MiB")]})
        return schema

    router.include_router(public)
    return router
