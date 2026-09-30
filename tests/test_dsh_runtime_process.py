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
import time
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.process


@pytest.fixture
def dsh_service(wm_paths, tmp_path, monkeypatch):
    from agent_bridge.app.service import AgentBridgeService

    # 端口池是 check-then-bind 竞态：xdist 多 worker 并行时同抢 48400 会
    # 互相干扰（探活命中对方进程）。按 worker 分配互不重叠的 50 端口段。
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    worker_index = int("".join(ch for ch in worker if ch.isdigit()) or 0)
    monkeypatch.setattr(
        "agent_bridge.dsh.service.DSH_PORT_BASE",
        48400 + worker_index * 50,
    )

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
    # 替身命令不是 dsh：固定空插件名单，避免真实安装命令被反复执行
    monkeypatch.setattr("agent_bridge.dsh.plugins.read_plugin_list", lambda: [])

    svc.dsh_configs.save_runtime_config(
        "root",
        web_command=f'"{sys.executable}" -m http.server {{patch}} {{port}} --bind 127.0.0.1',
        idle_timeout_minutes=120,
        base_url="http://model.internal/v1",
        available_models=["gpt-x", "gpt-y"],
    )
    svc.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        linux_user=current_user,
        default_model="gpt-x",
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

    state = service.dsh._read_state("personal", "user1")
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

    # DSH 原生 settings.yaml 已按公共接入 + 组级默认模型写入
    settings_text = (config_dir / "settings.yaml").read_text(encoding="utf-8")
    assert "agent-bridge" in settings_text
    assert "http://model.internal/v1" in settings_text
    assert "AGENT_BRIDGE_DSH_API_KEY" in settings_text
    # 替身进程未打印 `dsh web:` 横幅，鉴权入口缺失应被容忍（真实 DSH 由 #3 的 smoke 覆盖）
    assert service.dsh.workspace_auth("user1") is None

    import httpx

    response = httpx.get(f"http://127.0.0.1:{port}/", timeout=2.0)
    assert response.status_code == 200

    # 重复进入复用同一实例
    again = service.dsh.ensure_running("user1")
    assert again["status"] == "running"
    assert service.dsh._read_state("personal", "user1")["pid"] == pid

    # 同 group 另一业务用户：独立目录、独立实例
    second = service.dsh.ensure_running("user2")
    assert second["status"] == "running"
    state2 = service.dsh._read_state("personal", "user2")
    assert state2["pid"] != pid
    assert Path(str(state2["config_dir"])).name == "user2"

    # 代理目标解析
    assert service.dsh.require_runtime_target("user1") == f"http://127.0.0.1:{port}"

    # 停止：进程退出、状态清理、配置目录保留
    (config_dir / "session.json").write_text("{}", encoding="utf-8")
    stopped = service.dsh.stop_runtime("user1")
    assert stopped["stopped"] is True
    assert service.dsh._read_state("personal", "user1") is None
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
        base_url="",
        available_models=[],
    )
    with pytest.raises(Exception) as exc_info:
        service.dsh.ensure_running("user1")
    assert "exit" in str(exc_info.value) or "未就绪" in str(exc_info.value)
    assert service.dsh._read_state("personal", "user1") is None


# 替身 web 进程：模拟 DSH 的 dsh-subprocess-local 行为——启动若干
# ``detached``（独立进程组/会话）的子进程后开始服务 HTTP。这些子进程
# 不在主进程的进程组里，对主进程组发的信号不可达；父链保持完整。
_DETACHED_CHILD_STANDIN = """
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int([arg for arg in sys.argv[1:] if arg.isdigit()][-1])
report_path = os.environ["DETACHED_CHILD_REPORT"]
children = [
    subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        start_new_session=True,
    )
    for _ in range(3)
]
with open(report_path, "w", encoding="utf-8") as report:
    report.write("\\n".join(str(child.pid) for child in children))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_until_gone(pids: list[int], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_pid_alive(pid) for pid in pids):
            return True
        time.sleep(0.1)
    return not any(_pid_alive(pid) for pid in pids)


def test_real_process_stop_reaps_detached_children(dsh_service, tmp_path, monkeypatch) -> None:
    """停止 runtime 必须回收 detached 子进程（独立进程组，主进程组信号不可达）。

    回归背景：DSH 的 dsh-subprocess-local 以 ``detached: true`` 启动全部
    子进程，旧实现只对 ``dsh web`` 主进程组发 SIGTERM/SIGKILL，子进程
    全部遗留。修复后按「主进程 + 后代进程树」完整集合回收。
    """
    service = dsh_service
    standin = tmp_path / "detached_web_standin.py"
    standin.write_text(_DETACHED_CHILD_STANDIN, encoding="utf-8")
    report_path = tmp_path / "detached-children.txt"
    monkeypatch.setenv("DETACHED_CHILD_REPORT", str(report_path))

    service.dsh_configs.save_runtime_config(
        "root",
        web_command=f'"{sys.executable}" "{standin}" {{patch}} {{port}}',
        idle_timeout_minutes=120,
        base_url="http://model.internal/v1",
        available_models=["gpt-x"],
    )

    service.dsh.ensure_running("user1")
    state = service.dsh._read_state("personal", "user1")
    assert state is not None
    root_pid = int(state["pid"])
    assert _pid_alive(root_pid)

    # 替身进程已把 detached 子进程 pid 写入报告文件
    for _ in range(50):
        if report_path.exists():
            break
        time.sleep(0.1)
    child_pids = [int(line) for line in report_path.read_text(encoding="utf-8").split() if line]
    assert len(child_pids) == 3
    assert all(_pid_alive(pid) for pid in child_pids)
    # detached 子进程确实脱离主进程进程组（独立会话）
    assert all(os.getpgid(pid) != os.getpgid(root_pid) for pid in child_pids)

    stopped = service.dsh.stop_runtime("user1")
    assert stopped["stopped"] is True

    # 主进程与全部 detached 子进程都被回收
    assert not _pid_alive(root_pid)
    assert _wait_until_gone(child_pids), f"detached 子进程未被回收：{child_pids}"
