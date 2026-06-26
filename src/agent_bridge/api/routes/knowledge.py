"""Knowledge base, document, sync, search, and ask endpoints."""
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from agent_bridge.api.schemas import (
    AskRequest,
    CreateAgentRequest,
    CreateKbRequest,
    PurgeRequest,
    SyncRequest,
    UpdateKbDefaultsRequest,
    UpsertBackendRequest,
    UpdateBackendRequest,
)




def create_knowledge_routes(service, actor, save_upload, upload_filename):
    router = APIRouter()

    @router.get("/backends")
    def list_backends(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.list_backends(current_actor)

    @router.post("/backends")
    def add_backend(payload: UpsertBackendRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.add_backend(
            current_actor, payload.slug, payload.backend_type,
            base_url=payload.base_url, api_key=payload.api_key,
            timeout=payload.timeout, embedding_model_id=payload.embedding_model_id,
            summary_model_id=payload.summary_model_id,
        )

    @router.put("/backends/{slug}")
    def update_backend(slug: str, payload: UpdateBackendRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.update_backend(
            current_actor, slug,
            backend_type=payload.backend_type, base_url=payload.base_url,
            api_key=payload.api_key, timeout=payload.timeout,
            embedding_model_id=payload.embedding_model_id,
            summary_model_id=payload.summary_model_id,
        )

    @router.post("/backends/{slug}/delete")
    def delete_backend(slug: str, current_actor: str = Depends(actor)) -> dict[str, str]:
        return service.remove_backend(current_actor, slug)

    @router.get("/backends/{slug}/agents")
    def list_backend_agents(slug: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.list_backend_agents(current_actor, slug)

    @router.get("/backends/{slug}/agent-types")
    def list_backend_agent_types(slug: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.list_backend_agent_types(current_actor, slug)

    @router.post("/backends/{slug}/agents")
    def create_backend_agent(slug: str, payload: CreateAgentRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.create_backend_agent(current_actor, slug, payload.name, payload.preset_id)

    @router.post("/admin/init")
    def init_system(current_actor: str = Depends(actor)) -> None:
        return service.init_system()

    @router.post("/kbs")
    def create_kb(payload: CreateKbRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.create_kb(current_actor, payload.slug, payload.name, payload.description)

    @router.get("/kbs")
    def list_kbs(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.list_kbs(current_actor)

    @router.post("/kbs/{kb_slug}/delete")
    def delete_kb(kb_slug: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.delete_kb(current_actor, kb_slug)

    @router.post("/docs")
    def add_document(
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        kb: list[str] = Form(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        upload_path = save_upload(file)
        try:
            return service.add_document(current_actor, upload_path, kb, later, original_filename=upload_filename(file))
        finally:
            upload_path.unlink(missing_ok=True)

    @router.get("/docs")
    def list_docs(kb: str, backend: str | None = None, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.list_docs(current_actor, kb, backend=backend)

    @router.get("/docs/{doc_slug}")
    def get_doc(doc_slug: str, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.get_doc(current_actor, doc_slug, backend=backend)

    @router.post("/docs/{doc_slug}/versions")
    def update_document(
        doc_slug: str,
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        upload_path = save_upload(file)
        try:
            return service.update_document(current_actor, doc_slug, upload_path, later, original_filename=upload_filename(file))
        finally:
            upload_path.unlink(missing_ok=True)

    @router.post("/docs/{doc_slug}/delete")
    def delete_document(doc_slug: str, current_actor: str = Depends(actor)) -> dict[str, str]:
        return service.delete_document(current_actor, doc_slug)

    @router.post("/docs/{doc_slug}/purge")
    def purge_document(doc_slug: str, payload: PurgeRequest, current_actor: str = Depends(actor)) -> dict[str, str]:
        return service.purge_document(current_actor, doc_slug, confirm=payload.confirm)

    @router.get("/status")
    def status(backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, list[dict[str, Any]]]:
        return service.status(current_actor, backend=backend)

    @router.post("/sync")
    def sync(payload: SyncRequest, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, int]:
        return service.sync(current_actor, all_users=payload.all_users, backend=backend)

    @router.get("/search")
    def search(q: str, kb: str, backend: str | None = None, top_k: int = 6, profile_key: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        results = service.search(current_actor, kb, q, backend_slug=backend, profile_key=profile_key, top_k=top_k)
        return {"results": [{"chunk_id": r.chunk_id, "content": r.content, "document_name": r.document_name, "similarity": r.similarity, "dataset_id": r.dataset_id} for r in results]}

    @router.get("/search-all")
    def search_all(q: str, top_k: int = 6, profile_key: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return {"results": service.search_all(current_actor, q, top_k=top_k, profile_key=profile_key)}

    @router.post("/ask")
    def ask(payload: AskRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        result = service.ask(current_actor, payload.kb, payload.question, backend_slug=payload.backend, session_id=payload.session_id, profile_key=payload.profile_key)
        return {"answer": result.answer, "chunks": [{"chunk_id": c.chunk_id, "content": c.content, "document_name": c.document_name, "similarity": c.similarity, "dataset_id": c.dataset_id} for c in result.chunks], "session_id": result.session_id}

    @router.put("/kbs/{kb_slug}/defaults")
    def update_kb_defaults(kb_slug: str, payload: UpdateKbDefaultsRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return service.update_kb_defaults(current_actor, kb_slug, default_backend_slug=payload.default_backend_slug, default_agent_id=payload.default_agent_id)

    @router.get("/builtin/wiki/kbs")
    def list_builtin_wiki_kbs(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return service.list_kb_status_summaries(current_actor)

    return router
