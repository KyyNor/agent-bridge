"""终端目录选择、降权文件操作与目录逃逸防护。"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from agent_bridge.agent_workspace.directory import snapshot_directory
from agent_bridge.agent_workspace.file_worker import execute, upload_file
from agent_bridge.agent_workspace.files import WorkspaceFileService
from agent_bridge.agent_workspace.launcher import LinuxIdentity
from agent_bridge.core.domain import AgentBridgeError, ConflictError, ValidationError


@pytest.fixture
def directory(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / '项目').mkdir()
    (root / 'hello.txt').write_text('你好，终端')
    return root


@pytest.fixture
def identity(tmp_path):
    return LinuxIdentity('test-user', os.geteuid(), os.getegid(), tmp_path)


def test_real_user_helper_roundtrips_binary_files(directory, identity):
    service = WorkspaceFileService()
    browsed = service.browse(identity, str(directory))
    assert browsed['root'] == str(directory.resolve())
    assert [entry['name'] for entry in browsed['entries']] == ['项目']
    root = snapshot_directory(str(directory))
    body = b'\x00\xff\r\nhello'
    assert service.selected(identity, root, 'upload', path='项目', name='数据.bin', data=body)['size'] == len(body)
    assert service.selected(identity, root, 'download', path='项目/数据.bin') == body
    listed = service.selected(identity, root, 'list', path='项目')
    assert listed['entries'] == [{'name': '数据.bin', 'kind': 'file', 'size': len(body)}]
    assert os.stat(directory / '项目/数据.bin').st_mode & 0o777 == 0o600
    with pytest.raises(ConflictError):
        service.selected(identity, root, 'upload', path='项目', name='数据.bin', data=b'replace')
    assert (directory / '项目/数据.bin').read_bytes() == body
    assert not list((directory / '项目').glob('.agent-bridge-upload-*'))


@pytest.mark.parametrize('path', ['../secret', '/etc/passwd', '项目/../../secret', '项目\\secret'])
def test_download_rejects_escaping_paths(directory, path):
    with pytest.raises((ValueError, OSError)):
        execute({'action': 'download', 'root': snapshot_directory(str(directory)).payload(), 'path': path})


def test_symlink_files_directories_and_replaced_root_are_rejected(directory, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret').write_text('outside')
    (directory / 'linked-file').symlink_to(outside / 'secret')
    (directory / 'linked-dir').symlink_to(outside, target_is_directory=True)
    root = snapshot_directory(str(directory))
    for action, path in [('download', 'linked-file'), ('list', 'linked-dir')]:
        with pytest.raises(OSError):
            execute({'action': action, 'root': root.payload(), 'path': path})
    with pytest.raises(OSError):
        upload_file(root, 'linked-dir', 'new', b'no')
    with pytest.raises(FileExistsError):
        upload_file(root, '', 'linked-file', b'no')
    directory.rename(tmp_path / 'original')
    directory.mkdir()
    with pytest.raises(ValueError, match='替换'):
        execute({'action': 'list', 'root': root.payload(), 'path': ''})
    assert (outside / 'secret').read_text() == 'outside'
    assert not (outside / 'new').exists()


def test_special_file_and_transfer_limit_are_rejected(directory, monkeypatch):
    import agent_bridge.agent_workspace.file_worker as worker
    os.mkfifo(directory / 'pipe')
    root = snapshot_directory(str(directory))
    with pytest.raises(ValueError, match='普通文件'):
        execute({'action': 'download', 'root': root.payload(), 'path': 'pipe'})
    monkeypatch.setattr(worker, 'MAX_FILE_BYTES', 4)
    with pytest.raises(ValueError, match='32 MiB'):
        upload_file(root, '', 'too-big', b'12345')
    assert not (directory / 'too-big').exists()
    with pytest.raises(ValueError, match='32 MiB'):
        execute({'action': 'download', 'root': root.payload(), 'path': 'hello.txt'})


def terminal_client(runtime):
    from agent_bridge.access_control.identity import IdentityConfig, RequestIdentityResolver
    from agent_bridge.api.routes.workspace_terminal import create_terminal_routes
    app = FastAPI()
    @app.exception_handler(AgentBridgeError)
    def domain_error(_request, exc):
        return JSONResponse({'detail': exc.message}, status_code=exc.status_code)
    def select(agent, *, mode):
        assert agent == 'pi' and mode == 'terminal'
        return runtime
    app.include_router(create_terminal_routes(SimpleNamespace(workspace_runtime=select),
                                             RequestIdentityResolver(IdentityConfig())))
    return TestClient(app)


def test_http_directory_selection_is_read_only_and_start_is_explicit():
    started = []
    runtime = SimpleNamespace(
        browse_directories=lambda user, path: {'root': path or '/home/zhangsan', 'entries': []},
        start_in_directory=lambda user, **kw: started.append((user, kw)) or {'status': 'running'},
    )
    headers = {'X-Agent-Bridge-User': 'user'}
    with terminal_client(runtime) as client:
        assert client.get('/agent-workspace/terminal/pi/directories').status_code == 401
        result = client.get('/agent-workspace/terminal/pi/directories', headers=headers)
        assert result.json()['root'] == '/home/zhangsan'
        assert not started
        assert client.post('/agent-workspace/terminal/pi/start', headers={**headers, 'Origin': 'https://evil.example'},
                           json={'directory': '/home/zhangsan'}).status_code == 403
        assert not started
        result = client.post('/agent-workspace/terminal/pi/start', headers=headers,
                             json={'directory': '/home/zhangsan/work', 'profile_key': 'safe'})
        assert result.status_code == 200
        assert started == [('user', {'directory': '/home/zhangsan/work', 'profile_key': 'safe'})]


def test_http_upload_download_are_scoped_and_bounded(monkeypatch):
    import agent_bridge.api.routes.workspace_terminal as module
    calls = []
    def files(user, action, **kwargs):
        calls.append((user, action, kwargs))
        return b'contents' if action == 'download' else {'name': kwargs.get('name')}
    runtime = SimpleNamespace(terminal_target=lambda *_a, **_k: None, files=files)
    headers = {'X-Agent-Bridge-User': 'user', 'Content-Type': 'application/octet-stream'}
    with terminal_client(runtime) as client:
        response = client.post('/agent-workspace/terminal/pi/upload?path=docs&name=hello.txt', headers=headers, content=b'file')
        assert response.status_code == 200
        assert calls[-1] == ('user', 'upload', {'path': 'docs', 'name': 'hello.txt', 'data': b'file'})
        response = client.get('/agent-workspace/terminal/pi/download?path=docs/hello.txt', headers=headers)
        assert response.content == b'contents'
        assert 'attachment' in response.headers['content-disposition']
        count = len(calls)
        assert client.post('/agent-workspace/terminal/pi/upload?name=../bad', headers=headers, content=b'x').status_code == 400
        monkeypatch.setattr(module, 'MAX_FILE_BYTES', 4)
        assert client.post('/agent-workspace/terminal/pi/upload?name=big', headers=headers, content=b'12345').status_code == 400
        assert client.post('/agent-workspace/terminal/pi/upload?name=bad', headers={**headers, 'Origin': 'https://evil.example'}, content=b'x').status_code == 403
        assert len(calls) == count


def test_terminal_api_cannot_autostart_without_directory(wm_paths):
    from agent_bridge.app.service import AgentBridgeService
    service = AgentBridgeService.create(wm_paths, {'root'})
    runtime = service.workspace_runtime('pi', mode='terminal')
    with pytest.raises(ValidationError, match='选择启动目录'):
        runtime.ensure_running('user')
    with pytest.raises(ValidationError, match='选择启动目录'):
        runtime.authorize_workspace('user', profile_key=None)


def test_bridge_launch_plan_carries_directory_snapshot(tmp_path):
    from agent_bridge.agent_workspace.terminal_adapter import bridge_plan
    directory = snapshot_directory(str(tmp_path))
    plan = bridge_plan(['pi'], {}, 1234, tmp_path / 'terminal.log', 'Pi', directory)
    assert plan.command[plan.command.index('--directory') + 1] == str(tmp_path.resolve())
    assert plan.command[plan.command.index('--directory-inode') + 1] == str(directory.inode)
    assert plan.command[-2:] == ['--', 'pi']


def test_real_bridge_starts_in_selected_directory_and_restarts_on_change(wm_paths, tmp_path):
    import sys
    import time
    from agent_bridge.app.service import AgentBridgeService
    from agent_bridge.agent_workspace.terminal_adapter import PiTerminalAdapter, bridge_plan
    from agent_bridge.agent_workspace.terminal_service import TerminalRuntimeService
    from agent_bridge.core.domain import AccessDenied

    class LocalAdapter(PiTerminalAdapter):
        def build_launch_plan(self, *, identity, workspace, port, log_path, **_kwargs):
            code = "import os,time; from pathlib import Path; Path('started-here.txt').write_text(os.getcwd()); time.sleep(60)"
            return bridge_plan([sys.executable, '-c', code], dict(os.environ), port, log_path,
                               '测试终端', workspace.directory)
        def on_stopped(self, *_args, **_kwargs):
            pass

    service = AgentBridgeService.create(wm_paths, {'root'})
    service.access.upsert_group(actor='root', group_key='team', name='小组')
    service.access.create_user(actor='root', user_id='user')
    service.access.set_user_group(actor='root', user_id='user', group_key='team')
    service.dsh_configs.save_runtime_config('root', web_command='dsh web {patch} --host 127.0.0.1 --port {port} --no-open', idle_timeout_minutes=120, base_url='http://model.invalid/v1', available_models=['m'])
    service.dsh_configs.save_group_config('root', group_key='team', linux_user='mapped', default_model='m')
    lookup = lambda _name: SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid(), pw_dir=str(tmp_path))
    runtime = TerminalRuntimeService(adapter_type=LocalAdapter, paths=wm_paths, configs=service.dsh_configs,
                                     access=service.access, admins={'root'}, passwd_lookup=lookup)
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    def wait_started(path):
        deadline = time.monotonic() + 5
        while not (path / 'started-here.txt').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        return (path / 'started-here.txt').read_text()

    try:
        assert runtime.browse_directories('user')['root'] == str(tmp_path.resolve())
        assert not runtime._all_states()
        status = runtime.start_in_directory('user', directory=str(first))
        assert status['working_directory'] == str(first.resolve())
        assert wait_started(first) == str(first.resolve())
        pid = runtime._read_state('personal', 'user')['pid']
        runtime.start_in_directory('user', directory=str(first))
        assert runtime._read_state('personal', 'user')['pid'] == pid
        runtime.files('user', 'upload', name='upload.txt', data=b'actual runtime')
        assert (first / 'upload.txt').read_bytes() == b'actual runtime'
        runtime.start_in_directory('user', directory=str(second))
        assert runtime._read_state('personal', 'user')['pid'] != pid
        assert wait_started(second) == str(second.resolve())
        service.access.upsert_group(actor='root', group_key='other', name='其他小组')
        service.access.set_user_group(actor='root', user_id='user', group_key='other')
        with pytest.raises(AccessDenied):
            runtime.files('user', 'list')
    finally:
        runtime.stop_all()
