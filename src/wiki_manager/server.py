from __future__ import annotations

import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from wiki_manager.config import DEFAULT_ROOT, WikiManagerPaths, load_server_config
from wiki_manager.domain import KbRole, WikiManagerError
from wiki_manager.services import WikiManagerService


class CreateKbRequest(BaseModel):
    slug: str
    name: str
    description: str = ""


class GrantMemberRequest(BaseModel):
    linux_user: str
    role: KbRole


class SyncRequest(BaseModel):
    all_users: bool = False


class AskRequest(BaseModel):
    kb: str
    question: str
    backend: str | None = None
    session_id: str | None = None


class PurgeRequest(BaseModel):
    confirm: bool = False


def create_app(paths: WikiManagerPaths | None = None, admins: set[str] | None = None) -> FastAPI:
    resolved_paths = paths or WikiManagerPaths.from_root(DEFAULT_ROOT)
    resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
    service = WikiManagerService.create(resolved_paths, resolved_admins)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.align_backends()
        yield

    app = FastAPI(title="wiki-manager", docs_url=None, openapi_url=None, redoc_url=None, lifespan=lifespan)

    def actor(x_wiki_user: str = Header(alias="X-Wiki-User")) -> str:
        return x_wiki_user

    def call_safely(call: Callable[[], Any]) -> Any:
        try:
            if admins is None:
                service.admins = load_server_config(resolved_paths).admins
            return call()
        except WikiManagerError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    def save_upload(file: UploadFile) -> Path:
        resolved_paths.run_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(upload_filename(file)).suffix
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, dir=resolved_paths.run_dir, suffix=suffix) as output:
                tmp_path = Path(output.name)
                shutil.copyfileobj(file.file, output)
            return tmp_path
        except Exception:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise

    def upload_filename(file: UploadFile) -> str:
        return Path((file.filename or "upload").replace("\\", "/")).name or "upload"

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/backends")
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

    @app.post("/admin/init")
    def init_system(current_actor: str = Depends(actor)) -> None:
        return call_safely(lambda: service.init_system())

    @app.post("/kbs")
    def create_kb(payload: CreateKbRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return call_safely(lambda: service.create_kb(current_actor, payload.slug, payload.name, payload.description))

    @app.get("/kbs")
    def list_kbs(current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_kbs(current_actor))

    @app.post("/kbs/{kb_slug}/members")
    def grant_kb_member(
        kb_slug: str,
        payload: GrantMemberRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, str]:
        return call_safely(lambda: service.grant_kb_member(current_actor, kb_slug, payload.linux_user, payload.role))

    @app.get("/kbs/{kb_slug}/members")
    def list_kb_members(kb_slug: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_kb_members(current_actor, kb_slug))

    @app.post("/docs")
    def add_document(
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        kb: list[str] = Form(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        upload_path = save_upload(file)
        try:
            return call_safely(
                lambda: service.add_document(
                    current_actor,
                    upload_path,
                    kb,
                    later,
                    original_filename=upload_filename(file),
                )
            )
        finally:
            upload_path.unlink(missing_ok=True)

    @app.get("/docs")
    def list_docs(kb: str, backend: str | None = None, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_docs(current_actor, kb, backend=backend))

    @app.get("/docs/{doc_slug}")
    def get_doc(doc_slug: str, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return call_safely(lambda: service.get_doc(current_actor, doc_slug, backend=backend))

    @app.post("/docs/{doc_slug}/versions")
    def update_document(
        doc_slug: str,
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        upload_path = save_upload(file)
        try:
            return call_safely(
                lambda: service.update_document(
                    current_actor,
                    doc_slug,
                    upload_path,
                    later,
                    original_filename=upload_filename(file),
                )
            )
        finally:
            upload_path.unlink(missing_ok=True)

    @app.post("/docs/{doc_slug}/delete")
    def delete_document(doc_slug: str, current_actor: str = Depends(actor)) -> dict[str, str]:
        return call_safely(lambda: service.delete_document(current_actor, doc_slug))

    @app.post("/docs/{doc_slug}/purge")
    def purge_document(
        doc_slug: str,
        payload: PurgeRequest,
        current_actor: str = Depends(actor),
    ) -> dict[str, str]:
        return call_safely(lambda: service.purge_document(current_actor, doc_slug, confirm=payload.confirm))

    @app.get("/status")
    def status(backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, list[dict[str, Any]]]:
        return call_safely(lambda: service.status(current_actor, backend=backend))

    @app.post("/sync")
    def sync(payload: SyncRequest, backend: str | None = None, current_actor: str = Depends(actor)) -> dict[str, int]:
        return call_safely(lambda: service.sync(current_actor, all_users=payload.all_users, backend=backend))

    @app.get("/search")
    def search(q: str, kb: str, backend: str | None = None, top_k: int = 6, current_actor: str = Depends(actor)) -> dict[str, Any]:
        results = call_safely(
            lambda: service.search(current_actor, kb, q, backend_slug=backend, top_k=top_k)
        )
        return {"results": [{"chunk_id": r.chunk_id, "content": r.content, "document_name": r.document_name, "similarity": r.similarity, "dataset_id": r.dataset_id} for r in results]}

    @app.post("/ask")
    def ask(payload: AskRequest, current_actor: str = Depends(actor)) -> dict[str, Any]:
        result = call_safely(
            lambda: service.ask(current_actor, payload.kb, payload.question, backend_slug=payload.backend, session_id=payload.session_id)
        )
        return {
            "answer": result.answer,
            "chunks": [{"chunk_id": c.chunk_id, "content": c.content, "document_name": c.document_name, "similarity": c.similarity, "dataset_id": c.dataset_id} for c in result.chunks],
            "session_id": result.session_id,
        }

    return app
