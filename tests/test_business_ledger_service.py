from __future__ import annotations

import pytest

from agent_bridge.business_ledger.service import BusinessLedgerService
from agent_bridge.core.domain import ValidationError


@pytest.fixture
def service(wm_paths):
    result = BusinessLedgerService(db_path=wm_paths.ledger_db_path, admins={"root"})
    result.init_schema()
    return result


def _fields():
    return [
        {"field_key": "ip", "name": "IP", "field_type": "text", "required": True, "query_modes": ["exact", "prefix"]},
        {"field_key": "name", "name": "名称", "field_type": "text", "query_modes": ["contains"], "agent_readable": True},
        {"field_key": "status", "name": "状态", "field_type": "enum", "enum_values": ["online", "offline"], "query_modes": ["exact", "in"]},
        {"field_key": "cores", "name": "核心数", "field_type": "number", "query_modes": ["between", "gte"], "sortable": True},
        {"field_key": "last_seen", "name": "最后发现", "field_type": "date", "query_modes": ["between", "after"], "sortable": True},
    ]


def test_ledger_query_uses_in_memory_snapshot_for_typed_and_like_filters(service) -> None:
    service.create_ledger("root", ledger_key="asset_inventory", name="资产台账", description="", fields=_fields())
    service.add_record("root", "asset_inventory", {"ip": "10.0.0.8", "name": "支付应用", "status": "online", "cores": 8, "last_seen": "2026-08-02"})
    service.add_record("root", "asset_inventory", {"ip": "10.0.1.8", "name": "日志服务", "status": "offline", "cores": 4, "last_seen": "2026-08-04"})

    result = service.query(
        "asset_inventory",
        filters={"cores": {"op": "between", "from": 6, "to": 10}, "last_seen": {"op": "between", "from": "2026-08-01", "to": "2026-08-03"}},
        keyword="支付",
    )

    assert result["total"] == 1
    assert result["items"][0]["values"]["ip"] == "10.0.0.8"


def test_ledger_rejects_undefined_fields_and_unconfigured_queries(service) -> None:
    service.create_ledger("root", ledger_key="asset_inventory", name="资产台账", description="", fields=_fields())
    with pytest.raises(ValidationError, match="未定义字段"):
        service.add_record("root", "asset_inventory", {"ip": "10.0.0.8", "bad": "value"})
    with pytest.raises(ValidationError, match="不支持该查询方式"):
        service.query("asset_inventory", filters={"status": {"op": "contains", "value": "on"}})


def test_ledger_update_rebuilds_snapshot(service) -> None:
    service.create_ledger("root", ledger_key="asset_inventory", name="资产台账", description="", fields=_fields())
    record = service.add_record("root", "asset_inventory", {"ip": "10.0.0.8", "status": "online"})
    service.update_record("root", "asset_inventory", record["record_id"], {"ip": "10.0.0.8", "name": "更新后的名称", "status": "online"})

    assert service.query("asset_inventory", keyword="更新后")["total"] == 1
