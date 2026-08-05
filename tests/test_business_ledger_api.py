from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def test_business_ledger_management_api(wm_paths) -> None:
    app = create_app(wm_paths, {"root"})
    with TestClient(app) as client:
        headers = {"X-Agent-Bridge-User": "root"}
        created = client.post(
            "/business-ledgers",
            headers=headers,
            json={
                "ledger_key": "assets",
                "name": "资产台账",
                "fields": [
                    {"field_key": "name", "name": "名称", "field_type": "text", "query_modes": ["contains"]},
                    {"field_key": "cost", "name": "成本", "field_type": "number", "query_modes": ["between"]},
                ],
            },
        )
        assert created.status_code == 200
        added = client.post("/business-ledgers/assets/records", headers=headers, json={"values": {"name": "支付系统", "cost": 12}})
        assert added.status_code == 200
        found = client.post(
            "/business-ledgers/assets/records/query",
            headers=headers,
            json={"keyword": "支付", "filters": {"cost": {"op": "between", "from": 10, "to": 20}}},
        )
        assert found.status_code == 200
        assert found.json()["total"] == 1
