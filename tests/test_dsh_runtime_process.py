"""DSH Web Runtime 真实进程 smoke test。

用 ``python -m http.server`` 充当 DSH Web 进程（监听 127.0.0.1 动态端口、
对 ``/`` 返回 HTTP 响应），验证启动、复用、uid、停止与配置目录保留的完整
生命周期语义。仅在 ``./scripts/test.sh all``（``-m process``）时执行。
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.process


@pytest.fixture
def dsh_service(wm_paths, tmp_path):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.access.upsert_group(actor="root", group_key="groupa", name="A 组")
    svc.access.create_user(actor="root", user_id="user1")
    svc.access.create_user(actor="root", user_id="user2")
    svc.access.set_user_group(actor="root", user_id="user1", group_key="groupa")
    svc.access.set_user_group(actor="root", user_id="user2", group_key="groupa")

    # 测试不能写真实 home：用假 passwd 条目指向临时目录，uid/gid 仍为当前用户
    linux_home = tmp_path / "linux-home"
    linux_home.mkdir()
    current_user = getpass.getuser()
    svc.dsh._passwd_lookup = lambda user: types.SimpleNamespace(
        pw_uid=os.getuid(), pw_gid=os.getgid(), pw_dir=str(linux_home / user)
    )

    svc.dsh_configs.save_runtime_config(
        "root",
        web_command=f'"{sys.executable}" -m http.server {{port}} --bind 127.0.0.1',
        idle_timeout_minutes=120,
    )
    svc.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        linux_user=current_user,
        base_url="http://model.internal/v1",
        default_model="gpt-x",
        available_models=["gpt-x", "gpt-y"],
        api_key="sk-secret",
    )
    yield svc
    svc.dsh.stop_all()


def _process_uid(pid: int) -> int:
    completed = subprocess.run(
        ["ps", "-o", "uid=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return int(completed.stdout.strip())


def test_real_process_lifecycle(dsh_service, tmp_path) -> None:
    service = dsh_service
    status = service.dsh.ensure_running("user1")
    assert status["status"] == "running"
    assert "port" not in status

    state = service.dsh._read_state("user1")
    assert state is not None
    pid = int(state["pid"])
    port = int(state["port"])
    assert port >= 48400

    # 进程以目标 Linux 用户的 uid 运行（非 root 环境下等于当前用户）
    expected_uid = os.getuid() if os.geteuid() != 0 else int(state.get("uid") or os.getuid())
    assert _process_uid(pid) == expected_uid

    # 用户级配置目录位于 Linux 用户 home 下，与 Agent Bridge data 隔离
    config_dir = Path(str(state["config_dir"]))
    current_user = getpass.getuser()
    assert config_dir == Path(str(service.dsh._passwd_lookup(current_user).pw_dir)) / ".config" / "dsh" / "user1"
    assert config_dir.is_dir()
    assert not (service.paths.data_dir / "dsh").exists()

    # 环境注入写进启动日志可被进程观测：通过 http.server 无法直接读取 env，
    # 这里验证 state 与配置一致即可；env 注入由单元测试覆盖。
    import httpx

    response = httpx.get(f"http://127.0.0.1:{port}/", timeout=2.0)
    assert response.status_code == 200

    # 重复进入复用同一实例
    again = service.dsh.ensure_running("user1")
    assert again["status"] == "running"
    assert service.dsh._read_state("user1")["pid"] == pid

    # 同 group 另一业务用户：独立目录、独立实例
    second = service.dsh.ensure_running("user2")
    assert second["status"] == "running"
    state2 = service.dsh._read_state("user2")
    assert state2["pid"] != pid
    assert Path(str(state2["config_dir"])).name == "user2"

    # 代理目标解析
    assert service.dsh.require_runtime_target("user1") == f"http://127.0.0.1:{port}"

    # 停止：进程退出、状态清理、配置目录保留
    (config_dir / "session.json").write_text("{}", encoding="utf-8")
    stopped = service.dsh.stop_runtime("user1")
    assert stopped["stopped"] is True
    assert service.dsh._read_state("user1") is None
    assert (config_dir / "session.json").exists()

    deadline_port_free = service.dsh._probe_port(port)
    assert deadline_port_free is False

    # 重启后能恢复状态：模拟遗留 state（进程仍存活）→ recover 保留
    service.dsh.ensure_running("user2")
    result = service.dsh.recover()
    assert result["kept"] >= 1


def test_real_process_failure_reports_log_tail(dsh_service) -> None:
    service = dsh_service
    # 命令立即退出：http.server 带非法参数
    service.dsh_configs.save_runtime_config(
        "root",
        web_command=f'"{sys.executable}" -m http.server --definitely-invalid-flag {{port}}',
        idle_timeout_minutes=120,
    )
    with pytest.raises(Exception) as exc_info:
        service.dsh.ensure_running("user1")
    assert "exit" in str(exc_info.value) or "未就绪" in str(exc_info.value)
    assert service.dsh._read_state("user1") is None
