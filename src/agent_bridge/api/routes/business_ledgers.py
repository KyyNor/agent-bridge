from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response

from agent_bridge.api.schemas import (
    BusinessLedgerQueryRequest,
    BusinessLedgerRecordRequest,
    BusinessLedgerRequest,
    BusinessLedgerUpdateRequest,
)


def create_business_ledger_routes(service, actor):
    router = APIRouter(prefix="/business-ledgers", tags=["business-ledgers"])

    @router.get("")
    def list_ledgers(current_actor: str = Depends(actor)) -> list[dict]:
        return service.business_ledgers.list_ledgers(current_actor)

    @router.post("")
    def create_ledger(payload: BusinessLedgerRequest, current_actor: str = Depends(actor)) -> dict:
        return service.business_ledgers.create_ledger(
            current_actor,
            ledger_key=payload.ledger_key,
            name=payload.name,
            description=payload.description,
            fields=[item.model_dump() for item in payload.fields],
            expected_edit_token=payload.expected_edit_token,
        )

    @router.get("/{ledger_key}")
    def get_ledger(ledger_key: str, current_actor: str = Depends(actor)) -> dict:
        return service.business_ledgers.get_ledger(current_actor, ledger_key)

    @router.put("/{ledger_key}")
    def update_ledger(ledger_key: str, payload: BusinessLedgerUpdateRequest, current_actor: str = Depends(actor)) -> dict:
        return service.business_ledgers.update_ledger(
            current_actor,
            ledger_key,
            name=payload.name,
            description=payload.description,
            fields=[item.model_dump() for item in payload.fields],
            expected_edit_token=payload.expected_edit_token,
        )

    @router.delete("/{ledger_key}")
    def delete_ledger(ledger_key: str, current_actor: str = Depends(actor)) -> dict:
        service.business_ledgers.delete_ledger(current_actor, ledger_key)
        service.store.delete_resource_rules_by_key("business_ledger", ledger_key)
        return {"ledger_key": ledger_key, "deleted": True}

    @router.post("/{ledger_key}/records/query")
    def query_records(ledger_key: str, payload: BusinessLedgerQueryRequest, current_actor: str = Depends(actor)) -> dict:
        return service.business_ledgers.list_records(current_actor, ledger_key, **payload.model_dump())

    @router.post("/{ledger_key}/records")
    def add_record(ledger_key: str, payload: BusinessLedgerRecordRequest, current_actor: str = Depends(actor)) -> dict:
        return service.business_ledgers.add_record(current_actor, ledger_key, payload.values)

    @router.put("/{ledger_key}/records/{record_id}")
    def update_record(ledger_key: str, record_id: str, payload: BusinessLedgerRecordRequest, current_actor: str = Depends(actor)) -> dict:
        return service.business_ledgers.update_record(current_actor, ledger_key, record_id, payload.values)

    @router.delete("/{ledger_key}/records/{record_id}")
    def delete_record(ledger_key: str, record_id: str, current_actor: str = Depends(actor)) -> dict:
        service.business_ledgers.delete_record(current_actor, ledger_key, record_id)
        return {"record_id": record_id, "deleted": True}

    @router.post("/{ledger_key}/imports/xlsx/preview")
    async def preview_xlsx_import(ledger_key: str, file: UploadFile = File(...), current_actor: str = Depends(actor)) -> dict:
        return service.business_ledgers.preview_xlsx_import(current_actor, ledger_key, await file.read())

    @router.get("/{ledger_key}/imports/xlsx/template")
    def download_xlsx_import_template(ledger_key: str, current_actor: str = Depends(actor)) -> Response:
        return Response(
            service.business_ledgers.xlsx_import_template(current_actor, ledger_key),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{ledger_key}-template.xlsx"'},
        )

    @router.post("/{ledger_key}/imports/xlsx/{preview_id}/confirm")
    def confirm_xlsx_import(ledger_key: str, preview_id: str, current_actor: str = Depends(actor)) -> dict:
        return service.business_ledgers.confirm_xlsx_import(current_actor, ledger_key, preview_id)

    @router.get("/{ledger_key}/exports/xlsx")
    def export_xlsx(ledger_key: str, current_actor: str = Depends(actor)) -> Response:
        return Response(
            service.business_ledgers.export_xlsx(current_actor, ledger_key),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{ledger_key}.xlsx"'},
        )

    return router
