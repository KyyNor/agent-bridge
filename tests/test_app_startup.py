from __future__ import annotations

import threading
import time
import sqlite3

from fastapi.testclient import TestClient

from agent_bridge.api.app import create_app


def test_existing_business_ledger_migrates_after_main_access_schema(wm_paths) -> None:
    """升级旧台账时，归属回填必须晚于主库权限表初始化。"""
    wm_paths.ledger_db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(wm_paths.ledger_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE business_ledgers (
              ledger_key TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              definition_json TEXT NOT NULL,
              current_revision_no INTEGER NOT NULL DEFAULT 1,
              created_by TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO business_ledgers (
              ledger_key, name, definition_json, created_by
            ) VALUES ('legacy', '存量台账', '{"fields": []}', 'root')
            """
        )

    app = create_app(paths=wm_paths, admins={"root"})

    with sqlite3.connect(wm_paths.ledger_db_path) as conn:
        owner_group_key, visibility = conn.execute(
            "SELECT owner_group_key, visibility FROM business_ledgers WHERE ledger_key = 'legacy'"
        ).fetchone()
    assert owner_group_key == "system-maintainers"
    assert visibility == "group"
    assert app.state.agent_bridge_service.business_ledgers.get_ledger("root", "legacy")


def test_startup_does_not_block_on_managed_plugins(wm_paths) -> None:
    """ensure_managed_plugins 做阻塞网络 I/O，必须在后台跑、不能挡住 /health 就绪。"""
    app = create_app(paths=wm_paths, admins={"root"})
    service = app.state.agent_bridge_service

    refresh_started = threading.Event()
    refresh_duration = 0.8

    def slow_refresh() -> None:
        # 模拟一次缓慢的 git pull（真实场景 ~2s/仓库，慢网络下最高 60s）。
        refresh_started.set()
        time.sleep(refresh_duration)

    service.ensure_managed_plugins = slow_refresh  # type: ignore[assignment]

    started = time.monotonic()
    with TestClient(app) as client:
        startup_elapsed = time.monotonic() - started
        # 若仍在 lifespan 里同步 await，startup_elapsed 会 >= refresh_duration。
        assert startup_elapsed < refresh_duration, startup_elapsed
        assert client.get("/health").status_code == 200
        # 后台任务仍然会真正执行刷新。
        assert refresh_started.wait(timeout=refresh_duration + 2)


def test_shutdown_stops_claude_mem_workers(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    service = app.state.agent_bridge_service
    stopped = []

    service.ensure_managed_plugins = lambda: None  # type: ignore[assignment]
    service.memory.worker_service.stop_all_workers = lambda: stopped.append(True)  # type: ignore[attr-defined]

    with TestClient(app):
        pass

    assert stopped == [True]


def test_shutdown_stops_active_codegraph_processes(wm_paths) -> None:
    app = create_app(paths=wm_paths, admins={"root"})
    service = app.state.agent_bridge_service
    stopped = []

    service.ensure_managed_plugins = lambda: None  # type: ignore[assignment]
    service.codegraph.stop_active_processes = lambda: stopped.append(True)  # type: ignore[assignment]

    with TestClient(app):
        pass

    assert stopped == [True]
