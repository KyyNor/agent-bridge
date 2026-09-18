"""DSH Web Runtime 生命周期与配置管理的单元/接口测试。"""

from __future__ import annotations

import json
import os
import time
import types
from pathlib import Path

import pytest


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self) -> int | None:
        return self.returncode


class FakeLauncher:
    def __init__(self, pid: int = 410001) -> None:
        self.next_pid = pid
        self.starts: list[dict] = []

    def start(self, *, command, env, cwd, log_path, identity):
        process = FakeProcess(self.next_pid)
        self.next_pid += 1
        self.starts.append(
            {
                "pid": process.pid,
                "command": list(command),
                "env": dict(env),
                "cwd": str(cwd),
                "log_path": str(log_path),
                "identity": identity,
            }
        )
        return process


@pytest.fixture
def service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.access.upsert_group(actor="root", group_key="groupa", name="A 组")
    svc.access.create_user(actor="root", user_id="user1")
    svc.access.create_user(actor="root", user_id="user2")
    svc.access.set_user_group(actor="root", user_id="user1", group_key="groupa")
    svc.access.set_user_group(actor="root", user_id="user2", group_key="groupa")
    return svc


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "linux-home"
    home.mkdir()
    return home


@pytest.fixture
def passwd_lookup(home):
    def lookup(user: str):
        return types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(home / user))

    return lookup


def configure_runtime(service, **overrides) -> None:
    payload = {
        "group_key": "groupa",
        "linux_user": "groupa",
        "base_url": "http://model.internal/v1",
        "default_model": "gpt-x",
        "available_models": ["gpt-x", "gpt-y"],
        "api_key": "sk-secret",
    }
    payload.update(overrides)
    service.dsh_configs.save_group_config("root", **payload)


def patch_lifecycle(service, monkeypatch, *, alive_pids: set[int] | None = None, healthy_ports: set[int] | None = None, launcher: FakeLauncher | None = None):
    """替换 pid 存活/端口健康/终止三个进程级副作用，测试内闭环。

    传入 ``launcher`` 时，其启动的实例自动视为存活且健康，模拟真实进程行为。
    """
    alive = set(alive_pids or ())
    healthy = set(healthy_ports or ())
    terminated: list[int] = []

    def fake_pid_alive(pid: int) -> bool:
        if launcher is not None and any(start["pid"] == pid for start in launcher.starts):
            return True
        return pid in alive

    def fake_probe(port: int) -> bool:
        if launcher is not None and any(int(start["env"]["DSH_PORT"]) == port for start in launcher.starts):
            return True
        return port in healthy

    def fake_terminate(pid: int, *, grace_seconds: float) -> bool:
        terminated.append(pid)
        alive.discard(pid)
        healthy.discard(pid)
        return True

    monkeypatch.setattr(service.dsh, "_pid_alive", staticmethod(fake_pid_alive))
    monkeypatch.setattr(service.dsh, "_probe_port", staticmethod(fake_probe))
    monkeypatch.setattr(service.dsh, "_terminate_pid", fake_terminate)
    return terminated


def install_fake_launcher(service, home, passwd_lookup) -> FakeLauncher:
    launcher = FakeLauncher()
    service.dsh._launcher = launcher
    service.dsh._passwd_lookup = passwd_lookup
    return launcher


# -- 配置服务 --


def test_group_config_validation_and_masking(service) -> None:
    with pytest.raises(Exception) as exc_info:
        service.dsh_configs.save_group_config(
            "root", group_key="groupa", default_model="gpt-x", available_models=["gpt-y"]
        )
    assert "default_model" in str(exc_info.value)

    with pytest.raises(Exception):
        service.dsh_configs.save_group_config(
            "root", group_key="groupa", base_url="not-a-url"
        )

    with pytest.raises(Exception):
        service.dsh_configs.save_group_config("root", group_key="missing-group")

    saved = service.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        base_url="http://model.internal/v1",
        default_model="gpt-x",
        available_models=["gpt-x", "gpt-y", "gpt-x"],
        api_key="sk-secret",
    )
    assert saved["api_key_set"] is True
    assert "api_key" not in saved
    assert saved["available_models"] == ["gpt-x", "gpt-y"]
    # runtime 读取入口能拿到原值
    raw = service.dsh_configs.group_config_for_runtime("groupa")
    assert raw["api_key"] == "sk-secret"

    with pytest.raises(Exception) as conflict:
        service.dsh_configs.save_group_config(
            "root",
            group_key="groupa",
            base_url="http://model.internal/v1",
            default_model="gpt-x",
            available_models=["gpt-x"],
            api_key=None,
            expected_edit_token="stale-token",
        )
    assert "更新" in str(conflict.value) or "409" in str(conflict.value)


def test_group_config_api_key_clear_semantics(service) -> None:
    service.dsh_configs.save_group_config(
        "root", group_key="groupa", base_url="", available_models=["m1"], default_model="m1", api_key="sk-1"
    )
    cleared = service.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        base_url="",
        available_models=["m1"],
        default_model="m1",
        clear_api_key=True,
        expected_edit_token=service.dsh_configs.get_group_config("root", "groupa")["edit_token"],
    )
    assert cleared["api_key_set"] is False
    assert service.dsh_configs.group_config_for_runtime("groupa")["api_key"] == ""


def test_runtime_config_roundtrip_and_defaults(service) -> None:
    default_config = service.dsh_configs.get_runtime_config("root")
    assert default_config["web_command"]
    assert default_config["idle_timeout_minutes"] > 0

    saved = service.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh-server run --port {port}",
        idle_timeout_minutes=30,
        expected_edit_token=default_config["edit_token"],
    )
    assert saved["web_command"] == "dsh-server run --port {port}"
    assert service.dsh_configs.runtime_config_for_runtime()["idle_timeout_minutes"] == 30

    with pytest.raises(Exception):
        service.dsh_configs.save_runtime_config(
            "root", web_command="", idle_timeout_minutes=0, expected_edit_token=saved["edit_token"]
        )


def test_config_endpoints_require_admin(service) -> None:
    from fastapi.testclient import TestClient
    from agent_bridge.api.app import create_app

    client = TestClient(create_app(service.paths, {"root"}))
    assert client.get("/api/v1/dsh/group-configs", headers={"X-Agent-Bridge-User": "user1"}).status_code == 403
    assert client.get("/api/v1/dsh/group-configs", headers={"X-Agent-Bridge-User": "root"}).status_code == 200


# -- Runtime 生命周期 --


def test_ensure_running_starts_reuses_and_injects_env(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    patch_lifecycle(service, monkeypatch, launcher=launcher)

    first = service.dsh.ensure_running("user1")
    assert first["status"] == "running"
    assert "port" not in first and "pid" not in first
    assert first["group_key"] == "groupa"

    assert len(launcher.starts) == 1
    start = launcher.starts[0]
    assert start["env"]["DSH_HOME"] == str(home / "groupa" / ".config" / "dsh" / "user1")
    assert start["env"]["DSH_BASE_URL"] == "http://model.internal/v1"
    assert start["env"]["DSH_API_KEY"] == "sk-secret"
    assert start["env"]["DSH_DEFAULT_MODEL"] == "gpt-x"
    assert start["env"]["DSH_AVAILABLE_MODELS"] == "gpt-x,gpt-y"
    assert start["cwd"] == str(home / "groupa")
    assert "{port}" not in " ".join(start["command"])
    assert str(home / "groupa" / ".config" / "dsh" / "user1") == start["env"]["DSH_HOME"]
    # 配置目录确实创建在 Linux 用户 home 下，而不是 Agent Bridge data 目录
    assert (home / "groupa" / ".config" / "dsh" / "user1").is_dir()
    assert not (service.paths.data_dir / "dsh").exists()

    # 第二次进入复用同一个实例
    second = service.dsh.ensure_running("user1")
    assert second["status"] == "running"
    assert len(launcher.starts) == 1

    # 同 group 的另一个业务用户目录互相隔离
    third = service.dsh.ensure_running("user2")
    assert third["status"] == "running"
    assert len(launcher.starts) == 2
    assert launcher.starts[1]["env"]["DSH_HOME"] == str(home / "groupa" / ".config" / "dsh" / "user2")


def test_ensure_running_rejects_unassigned_or_unconfigured(service) -> None:
    service.access.create_user(actor="root", user_id="lonely")
    with pytest.raises(Exception):
        service.dsh.ensure_running("lonely")

    configure_runtime(service)
    service.access.create_user(actor="root", user_id="other-group-user")
    service.access.upsert_group(actor="root", group_key="groupb", name="B 组")
    service.access.set_user_group(actor="root", user_id="other-group-user", group_key="groupb")
    with pytest.raises(Exception) as exc_info:
        service.dsh.ensure_running("other-group-user")
    assert "尚未配置" in str(exc_info.value)


def test_dead_process_state_is_restarted(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    terminated = patch_lifecycle(service, monkeypatch, launcher=launcher)

    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": 400123,
        "port": 48400,
        "config_dir": str(home / "groupa" / ".config" / "dsh" / "user1"),
        "log_path": str(service.paths.logs_dir / "dsh-runtimes" / "user1.log"),
        "started_at": time.time() - 3600,
        "last_access_at": time.time() - 3600,
    }
    service.dsh._write_state("user1", state)

    result = service.dsh.ensure_running("user1")
    assert result["status"] == "running"
    assert len(launcher.starts) == 1
    assert terminated == []  # 进程已死，无需发信号
    assert service.dsh._read_state("user1")["pid"] == launcher.starts[0]["pid"]


def test_wedged_process_is_restarted_after_timeout(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    # 老实例存活但端口长期不健康（启动早已超过就绪窗口）
    terminated = patch_lifecycle(service, monkeypatch, alive_pids={400123}, launcher=launcher)

    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": 400123,
        "port": 48400,
        "config_dir": "",
        "log_path": "",
        "started_at": time.time() - 3600,
        "last_access_at": time.time() - 3600,
    }
    service.dsh._write_state("user1", state)

    result = service.dsh.ensure_running("user1")
    assert result["status"] == "running"
    assert terminated == [400123]
    assert len(launcher.starts) == 1


def test_starting_instance_is_reused_without_restart(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    # 老实例存活、尚未通过健康探测，但仍在启动窗口内：不重启，返回 starting
    patch_lifecycle(service, monkeypatch, alive_pids={400124})

    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": 400124,
        "port": 48400,
        "config_dir": "",
        "log_path": "",
        "started_at": time.time() - 5,
        "last_access_at": time.time() - 5,
    }
    service.dsh._write_state("user1", state)

    result = service.dsh.ensure_running("user1")
    assert result["status"] == "starting"
    assert launcher.starts == []


def test_group_change_restarts_runtime(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    service.access.upsert_group(actor="root", group_key="groupb", name="B 组")
    service.dsh_configs.save_group_config(
        "root",
        group_key="groupb",
        base_url="http://model.internal/v1",
        default_model="gpt-x",
        available_models=["gpt-x"],
    )

    old_pid = 410100
    terminated = patch_lifecycle(
        service, monkeypatch, alive_pids={old_pid}, healthy_ports={48400}, launcher=launcher
    )
    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": old_pid,
        "port": 48400,
        "config_dir": "",
        "log_path": "",
        "started_at": time.time(),
        "last_access_at": time.time(),
    }
    service.dsh._write_state("user1", state)

    service.access.set_user_group(actor="root", user_id="user1", group_key="groupb")
    result = service.dsh.ensure_running("user1")
    assert result["status"] == "running"
    assert result["group_key"] == "groupb"
    assert terminated == [old_pid]


def test_stop_runtime_keeps_config_dir(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    pid = 410200
    patch_lifecycle(service, monkeypatch, alive_pids={pid}, healthy_ports={48400})
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    config_dir.mkdir(parents=True)
    (config_dir / "session.json").write_text("{}", encoding="utf-8")
    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": pid,
        "port": 48400,
        "config_dir": str(config_dir),
        "log_path": "",
        "started_at": time.time(),
        "last_access_at": time.time(),
    }
    service.dsh._write_state("user1", state)

    stopped = service.dsh.stop_runtime("user1")
    assert stopped["stopped"] is True
    assert service.dsh._read_state("user1") is None
    # 停止 Runtime 不删除用户 DSH 配置和 session 数据
    assert (config_dir / "session.json").exists()


def test_idle_reaper_stops_expired_only(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    install_fake_launcher(service, home, passwd_lookup)
    terminated = patch_lifecycle(service, monkeypatch, alive_pids={410300, 410301}, healthy_ports={48400, 48401})
    old = time.time() - 10 * 24 * 60 * 60
    for user_id, pid, port, last_access in (("user1", 410300, 48400, old), ("user2", 410301, 48401, time.time())):
        service.dsh._write_state(
            user_id,
            {
                "user_id": user_id,
                "group_key": "groupa",
                "linux_user": "groupa",
                "pid": pid,
                "port": port,
                "config_dir": "",
                "log_path": "",
                "started_at": last_access,
                "last_access_at": last_access,
            },
        )

    stopped = service.dsh.stop_idle_expired()
    assert stopped == ["user1"]
    assert service.dsh._read_state("user1") is None
    assert service.dsh._read_state("user2") is not None
    assert 410300 in terminated and 410301 not in terminated


def test_touch_refreshes_last_access(service, monkeypatch) -> None:
    old = time.time() - 3600
    service.dsh._write_state(
        "user1",
        {
            "user_id": "user1",
            "group_key": "groupa",
            "linux_user": "groupa",
            "pid": 410400,
            "port": 48400,
            "config_dir": "",
            "log_path": "",
            "started_at": old,
            "last_access_at": old,
        },
    )
    service.dsh.touch_runtime("user1")
    assert service.dsh._read_state("user1")["last_access_at"] > old


def test_recover_cleans_dead_and_keeps_alive(service, monkeypatch) -> None:
    patch_lifecycle(service, monkeypatch, alive_pids={410500}, healthy_ports={48400})
    for user_id, pid in (("user1", 410500), ("user2", 410501)):
        service.dsh._write_state(
            user_id,
            {
                "user_id": user_id,
                "group_key": "groupa",
                "linux_user": "groupa",
                "pid": pid,
                "port": 48400,
                "config_dir": "",
                "log_path": "",
                "started_at": time.time(),
                "last_access_at": time.time(),
            },
        )
    result = service.dsh.recover()
    assert result == {"kept": 1, "cleaned": 1}
    assert service.dsh._read_state("user1") is not None
    assert service.dsh._read_state("user2") is None


def test_port_pool_skips_occupied(service, monkeypatch) -> None:
    service.dsh._write_state(
        "user2",
        {
            "user_id": "user2",
            "group_key": "groupa",
            "linux_user": "groupa",
            "pid": 410600,
            "port": 48400,
            "config_dir": "",
            "log_path": "",
            "started_at": time.time(),
            "last_access_at": time.time(),
        },
    )
    patch_lifecycle(service, monkeypatch, alive_pids={410600})
    monkeypatch.setattr(service.dsh, "_port_in_use", staticmethod(lambda port: port == 48401))
    assert service.dsh._available_port() == 48402


def test_command_template_validation(service) -> None:
    with pytest.raises(Exception):
        service.dsh._build_command("dsh {unknown} --port {port}", 100)
    assert service.dsh._build_command("dsh web --port {port}", 48500) == ["dsh", "web", "--port", "48500"]


def test_require_runtime_target_reads_registered_state_only(service, monkeypatch) -> None:
    patch_lifecycle(service, monkeypatch, alive_pids={410700}, healthy_ports={48410})
    service.dsh._write_state(
        "user1",
        {
            "user_id": "user1",
            "group_key": "groupa",
            "linux_user": "groupa",
            "pid": 410700,
            "port": 48410,
            "config_dir": "",
            "log_path": "",
            "started_at": time.time(),
            "last_access_at": time.time() - 600,
        },
    )
    target = service.dsh.require_runtime_target("user1")
    assert target == "http://127.0.0.1:48410"
    # 访问刷新了空闲时间
    assert service.dsh._read_state("user1")["last_access_at"] > time.time() - 60
    assert service.dsh.require_runtime_target("user2") is None


# -- API 集成 --


def test_dsh_runtime_api_flow(service, home, passwd_lookup, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from agent_bridge.api.app import create_app

    configure_runtime(service)
    client = TestClient(create_app(service.paths, {"root"}))
    headers_user = {"X-Agent-Bridge-User": "user1"}
    headers_root = {"X-Agent-Bridge-User": "root"}

    # create_app 会基于同一数据目录装配新的 service；进程类 fakes 需装在 app 实例上
    app_service = client.app.state.agent_bridge_service
    install_fake_launcher(app_service, home, passwd_lookup)

    status = client.get("/api/v1/dsh/runtime", headers=headers_user).json()
    assert status["status"] == "stopped"

    # 先保存一份合法配置（经 API 落库）
    saved = client.put(
        "/api/v1/dsh/group-configs/groupa",
        headers=headers_root,
        json={
            "linux_user": "groupa",
            "base_url": "http://model.internal/v1",
            "default_model": "gpt-x",
            "available_models": ["gpt-x", "gpt-y"],
            "api_key": "sk-secret",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["api_key_set"] is True
    assert "api_key" not in saved.json()

    launcher = app_service.dsh._launcher
    patch_lifecycle(app_service, monkeypatch, launcher=launcher)

    ensured = client.post("/api/v1/dsh/runtime/ensure", headers=headers_user)
    assert ensured.status_code == 200
    payload = ensured.json()
    assert payload["status"] == "running"
    assert "port" not in payload and "pid" not in payload

    # 管理端可以看到端口等细节，普通用户被拒绝
    listed = client.get("/api/v1/dsh/runtimes", headers=headers_root).json()
    assert listed["runtimes"][0]["port"] > 0
    assert client.get("/api/v1/dsh/runtimes", headers=headers_user).status_code == 403

    stopped = client.post("/api/v1/dsh/runtime/stop", headers=headers_user)
    assert stopped.status_code == 200
    assert stopped.json()["stopped"] is True

    # 未识别身份的调用被拒绝
    assert client.get("/api/v1/dsh/runtime").status_code == 401


def test_dsh_runtime_config_api_roundtrip(service) -> None:
    from fastapi.testclient import TestClient
    from agent_bridge.api.app import create_app

    client = TestClient(create_app(service.paths, {"root"}))
    headers_root = {"X-Agent-Bridge-User": "root"}

    current = client.get("/api/v1/dsh/runtime-config", headers=headers_root)
    assert current.status_code == 200
    saved = client.put(
        "/api/v1/dsh/runtime-config",
        headers=headers_root,
        json={
            "web_command": "dsh web --host 127.0.0.1 --port {port}",
            "idle_timeout_minutes": 90,
            "expected_edit_token": current.json()["edit_token"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["idle_timeout_minutes"] == 90

    conflict = client.put(
        "/api/v1/dsh/runtime-config",
        headers=headers_root,
        json={
            "web_command": "dsh web --host 127.0.0.1 --port {port}",
            "idle_timeout_minutes": 90,
            "expected_edit_token": "stale",
        },
    )
    assert conflict.status_code == 409
