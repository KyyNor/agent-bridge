"""DSH 小组共享 Workspace（issue #13）的 scope、复用、Profile 锁定与隔离测试。"""

from __future__ import annotations

import json
import os
import threading
import time
import types
from pathlib import Path

import pytest
import yaml


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self) -> int | None:
        return self.returncode


class FakeLauncher:
    def __init__(self, pid: int = 430001) -> None:
        self.next_pid = pid
        self.starts: list[dict] = []
        self.once_calls: list[list[str]] = []

    def start(self, *, command, env, cwd, log_path, identity):
        process = FakeProcess(self.next_pid)
        self.next_pid += 1
        port = 0
        if "--port" in command:
            try:
                port = int(command[command.index("--port") + 1])
            except (IndexError, ValueError):
                port = 0
        self.starts.append(
            {"pid": process.pid, "command": list(command), "port": port, "log_path": str(log_path)}
        )
        return process

    def run_once(self, *, command, env, cwd, identity, timeout_seconds):
        self.once_calls.append(list(command))
        return 0, "installed"


@pytest.fixture
def service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.access.upsert_group(actor="root", group_key="groupa", name="A 组")
    for user_id in ("user1", "user2"):
        svc.access.create_user(actor="root", user_id=user_id)
        svc.access.set_user_group(actor="root", user_id=user_id, group_key="groupa")
    # Profile 是组内资源：由 groupa 成员创建，避免归入 root 的维护组
    svc.governance.upsert_profile("user1", "safe", "安全平面", "", "active")
    svc.governance.upsert_profile("user1", "wide", "宽平面", "", "active")
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


def configure_group(service) -> None:
    service.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        idle_timeout_minutes=120,
        base_url="http://model.internal/v1",
        available_models=["gpt-x"],
    )
    service.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        linux_user="groupa",
        default_model="gpt-x",
        api_key="sk-secret",
    )


def install_fakes(service, home, passwd_lookup, monkeypatch) -> FakeLauncher:
    launcher = FakeLauncher()
    service.dsh._launcher = launcher
    service.dsh._passwd_lookup = passwd_lookup
    monkeypatch.setattr(
        service.dsh,
        "_pid_alive",
        staticmethod(lambda pid: any(start["pid"] == pid for start in launcher.starts)),
    )
    monkeypatch.setattr(
        service.dsh,
        "_probe_port",
        staticmethod(lambda port: any(int(start["port"]) == port for start in launcher.starts)),
    )
    return launcher


def shared_state_path(service, linux_user: str = "groupa") -> Path:
    return (
        service.dsh._state_dir()
        / "shared"
        / f"{linux_user}.json"
    )


# -- 目录与 runtime 身份 --


def test_shared_scope_uses_linux_user_dsh_home_and_state(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    result = service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")

    assert result["status"] == "running"
    assert result["scope"] == "shared"
    assert result["profile_key"] == "safe"
    assert result["workspace_url"] == "/agent-workspace-shared/"
    # 共享 DSH_HOME：<linux home>/.config/dsh/<linux-user>/
    assert result["config_dir"] == str(home / "groupa" / ".config" / "dsh" / "groupa")
    state = json.loads(shared_state_path(service).read_text(encoding="utf-8"))
    assert state["scope"] == "shared"
    assert state["user_id"] == "groupa"
    assert state["linux_user"] == "groupa"
    assert state["profile_key"] == "safe"

    # 个人模式仍使用 <business-user> 目录，两者互不影响
    personal = service.dsh.authorize_workspace("user1", profile_key="safe")
    assert personal["config_dir"] == str(home / "groupa" / ".config" / "dsh" / "user1")
    assert len(launcher.starts) == 2


def test_personal_scope_unchanged_default(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    result = service.dsh.authorize_workspace("user1", profile_key="safe")
    assert result["scope"] == "personal"
    assert result["workspace_url"] == "/agent-workspace/"
    assert not shared_state_path(service).exists()
    assert service.dsh._state_path("personal", "user1").exists()


# -- 共享 runtime 复用与 Profile 锁定 --


def test_shared_runtime_reused_by_second_member_with_locked_profile(
    service, home, passwd_lookup, monkeypatch
) -> None:
    """共享 Runtime 使用 runtime 级稳定 capability：后续成员进入不重签发、不重写 patch。"""
    from agent_bridge.dsh.service import shared_runtime_capability_key
    from agent_bridge.dsh.workspace import mcp_overlay_path

    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    first = service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")
    assert len(launcher.starts) == 1
    config_dir = home / "groupa" / ".config" / "dsh" / "groupa"
    overlay_path = mcp_overlay_path(config_dir)
    first_overlay_text = overlay_path.read_text(encoding="utf-8")
    first_overlay = yaml.safe_load(first_overlay_text)
    first_token = first_overlay[0]["insert"][0]["config"]["headers"]["X-Agent-Bridge-DSH-Capability"]

    # 第二名成员请求不同平面：直接进入现有 Runtime，active profile 锁定为 safe
    second = service.dsh.authorize_workspace("user2", profile_key="wide", scope="shared")
    assert second["status"] == "running"
    assert second["profile_key"] == "safe"
    assert len(launcher.starts) == 1  # 不产生第二个进程
    state = json.loads(shared_state_path(service).read_text(encoding="utf-8"))
    assert state["pid"] == launcher.starts[0]["pid"]
    assert state["profile_key"] == "safe"
    # patch 文件逐字节不变：runtime capability 不因成员进入重写。
    assert overlay_path.read_text(encoding="utf-8") == first_overlay_text
    # runtime capability 仍有效，且 user_id 为 Linux 用户（共享身份审计）。
    capability = service.dsh.capabilities.require(first_token, profile_key="safe")
    assert capability.user_id == "groupa"
    # registry 内不存在绑定成员身份的共享签发（只有 runtime 槽位）。
    assert shared_runtime_capability_key("groupa") in service.dsh.capabilities._by_user
    assert "user1" not in service.dsh.capabilities._by_user
    assert "user2" not in service.dsh.capabilities._by_user


def test_personal_and_shared_capabilities_do_not_invalidate_each_other(
    service, home, passwd_lookup, monkeypatch
) -> None:
    """同一用户同时打开个人与共享工作台：两边 capability 互不失效。"""
    from agent_bridge.dsh.workspace import mcp_overlay_path

    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    # 先进入个人（profile safe），再进入共享（profile wide）：个人 capability 仍有效。
    service.dsh.authorize_workspace("user1", profile_key="safe")
    personal_overlay = yaml.safe_load(
        (home / "groupa" / ".config" / "dsh" / "user1" / "agent-bridge-mcp.patch.yml")
        .read_text(encoding="utf-8")
    )
    personal_token = personal_overlay[0]["insert"][0]["config"]["headers"]["X-Agent-Bridge-DSH-Capability"]

    service.dsh.authorize_workspace("user1", profile_key="wide", scope="shared")
    # 个人 capability 未被共享进入撤销。
    personal_capability = service.dsh.capabilities.require(personal_token, profile_key="safe")
    assert personal_capability.user_id == "user1"

    # 共享 runtime capability 独立存在且有效。
    shared_overlay = yaml.safe_load(
        mcp_overlay_path(home / "groupa" / ".config" / "dsh" / "groupa").read_text(encoding="utf-8")
    )
    shared_token = shared_overlay[0]["insert"][0]["config"]["headers"]["X-Agent-Bridge-DSH-Capability"]
    shared_capability = service.dsh.capabilities.require(shared_token, profile_key="wide")
    assert shared_capability.user_id == "groupa"

    # 用户再进入个人另一平面：个人槽位被替换（个人语义），共享 capability 不受影响。
    service.dsh.authorize_workspace("user1", profile_key="wide")
    with pytest.raises(Exception):
        service.dsh.capabilities.require(personal_token)
    service.dsh.capabilities.require(shared_token, profile_key="wide")

    # 停止共享 runtime：撤销共享 capability，不影响用户当前个人 capability。
    newest_personal_overlay = yaml.safe_load(
        (home / "groupa" / ".config" / "dsh" / "user1" / "agent-bridge-mcp.patch.yml")
        .read_text(encoding="utf-8")
    )
    newest_personal_token = newest_personal_overlay[0]["insert"][0]["config"]["headers"]["X-Agent-Bridge-DSH-Capability"]
    service.dsh.stop_runtime("user1", scope="shared")
    with pytest.raises(Exception):
        service.dsh.capabilities.require(shared_token)
    service.dsh.capabilities.require(newest_personal_token, profile_key="wide")
    # 共享 overlay 随 runtime 停止移除（capability 已撤销，patch 不留死 token）。
    assert not mcp_overlay_path(home / "groupa" / ".config" / "dsh" / "groupa").exists()


def test_shared_profile_reselect_allowed_after_stop(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")
    assert len(launcher.starts) == 1

    stopped = service.dsh.stop_runtime("user2", scope="shared")
    assert stopped["stopped"] is True
    assert stopped["scope"] == "shared"
    assert stopped["runtime_key"] == "groupa"

    # 共享 Runtime 停止后：下次进入重新允许选择 Profile
    relaunched = service.dsh.authorize_workspace("user2", profile_key="wide", scope="shared")
    assert relaunched["profile_key"] == "wide"
    assert len(launcher.starts) == 2
    state = json.loads(shared_state_path(service).read_text(encoding="utf-8"))
    assert state["profile_key"] == "wide"


def test_shared_without_profile_runs_plain_and_members_enter_without_injection(
    service, home, passwd_lookup, monkeypatch
) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    plain = service.dsh.authorize_workspace("user1", profile_key=None, scope="shared")
    assert plain["profile_key"] is None
    assert len(launcher.starts) == 1
    assert "--patch" not in launcher.starts[0]["command"]

    # 运行中（active profile 为空）：成员即便请求平面也按无注入进入
    again = service.dsh.authorize_workspace("user2", profile_key="safe", scope="shared")
    assert again["profile_key"] is None
    assert len(launcher.starts) == 1


def test_shared_requires_active_profile_permission_for_entering_member(
    service, home, passwd_lookup, monkeypatch
) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)
    service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")

    # 其他组的用户无 groupa 的 safe 平面权限：即使映射到同一 Linux 用户，
    # 也不得以他人 capability 越权进入共享工作台。
    service.access.upsert_group(actor="root", group_key="groupb", name="B 组")
    service.access.create_user(actor="root", user_id="outsider")
    service.access.set_user_group(actor="root", user_id="outsider", group_key="groupb")
    service.dsh_configs.save_group_config(
        "root",
        group_key="groupb",
        linux_user="groupa",  # 映射到同一 Linux 用户的另一小组
        default_model="gpt-x",
        api_key="",
    )
    with pytest.raises(Exception):
        service.dsh.authorize_workspace("outsider", profile_key="safe", scope="shared")
    # 共享 runtime 不受影响
    assert len(launcher.starts) == 1


def test_shared_rejects_unassigned_or_unconfigured(service, monkeypatch) -> None:
    service.access.create_user(actor="root", user_id="lonely")
    with pytest.raises(Exception):
        service.dsh.authorize_workspace("lonely", profile_key=None, scope="shared")

    configure_group(service)
    service.access.upsert_group(actor="root", group_key="groupz", name="Z 组")
    service.access.create_user(actor="root", user_id="zuser")
    service.access.set_user_group(actor="root", user_id="zuser", group_key="groupz")
    with pytest.raises(Exception):
        service.dsh.authorize_workspace("zuser", profile_key=None, scope="shared")


def test_concurrent_shared_entries_start_single_process(
    service, home, passwd_lookup, monkeypatch
) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    results: list[dict] = []
    errors: list[Exception] = []

    def enter(user_id: str, profile: str) -> None:
        try:
            results.append(
                service.dsh.authorize_workspace(user_id, profile_key=profile, scope="shared")
            )
        except Exception as exc:  # pragma: no cover - 并发失败即测试失败
            errors.append(exc)

    threads = [
        threading.Thread(target=enter, args=("user1", "safe")),
        threading.Thread(target=enter, args=("user2", "wide")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(launcher.starts) == 1
    assert all(item["status"] == "running" for item in results)
    # 先拿到服务锁的成员决定 active profile；后到者被锁定到同一平面。
    # 谁先进入不确定，但两名成员必然观察到同一个 active profile。
    assert len(results) == 2
    assert len({item["profile_key"] for item in results}) == 1
    state = json.loads(shared_state_path(service).read_text(encoding="utf-8"))
    assert state["profile_key"] == results[0]["profile_key"]


# -- 状态、保活与空闲回收 --


def test_shared_status_target_and_touch_refresh(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)
    service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")

    # 状态查询按 scope 区分
    shared_status = service.dsh.runtime_status("user2", scope="shared")
    assert shared_status["scope"] == "shared"
    assert shared_status["status"] == "running"
    assert shared_status["linux_user"] == "groupa"
    assert shared_status["profile_key"] == "safe"
    personal_status = service.dsh.runtime_status("user2")
    assert personal_status["status"] == "stopped"

    # 代理目标解析：成员按 Linux 用户命中共享 runtime，个人范围未运行
    target = service.dsh.require_runtime_target("user2", scope="shared")
    assert target is not None and target.endswith(f":{launcher.starts[0]['port']}")
    assert service.dsh.require_runtime_target("user2") is None

    # 任一成员的访问都刷新共享 runtime 的 last_access_at
    state = json.loads(shared_state_path(service).read_text(encoding="utf-8"))
    old_access = float(state["last_access_at"])
    state["last_access_at"] = old_access - 3600
    shared_state_path(service).write_text(json.dumps(state), encoding="utf-8")
    assert service.dsh.require_runtime_target("user1", scope="shared") is not None
    refreshed = json.loads(shared_state_path(service).read_text(encoding="utf-8"))
    assert float(refreshed["last_access_at"]) > old_access - 3600


def test_shared_idle_reaper_stops_shared_runtime(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)
    service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")

    state = json.loads(shared_state_path(service).read_text(encoding="utf-8"))
    state["last_access_at"] = time.time() - 10 * 24 * 60 * 60
    shared_state_path(service).write_text(json.dumps(state), encoding="utf-8")

    stopped = service.dsh.stop_idle_expired()
    assert "groupa" in stopped
    assert not shared_state_path(service).exists()
    # 停止后可重新选择 Profile
    relaunched = service.dsh.authorize_workspace("user1", profile_key="wide", scope="shared")
    assert relaunched["profile_key"] == "wide"


def test_personal_and_shared_runtimes_fully_isolated(service, home, passwd_lookup, monkeypatch) -> None:
    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    service.dsh.authorize_workspace("user1", profile_key="safe")  # personal
    service.dsh.authorize_workspace("user1", profile_key=None, scope="shared")

    assert len(launcher.starts) == 2
    assert service.dsh._state_path("personal", "user1").exists()
    assert shared_state_path(service).exists()
    # 个人平面切换重启个人实例，不影响共享实例
    service.dsh.authorize_workspace("user1", profile_key="wide")
    assert len(launcher.starts) == 3
    shared_state = json.loads(shared_state_path(service).read_text(encoding="utf-8"))
    assert shared_state["pid"] == launcher.starts[1]["pid"]
    assert shared_state["profile_key"] is None


def test_recover_stops_alive_shared_runtime_and_clears_patch(
    service, home, passwd_lookup, monkeypatch
) -> None:
    """Agent Bridge 重启 recover：存活 shared runtime 安全停止并清理 state/patch。"""
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.dsh.service import DshRuntimeService
    from agent_bridge.dsh.workspace import mcp_overlay_path

    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)
    service.dsh.authorize_workspace("user1", profile_key="safe")
    service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")
    shared_config_dir = home / "groupa" / ".config" / "dsh" / "groupa"
    overlay_path = mcp_overlay_path(shared_config_dir)
    assert overlay_path.exists()
    shared_state = json.loads(shared_state_path(service).read_text(encoding="utf-8"))

    # 模拟服务重启：新进程 registry 为空，状态文件与 DSH patch 仍在；旧进程的
    # 两个 DSH 进程仍存活（类级 patch 使 create 内部的 recover 按存活处理）。
    terminated: list[int] = []
    monkeypatch.setattr(DshRuntimeService, "_pid_alive", staticmethod(lambda pid: True))
    monkeypatch.setattr(
        DshRuntimeService,
        "_terminate_pid",
        lambda self, pid, *, grace_seconds: terminated.append(pid) or True,
    )
    restarted = AgentBridgeService.create(service.paths, {"root"})

    # create 内的 recover：shared 被停止并清理 state/patch，个人存活实例保留。
    assert terminated == [shared_state["pid"]]
    assert not shared_state_path(service).exists()
    assert not overlay_path.exists()
    assert restarted.dsh._state_path("personal", "user1").exists()
    # 再次 recover 幂等：shared 已清理、个人仍保留、无死 token 残留。
    assert restarted.dsh.recover() == {"kept": 1, "cleaned": 0, "stopped_shared": 0}

    # 首位成员重新进入：按新选择重新签发 capability 并重建 patch。
    launcher2 = install_fakes(restarted, home, passwd_lookup, monkeypatch)
    relaunched = restarted.dsh.authorize_workspace("user2", profile_key="wide", scope="shared")
    assert relaunched["profile_key"] == "wide"
    assert len(launcher2.starts) == 1
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
    entry = overlay[0]["insert"][0]
    assert entry["config"]["headers"]["X-Agent-Bridge-MetaMCP-Profile"] == "wide"
    token = entry["config"]["headers"]["X-Agent-Bridge-DSH-Capability"]
    assert restarted.dsh.capabilities.require(token, profile_key="wide").user_id == "groupa"


def test_shared_runtime_capability_never_expires_while_runtime_alive(
    service, home, passwd_lookup, monkeypatch
) -> None:
    """共享 capability 绑定 runtime 生命周期：持续活跃远超 24h/30d 也不失效。"""
    configure_group(service)
    install_fakes(service, home, passwd_lookup, monkeypatch)
    service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")

    overlay = yaml.safe_load(
        (home / "groupa" / ".config" / "dsh" / "groupa" / "agent-bridge-mcp.patch.yml")
        .read_text(encoding="utf-8")
    )
    token = overlay[0]["insert"][0]["config"]["headers"]["X-Agent-Bridge-DSH-Capability"]
    capability = service.dsh.capabilities.require(token, profile_key="safe")
    # 不设独立过期（绑定 runtime 生命周期，随停止显式撤销）。
    assert capability.expires_at_monotonic is None

    # 把单调时钟推进 400 天：持续活跃的共享 runtime 依旧可用。
    now = {"monotonic": time.monotonic()}
    monkeypatch.setattr("agent_bridge.dsh.workspace.time.monotonic", lambda: now["monotonic"])
    now["monotonic"] += 400 * 24 * 60 * 60
    assert service.dsh.capabilities.require(token, profile_key="safe").user_id == "groupa"

    # 个人 capability 仍按 24h TTL 过期（不受共享改动影响）。
    service.dsh.authorize_workspace("user1", profile_key="safe")
    personal_overlay = yaml.safe_load(
        (home / "groupa" / ".config" / "dsh" / "user1" / "agent-bridge-mcp.patch.yml")
        .read_text(encoding="utf-8")
    )
    personal_token = personal_overlay[0]["insert"][0]["config"]["headers"]["X-Agent-Bridge-DSH-Capability"]
    now["monotonic"] += 25 * 60 * 60
    with pytest.raises(Exception):
        service.dsh.capabilities.require(personal_token, profile_key="safe")
    # 共享 capability 不因时间流逝失效（仅随 runtime 停止撤销）。
    assert service.dsh.capabilities.require(token, profile_key="safe") is not None


def test_shared_unhealthy_restart_keeps_active_profile_and_mcp(
    service, home, passwd_lookup, monkeypatch
) -> None:
    """shared unhealthy 自动重启：保留 active profile，重签 capability 并重建 patch。"""
    from agent_bridge.dsh.workspace import mcp_overlay_path

    configure_group(service)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)
    service.dsh.authorize_workspace("user1", profile_key="safe", scope="shared")
    first_port = launcher.starts[0]["port"]
    config_dir = home / "groupa" / ".config" / "dsh" / "groupa"
    overlay_path = mcp_overlay_path(config_dir)
    old_token = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))[0]["insert"][0]["config"]["headers"][
        "X-Agent-Bridge-DSH-Capability"
    ]

    # 进程存活但端口不健康，且已超过启动探测窗口 → 判定 unhealthy 并回收重启。
    # 重启可能复用同一端口，探针按“首个进程”判定：重启出第二个进程后即健康。
    state_path = shared_state_path(service)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["started_at"] = time.time() - 3600
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        service.dsh,
        "_probe_port",
        staticmethod(lambda port: not (port == first_port and len(launcher.starts) == 1)),
    )

    # 后续成员进入触发 ensure_running；重启后必须仍是 safe 平面且 MCP 可用。
    result = service.dsh.authorize_workspace("user2", profile_key="wide", scope="shared")
    assert result["profile_key"] == "safe"
    assert result["status"] == "running"
    assert len(launcher.starts) == 2
    restarted_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert restarted_state["pid"] == launcher.starts[1]["pid"]
    assert restarted_state["profile_key"] == "safe"
    # patch 重建为新 capability（旧 token 已随回收撤销）。
    new_token = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))[0]["insert"][0]["config"][
        "headers"
    ]["X-Agent-Bridge-DSH-Capability"]
    assert new_token != old_token
    with pytest.raises(Exception):
        service.dsh.capabilities.require(old_token, profile_key="safe")
    assert service.dsh.capabilities.require(new_token, profile_key="safe").user_id == "groupa"


# -- 代理路由 --


def test_proxy_route_matching_and_location_rewrite() -> None:
    from agent_bridge.api.workspace_proxy import (
        match_workspace_route,
        rewrite_workspace_location,
    )

    assert match_workspace_route("/agent-workspace") == ("personal", "/")
    assert match_workspace_route("/agent-workspace/ws") == ("personal", "/ws")
    assert match_workspace_route("/agent-workspace-shared") == ("shared", "/")
    assert match_workspace_route("/agent-workspace-shared/a/b") == ("shared", "/a/b")
    assert match_workspace_route("/agent-workspace-other") is None
    assert match_workspace_route("/api/v1/dsh/runtime") is None

    target = "http://127.0.0.1:48400"
    assert (
        rewrite_workspace_location("/x", target=target, prefix="/agent-workspace-shared")
        == "/agent-workspace-shared/x"
    )
    assert (
        rewrite_workspace_location(
            "http://127.0.0.1:48400/x?y=1", target=target, prefix="/agent-workspace-shared"
        )
        == "/agent-workspace-shared/x?y=1"
    )


def test_proxy_escape_claims_by_referer_scope() -> None:
    from agent_bridge.api.workspace_proxy import AgentWorkspaceProxyMiddleware

    middleware = AgentWorkspaceProxyMiddleware(app=None, service=None, identity_resolver=None)

    def http_scope(path: str, referer: str | None) -> dict:
        raw_headers = [(b"host", b"example.internal")]
        if referer is not None:
            raw_headers.append((b"referer", referer.encode("utf-8")))
        return {"type": "http", "path": path, "headers": raw_headers}

    def resolve(path: str, referer: str | None, user: str = "user1") -> tuple[str, str] | None:
        return middleware._resolve_escape_route(http_scope(path, referer), user)

    # 个人前缀 Referer → personal
    assert resolve("/api/chat", "http://example.internal/agent-workspace/") == ("personal", "/api/chat")
    # 共享前缀 Referer → shared
    assert resolve("/api/chat", "http://example.internal/agent-workspace-shared/x") == ("shared", "/api/chat")
    # 嵌套文档（无前缀 Referer）：先由工作台页面首次进入（Referer 带前缀），
    # 其子资源再按“该用户最近认领该路径的 scope”继续归属 shared
    assert resolve("/studio/", "http://example.internal/agent-workspace-shared/x") == ("shared", "/studio/")
    assert resolve("/studio/page", "http://example.internal/studio/") == ("shared", "/studio/page")
    assert resolve("/studio/page/asset.css", "http://example.internal/studio/page") == ("shared", "/studio/page/asset.css")

    # 不同用户相同路径互不串 scope：user2 没有 user1 的认领记忆，回落 personal。
    assert resolve("/studio/page2", "http://example.internal/studio/", user="user2") == ("personal", "/studio/page2")
    # user2 随后用自己的个人工作台认领同路径，不影响 user1 的 shared 记忆。
    assert resolve("/studio/", "http://example.internal/agent-workspace/", user="user2") == ("personal", "/studio/")
    assert resolve("/studio/page", "http://example.internal/studio/") == ("shared", "/studio/page")
    assert resolve("/studio/page", "http://example.internal/studio/", user="user2") == ("personal", "/studio/page")
    # 同一用户重复认领按“最近认领”更新（文档化的多 tab 已知限制）。
    assert resolve("/studio/", "http://example.internal/agent-workspace-shared/x") == ("shared", "/studio/")
    assert resolve("/studio/page2", "http://example.internal/studio/") == ("shared", "/studio/page2")

    # 无前缀且无记忆的 Referer 回落 personal
    middleware._claimed_scopes.clear()
    assert resolve("/assets/app.js", "http://example.internal/other-page/") == ("personal", "/assets/app.js")
    # 保留前缀永不认领
    assert resolve("/api/v1/users", "http://example.internal/agent-workspace/") is None


# -- 插件状态按 DSH_HOME 隔离（issue #14 验收） --


def test_plugin_fingerprint_state_isolated_per_dsh_home(
    service, home, passwd_lookup, monkeypatch, tmp_path
) -> None:
    from agent_bridge.dsh import plugins

    configure_group(service)
    list_path = tmp_path / "dsh-plugins.txt"
    list_path.write_text("dsh-context\n", encoding="utf-8")
    monkeypatch.setattr(plugins, "plugin_list_path", lambda: list_path)
    launcher = install_fakes(service, home, passwd_lookup, monkeypatch)

    service.dsh.authorize_workspace("user1", profile_key=None)
    service.dsh.authorize_workspace("user1", profile_key=None, scope="shared")

    personal_state = plugins.plugin_state_path(home / "groupa" / ".config" / "dsh" / "user1")
    shared_state = plugins.plugin_state_path(home / "groupa" / ".config" / "dsh" / "groupa")
    assert personal_state.exists()
    assert shared_state.exists()
    # 各自独立的状态文件，内容互不影响
    personal_payload = json.loads(personal_state.read_text(encoding="utf-8"))
    shared_payload = json.loads(shared_state.read_text(encoding="utf-8"))
    assert personal_payload["fingerprint"] == shared_payload["fingerprint"]
    assert personal_payload["plugins"] == ["dsh-context"]

    # 删除个人状态：只有个人 DSH_HOME 触发重新校验，共享不受影响
    personal_state.unlink()
    service.dsh.stop_runtime("user1")
    service.dsh.authorize_workspace("user1", profile_key=None)
    assert personal_state.exists()
    assert json.loads(shared_state.read_text(encoding="utf-8"))["installed_at"] == shared_payload["installed_at"]


# -- API 流程 --


def test_shared_workspace_api_flow(service, home, passwd_lookup, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from agent_bridge.api.app import create_app

    configure_group(service)
    client = TestClient(create_app(service.paths, {"root"}))
    # create_app 会基于同一数据目录装配新的 service；进程类 fakes 需装在 app 实例上
    app_service = client.app.state.agent_bridge_service
    app_service.dsh._launcher = FakeLauncher()
    app_service.dsh._passwd_lookup = passwd_lookup
    app_launcher = app_service.dsh._launcher
    monkeypatch.setattr(
        app_service.dsh,
        "_pid_alive",
        staticmethod(lambda pid: any(start["pid"] == pid for start in app_launcher.starts)),
    )
    monkeypatch.setattr(
        app_service.dsh,
        "_probe_port",
        staticmethod(lambda port: any(int(start["port"]) == port for start in app_launcher.starts)),
    )
    headers_user1 = {"X-Agent-Bridge-User": "user1"}
    headers_user2 = {"X-Agent-Bridge-User": "user2"}

    status = client.get("/api/v1/dsh/runtime?scope=shared", headers=headers_user1).json()
    assert status["scope"] == "shared"
    assert status["status"] == "stopped"
    assert status["linux_user"] == "groupa"

    authorized = client.post(
        "/api/v1/dsh/workspace/authorize",
        headers=headers_user1,
        json={"profile_key": "safe", "scope": "shared"},
    ).json()
    assert authorized["status"] == "running"
    assert authorized["workspace_url"] == "/agent-workspace-shared/"

    # 第二名成员查看共享状态：运行中且 active profile 可见
    shared_status = client.get("/api/v1/dsh/runtime?scope=shared", headers=headers_user2).json()
    assert shared_status["status"] == "running"
    assert shared_status["profile_key"] == "safe"

    stopped = client.post("/api/v1/dsh/runtime/stop?scope=shared", headers=headers_user2).json()
    assert stopped["stopped"] is True
    assert stopped["scope"] == "shared"

    # 非法 scope 被拒绝
    bad = client.post(
        "/api/v1/dsh/workspace/authorize",
        headers=headers_user1,
        json={"profile_key": None, "scope": "team"},
    )
    assert bad.status_code == 422
