"""个人终端的 PTY、鉴权与边界回归。"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent_bridge.agent_workspace.terminal_server import create_terminal_app


@pytest.fixture
def terminal_app():
    command = [sys.executable, "-u", "-c", "import os,sys; print('READY:'+str(sys.stdin.isatty()),flush=True); "
               "[(print('ECHO:'+line.strip(),flush=True)) for line in sys.stdin]"]
    return create_terminal_app(command, "test-secret")


def receive_until(socket, expected):
    collected = b""
    for _ in range(20):
        collected += socket.receive_bytes()
        if expected in collected:
            return collected
    raise AssertionError(collected)


def collect_messages(socket):
    """读到关闭帧为止，返回全部消息与关闭帧。"""
    messages = []
    while True:
        message = socket.receive()
        messages.append(message)
        if message['type'] == 'websocket.close':
            return messages, message


def test_agent_exit_sends_exit_frame_and_terminal_close_code():
    """Agent 退出必须给出结束码；结束后重连也不能挂回死会话。"""
    app = create_terminal_app([sys.executable, "-u", "-c", "import time; time.sleep(0.2)"], "s")
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer s'}
        with client.websocket_connect('/terminal', headers=headers) as socket:
            messages, closed = collect_messages(socket)
        assert closed['code'] == 4000
        assert any(m['type'] == 'websocket.send' and 'exit' in str(m.get('text')) for m in messages)
        # 已结束的会话再次连接：立刻回结束帧与 4000，不进入重连循环。
        with client.websocket_connect('/terminal', headers=headers) as again:
            _, closed_again = collect_messages(again)
        assert closed_again['code'] == 4000


@pytest.mark.asyncio
async def test_write_input_after_agent_exit_marks_session_finished():
    """Agent 退出后写 PTY 遇到 EIO：标记会话结束，而不是抛出未处理的 OSError。"""
    import time
    from agent_bridge.agent_workspace.terminal_server import PtySession
    session = PtySession([sys.executable, '-c', 'pass'])
    session.start()
    try:
        deadline = time.monotonic() + 5
        while session.process.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert session.process.poll() is not None
        await session.write_input(b'hello')
        assert session.finished.is_set()
    finally:
        await session.stop()


def test_pty_tty_input_exclusivity_and_reconnect(terminal_app):
    with TestClient(terminal_app) as client:
        assert client.get('/health').status_code == 401
        assert client.get('/health', headers={'Authorization': 'Bearer test-secret'}).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/terminal'):
                pass
        headers = {'Authorization': 'Bearer test-secret'}
        with client.websocket_connect('/terminal', headers=headers) as first:
            receive_until(first, b'READY:True')
            with client.websocket_connect('/terminal', headers=headers) as second:
                with pytest.raises(WebSocketDisconnect) as error:
                    second.receive_bytes()
                assert error.value.code == 4009
            first.send_json({'type': 'resize', 'cols': 100, 'rows': 30})
            first.send_json({'type': 'input', 'data': '中文\n'})
            receive_until(first, 'ECHO:中文'.encode())
        # 断线不结束 Agent；刷新后重放原会话，不另起子进程。
        pid = terminal_app.state.session.process.pid
        with client.websocket_connect('/terminal', headers=headers) as reconnected:
            receive_until(reconnected, 'ECHO:中文'.encode())
            assert terminal_app.state.session.process.pid == pid
            reconnected.send_json({'type': 'input', 'data': 'again\n'})
            receive_until(reconnected, b'ECHO:again')
    assert terminal_app.state.session.process.poll() is not None


@pytest.mark.parametrize('payload', [
    {'type': 'resize', 'cols': 0, 'rows': 30},
    {'type': 'resize', 'cols': True, 'rows': 30},
    {'type': 'input', 'data': 'a' * 65537},
    {'type': 'command', 'data': '/bin/sh'},
    [],
])
def test_invalid_messages_close_connection(terminal_app, payload):
    with TestClient(terminal_app) as client:
        with client.websocket_connect('/terminal', headers={'Authorization': 'Bearer test-secret'}) as socket:
            receive_until(socket, b'READY:True')
            socket.send_json(payload)
            with pytest.raises(WebSocketDisconnect) as error:
                while True:
                    socket.receive_bytes()
            assert error.value.code == 1008


def test_terminal_registry_scope_and_capability_isolation(wm_paths):
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.core.domain import ValidationError
    service = AgentBridgeService.create(wm_paths, {'root'})
    assert set(service.workspace_terminals) == {'dsh', 'pi'}
    marks = [runtime.adapter.mark_env for runtime in service.workspace_runtimes.values()]
    assert len(marks) == len(set(marks))
    terminal = service.workspace_runtime('dsh', mode='terminal')
    assert not terminal.adapter.supports_scope('shared')
    with pytest.raises(ValidationError):
        terminal.ensure_running('user', scope='shared')
    with pytest.raises(ValidationError):
        service.workspace_runtime('dsh', mode='unknown')
    capability = terminal.capabilities.issue(user_id='user', profile_key='p', owner_group_key='g')
    assert service.require_workspace_capability(capability.token).profile_key == 'p'
    assert service.dsh.capabilities.current_for_user('user') is None


def test_tui_lock_is_pinned_and_separate_from_web():
    from agent_bridge.agent_workspace.terminal_adapter import TUI_PLUGIN_LIST
    from agent_bridge.dsh import plugins
    import re
    specs = plugins.parse_plugin_list(TUI_PLUGIN_LIST.read_text())
    assert specs == ['@deepseek-harness-tui/dsh-tui@0.16.1']
    assert all(re.fullmatch(r'.+@\d+\.\d+\.\d+(?:-[\w.]+)?', spec) for spec in specs)
    assert not set(specs) & set(plugins.read_plugin_list())
    assert plugins.build_install_command('dsh', specs[0], profile='tui')[3] == 'tui'


def test_websocket_proxy_rejects_cross_origin_and_missing_identity():
    from fastapi import FastAPI
    from agent_bridge.api.routes.workspace_terminal import create_terminal_routes
    from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver
    app = FastAPI()
    # 校验失败必须在查询任何 runtime 之前结束。
    app.include_router(create_terminal_routes(object(), RequestIdentityResolver(IdentityConfig())))
    with TestClient(app) as client:
        for headers in ({}, {'origin': 'https://evil.example'}, {'origin': 'http://testserver'}):
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect('/agent-workspace/terminal/pi', headers=headers):
                    pass
            assert error.value.code == 1008


@pytest.mark.asyncio
async def test_disconnect_grace_stops_process_and_output_replay_is_bounded(monkeypatch):
    from agent_bridge.agent_workspace.terminal_server import PtySession, REPLAY_BYTES
    app = create_terminal_app([sys.executable, '-c', 'import time;time.sleep(30)'], 's', grace_seconds=0)
    from fastapi.testclient import TestClient
    # 无连接时超过宽限后启动 monitor 收敛；退出 lifespan 必须确认子进程已退出。
    with TestClient(app):
        import time
        deadline = time.monotonic() + 3
        while not app.state.session.finished.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert app.state.session.finished.is_set()
    assert app.state.session.process.poll() is not None
    session = PtySession([])
    monkeypatch.setattr(os, 'read', lambda *_: b'x' * 16384)
    for _ in range(REPLAY_BYTES // 16384 + 50):
        session.read_output()
    assert session.output_bytes <= REPLAY_BYTES
    assert sum(map(len, session.output)) == session.output_bytes


def test_terminal_close_code_mapping():
    """链路异常归一到 1011，业务关闭码原样保留，前端才能区分结束与抖动。"""
    from types import SimpleNamespace
    from agent_bridge.api.routes.workspace_terminal import upstream_close_code
    cases = {4000: 4000, 4001: 4001, 4009: 4009, 1013: 1013,
             1000: 1000, 1005: 1011, 1006: 1011, 1015: 1011, None: 1011}
    for code, expected in cases.items():
        assert upstream_close_code(SimpleNamespace(close_code=code)) == expected


def test_terminal_proxy_reports_ended_session_with_business_code():
    """终端已结束不能被报成登录失效：代理用 4001 让前端提示关闭页面。"""
    from fastapi import FastAPI
    from types import SimpleNamespace
    from agent_bridge.core.domain import AccessDenied
    from agent_bridge.api.routes.workspace_terminal import create_terminal_routes
    from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver

    def unavailable(*_args, **_kwargs):
        raise AccessDenied('终端进程已结束')

    runtime = SimpleNamespace(terminal_target=unavailable)
    service = SimpleNamespace(workspace_runtime=lambda *_args, **_kwargs: runtime)
    app = FastAPI()
    app.include_router(create_terminal_routes(service, RequestIdentityResolver(IdentityConfig())))
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect('/agent-workspace/terminal/pi', headers={
                'origin': 'http://testserver', 'X-Agent-Bridge-User': 'user',
            }):
                pass
        assert error.value.code == 4001


def test_terminal_proxy_preserves_exclusive_close_code(monkeypatch):
    """代理层必须透传连接独占的业务关闭码，前端才能明确提示。"""
    from fastapi import FastAPI
    from types import SimpleNamespace
    from agent_bridge.api.routes.workspace_terminal import create_terminal_routes
    from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver
    import agent_bridge.api.routes.workspace_terminal as module

    class Upstream:
        close_code = 4009
        close_reason = '该终端已在其他页面连接'
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    runtime = SimpleNamespace(terminal_target=lambda *_args, **_kwargs: ('http://127.0.0.1:1', 's'))
    service = SimpleNamespace(workspace_runtime=lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(module, 'connect', lambda *_args, **_kwargs: Upstream())
    app = FastAPI()
    app.include_router(create_terminal_routes(service, RequestIdentityResolver(IdentityConfig())))
    with TestClient(app) as client:
        with client.websocket_connect('/agent-workspace/terminal/pi', headers={
            'origin': 'http://testserver', 'X-Agent-Bridge-User': 'user',
        }) as socket:
            with pytest.raises(WebSocketDisconnect) as error:
                socket.receive_bytes()
            assert error.value.code == 4009


@pytest.mark.asyncio
@pytest.mark.parametrize('parent_exits', [
    False,
    pytest.param(True, marks=pytest.mark.skipif(sys.platform != 'linux', reason='孤儿环境标记扫描依赖 /proc')),
])
async def test_terminal_stop_reaps_detached_children_and_keeps_other_sessions(tmp_path, parent_exits):
    """主 Agent 存活/先退出时，均回收拒绝 SIGTERM 的 detached 工具进程。"""
    import signal
    import subprocess
    import time
    from agent_bridge.agent_workspace import process_tree
    from agent_bridge.agent_workspace.terminal_server import PtySession, SESSION_MARK_ENV

    pid_file = tmp_path / 'tool.pid'
    child_script = (
        'import os,signal,time; from pathlib import Path; '
        'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        f'Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)'
    )
    command = (
        'import subprocess,sys,time; '
        f'subprocess.Popen([sys.executable,"-c",{child_script!r}], start_new_session=True); '
        + ('time.sleep(0.3)' if parent_exits else 'time.sleep(60)')
    )
    session = PtySession([sys.executable, '-u', '-c', command])
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                             start_new_session=True, env={**os.environ, SESSION_MARK_ENV: 'other-session'})
    child_pid = None
    try:
        session.start()
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert pid_file.exists(), 'detached 工具进程未就绪'
        child_pid = int(pid_file.read_text())
        if parent_exits:
            while session.process.poll() is None and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert session.process.poll() == 0
        await session.stop()
        assert session.process.poll() is not None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            if process_tree.process_is_zombie(child_pid):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail('detached 工具进程在终端停止后仍存活')
        assert other.poll() is None, '其他终端会话不能被回收'
    finally:
        if session.master >= 0:
            await session.stop()
        for pid in [child_pid, other.pid]:
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        other.wait(timeout=5)


def test_cleanup_before_setsid_never_signals_bridge_process_group(monkeypatch):
    """PTY helper 尚未完成 setsid 就取消时，bridge 所在进程组不能被误杀。"""
    import signal
    from agent_bridge.agent_workspace.process_cleanup import RuntimeProcessController
    signaled = []
    monkeypatch.setattr(os, 'getpgid', lambda _pid: os.getpgrp())
    monkeypatch.setattr(os, 'killpg', lambda *_args: pytest.fail('不能向 bridge 自己的进程组发信号'))
    monkeypatch.setattr(os, 'kill', lambda pid, sig: signaled.append((pid, sig)))
    RuntimeProcessController._signal_process(12345, signal.SIGTERM)
    assert signaled == [(12345, signal.SIGTERM)]
