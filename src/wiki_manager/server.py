from __future__ import annotations

import shutil
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


def create_app(paths: WikiManagerPaths | None = None, admins: set[str] | None = None) -> FastAPI:
    resolved_paths = paths or WikiManagerPaths.from_root(DEFAULT_ROOT)
    resolved_admins = admins if admins is not None else load_server_config(resolved_paths).admins
    service = WikiManagerService.create(resolved_paths, resolved_admins)
    app = FastAPI(title="wiki-manager", docs_url=None)

    def actor(x_wiki_user: str = Header(alias="X-Wiki-User")) -> str:
        return x_wiki_user

    def call_safely(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except WikiManagerError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
        filename = file.filename or "upload"
        resolved_paths.run_dir.mkdir(parents=True, exist_ok=True)
        upload_dir = resolved_paths.run_dir / f"upload-{current_actor}-{filename}"
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / filename
        try:
            with upload_path.open("wb") as output:
                shutil.copyfileobj(file.file, output)
            return call_safely(lambda: service.add_document(current_actor, upload_path, kb, later))
        finally:
            upload_path.unlink(missing_ok=True)
            upload_dir.rmdir()

    @app.get("/docs")
    def list_docs(kb: str, current_actor: str = Depends(actor)) -> list[dict[str, Any]]:
        return call_safely(lambda: service.list_docs(current_actor, kb))

    @app.get("/docs/{doc_slug}")
    def get_doc(doc_slug: str, current_actor: str = Depends(actor)) -> dict[str, Any]:
        return call_safely(lambda: service.get_doc(current_actor, doc_slug))

    @app.post("/docs/{doc_slug}/versions")
    def update_document(
        doc_slug: str,
        current_actor: str = Depends(actor),
        file: UploadFile = File(),
        later: bool = Form(False),
    ) -> dict[str, Any]:
        filename = file.filename or "upload"
        resolved_paths.run_dir.mkdir(parents=True, exist_ok=True)
        upload_dir = resolved_paths.run_dir / f"upload-{current_actor}-{filename}"
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / filename
        try:
            with upload_path.open("wb") as output:
                shutil.copyfileobj(file.file, output)
            return call_safely(lambda: service.update_document(current_actor, doc_slug, upload_path, later))
        finally:
            upload_path.unlink(missing_ok=True)
            upload_dir.rmdir()

    @app.post("/docs/{doc_slug}/delete")
    def delete_document(doc_slug: str, current_actor: str = Depends(actor)) -> dict[str, str]:
        return call_safely(lambda: service.delete_document(current_actor, doc_slug))

    @app.post("/docs/{doc_slug}/purge")
    def purge_document(doc_slug: str, current_actor: str = Depends(actor)) -> dict[str, str]:
        return call_safely(lambda: service.purge_document(current_actor, doc_slug))

    @app.get("/status")
    def status(current_actor: str = Depends(actor)) -> dict[str, list[dict[str, Any]]]:
        return call_safely(lambda: service.status(current_actor))

    @app.post("/sync")
    def sync(payload: SyncRequest, current_actor: str = Depends(actor)) -> dict[str, int]:
        return call_safely(lambda: service.sync(current_actor, all_users=payload.all_users))

    return app
