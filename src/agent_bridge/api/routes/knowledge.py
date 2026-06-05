"""Knowledge base, document, sync, search, and ask endpoints."""
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from agent_bridge.api.schemas import (
    AskRequest,
    CreateKbRequest,
    GrantMemberRequest,
    PurgeRequest,
    SyncRequest,
)




def create_knowledge_routes(service, actor, call_safely, save_upload, upload_filename):
    router = APIRouter()

    @router.get("/backends")
    def list_backends() -> list[dict[str, str]]:
        if service.registry is None:
            return []
        result = []
        for slug in service.registry.list_slugs():
            adapter = service.registry.get(slug)
            module = type(adapter).__module__
            backend_type = "ragflow" if "ragflow" in module else "mock"
            result.append({"slug": slug, "type": backend_type, "status": "active"})
        return result

    @router.post("/admin/init")
    def init_system(current_actor: str = Depends(actor)) -> None:
        return call_safely(lambda: service.init_system())

    @router.post("/kbs")
    def create_kb(payload: CreateKbRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return call_safely(lambda: service.create_kb(current_actor, payload.slug, payload.name, payload.description))

    @router.get("/kbs")
    def list_kbs(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_kbs(current_actor))

    @router.post("/kbs/{kb_slug}/members")
    def grant_kb_member(
        kb_slug: str, payload: GrantMemberRequest, current_actor: str = Depends(actor),
    ) -> dict[str, str]:
        return call_safely(lambda: service.grant_kb_member(current_actor, kb_slug, payload.linux_user, payload.role))

    @router.get("/kbs/{kb_slug}/members")
    def list_kb_members(kb_slug: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_kb_members(current_actor, kb_slug))

    @router.post("/docs")
    def add_document(
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        kb: list[str] = Form(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        upload_path = save_upload(file)
        try:
            return call_safely(
                lambda: service.add_document(current_actor, upload_path, kb, later, original_filename=upload_filename(file))
            )
        finally:
            upload_path.unlink(missing_ok=True)

    @router.get("/docs")
    def list_docs(kb: str, backend: str | None = None, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_docs(current_actor, kb, backend=backend))

    @router.get("/docs/{doc_slug}")
    def get_doc(doc_slug: str, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return call_safely(lambda: service.get_doc(current_actor, doc_slug, backend=backend))

    @router.post("/docs/{doc_slug}/versions")
    def update_document(
        doc_slug: str,
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        upload_path = save_upload(file)
        try:
            return call_safely(
                lambda: service.update_document(current_actor, doc_slug, upload_path, later, original_filename=upload_filename(file))
            )
        finally:
            upload_path.unlink(missing_ok=True)

    @router.post("/docs/{doc_slug}/delete")
    def delete_document(doc_slug: str, current_actor: str = Depends(actor)) -> dict[str, str]:
        return call_safely(lambda: service.delete_document(current_actor, doc_slug))

    @router.post("/docs/{doc_slug}/purge")
    def purge_document(doc_slug: str, payload: PurgeRequest, current_actor: str = Depends(actor)) -> dict[str, str]:
        return call_safely(lambda: service.purge_document(current_actor, doc_slug, confirm=payload.confirm))

    @router.get("/status")
    def status(backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, list[dict[str, Any]]]:
        return call_safely(lambda: service.status(current_actor, backend=backend))

    @router.post("/sync")
    def sync(payload: SyncRequest, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, int]:
        return call_safely(lambda: service.sync(current_actor, all_users=payload.all_users, backend=backend))

    @router.get("/search")
    def search(q: str, kb: str, backend: str | None = None, top_k: int = 6, current_actor: str = Depends(actor)) -> dict[str, Any]:
        results = call_safely(lambda: service.search(current_actor, kb, q, backend_slug=backend, top_k=top_k))
        return {"results": [{"chunk_id": r.chunk_id, "content": r.content, "document_name": r.document_name, "similarity": r.similarity, "dataset_id": r.dataset_id} for r in results]}

    @router.post("/ask")
    def ask(payload: AskRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        result = call_safely(lambda: service.ask(current_actor, payload.kb, payload.question, backend_slug=payload.backend, session_id=payload.session_id))
        return {"answer": result.answer, "chunks": [{"chunk_id": c.chunk_id, "content": c.content, "document_name": c.document_name, "similarity": c.similarity, "dataset_id": c.dataset_id} for c in result.chunks], "session_id": result.session_id}

    @router.get("/builtin/wiki/kbs")
    def list_builtin_wiki_kbs(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_kb_status_summaries(current_actor))

    return router
