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
        {"field_key": "ip", "name": "IP", "field_type": "text", "required": True},
        {"field_key": "name", "name": "名称", "field_type": "text", "query_modes": ["contains"], "agent_readable": True},
        {"field_key": "status", "name": "状态", "field_type": "enum", "enum_values": ["online", "offline"]},
        {"field_key": "cores", "name": "核心数", "field_type": "number"},
        {"field_key": "last_seen", "name": "最后发现", "field_type": "date"},
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


def test_ledger_rejects_undefined_fields_and_queries_not_supported_by_type(service) -> None:
    service.create_ledger("root", ledger_key="asset_inventory", name="资产台账", description="", fields=_fields())
    with pytest.raises(ValidationError, match="未定义字段"):
        service.add_record("root", "asset_inventory", {"ip": "10.0.0.8", "bad": "value"})
    with pytest.raises(ValidationError, match="不支持该查询方式"):
        service.query("asset_inventory", filters={"status": {"op": "contains", "value": "on"}})


def test_ledger_uses_default_query_modes_and_orders_by_multiple_fields(service) -> None:
    ledger = service.create_ledger(
        "root",
        ledger_key="assets",
        name="资产台账",
        description="",
        fields=[
            {"field_key": "name", "name": "名称", "field_type": "text"},
            {"field_key": "category", "name": "分类", "field_type": "text", "query_modes": ["contains"]},
            {"field_key": "cost", "name": "成本", "field_type": "number"},
            {"field_key": "updated_on", "name": "更新时间", "field_type": "date"},
        ],
    )
    assert ledger["fields"][0]["query_modes"] == ["exact"]
    assert ledger["fields"][1]["query_modes"] == ["exact", "contains"]
    assert ledger["fields"][2]["query_modes"] == ["exact", "gt", "gte", "lt", "lte", "between"]
    assert ledger["fields"][3]["query_modes"] == ["exact", "gt", "gte", "lt", "lte", "between"]
    assert all(field["sortable"] for field in ledger["fields"])

    service.add_record("root", "assets", {"name": "A", "category": "系统", "cost": 20, "updated_on": "2026-08-03"})
    service.add_record("root", "assets", {"name": "C", "category": "应用", "cost": 20, "updated_on": "2026-08-01"})
    service.add_record("root", "assets", {"name": "B", "category": "应用", "cost": 10, "updated_on": "2026-08-02"})

    result = service.query(
        "assets",
        filters={"cost": {"op": "gte", "value": 10}, "updated_on": {"op": "lt", "value": "2026-08-03"}},
        sort=[{"field": "cost", "direction": "desc"}, {"field": "name", "direction": "asc"}],
    )

    assert [item["values"]["name"] for item in result["items"]] == ["C", "B"]


def test_ledger_update_rebuilds_snapshot(service) -> None:
    service.create_ledger("root", ledger_key="asset_inventory", name="资产台账", description="", fields=_fields())
    record = service.add_record("root", "asset_inventory", {"ip": "10.0.0.8", "status": "online"})
    service.update_record("root", "asset_inventory", record["record_id"], {"ip": "10.0.0.8", "name": "更新后的名称", "status": "online"})

    assert service.query("asset_inventory", keyword="更新后")["total"] == 1
