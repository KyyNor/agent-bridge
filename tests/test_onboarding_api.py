from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def _headers(actor: str) -> dict[str, str]:
    return {"X-Agent-Bridge-User": actor}


def test_onboarding_tour_status_is_persisted_per_user_and_version(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, {"root"}))

    first = client.get(
        "/onboarding/tours/workflow-first-use?version=1", headers=_headers("root")
    )
    assert first.status_code == 200
    assert first.json() == {
        "tour_key": "workflow-first-use",
        "tour_version": 1,
        "status": None,
        "updated_at": None,
        "should_show": True,
    }

    completed = client.put(
        "/onboarding/tours/workflow-first-use",
        headers=_headers("root"),
        json={"version": 1, "status": "completed"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["should_show"] is False
    assert completed.json()["updated_at"]

    root_completed = client.get(
        "/onboarding/tours/workflow-first-use?version=1", headers=_headers("root")
    )
    assert root_completed.json()["status"] == "completed"
    assert root_completed.json()["should_show"] is False

    other_user = client.get(
        "/onboarding/tours/workflow-first-use?version=1", headers=_headers("alice")
    )
    assert other_user.json()["should_show"] is True

    next_version = client.get(
        "/onboarding/tours/workflow-first-use?version=2", headers=_headers("root")
    )
    assert next_version.json()["should_show"] is True


def test_onboarding_tour_skip_overwrites_only_current_tour_version(wm_paths) -> None:
    client = TestClient(create_app(wm_paths, {"root"}))
    headers = _headers("root")

    assert client.put(
        "/onboarding/tours/workflow-first-use",
        headers=headers,
        json={"version": 1, "status": "completed"},
    ).status_code == 200
    skipped = client.put(
        "/onboarding/tours/workflow-first-use",
        headers=headers,
        json={"version": 1, "status": "skipped"},
    )
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"

    invalid = client.put(
        "/onboarding/tours/workflow-first-use",
        headers=headers,
        json={"version": 0, "status": "skipped"},
    )
    assert invalid.status_code == 422
