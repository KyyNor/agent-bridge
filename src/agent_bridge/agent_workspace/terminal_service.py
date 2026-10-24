"""个人终端复用公共 runtime；代理只取得当前登录用户的已登记目标。"""
from __future__ import annotations

from agent_bridge.agent_runtime.service import DEFAULT_MCP_URL
from agent_bridge.agent_workspace.launcher import PopenDshLauncher
from agent_bridge.agent_workspace.service import WorkspaceRuntimeService
from agent_bridge.core.domain import AccessDenied, ValidationError
from agent_bridge.agent_workspace.directory import WorkspaceDirectory
from agent_bridge.agent_workspace.files import WorkspaceFileService
from agent_bridge.agent_workspace.launcher import resolve_linux_identity


class TerminalRuntimeService(WorkspaceRuntimeService):
    def __init__(self, *, adapter_type, paths, configs, access, admins, launcher=None, passwd_lookup=None,
                 mcp_url=DEFAULT_MCP_URL):
        launcher = launcher or PopenDshLauncher()
        adapter = adapter_type(configs=configs, launcher=launcher, mcp_url=mcp_url)
        super().__init__(adapter=adapter, paths=paths, configs=configs, access=access, admins=admins,
                         launcher=launcher, passwd_lookup=passwd_lookup, mcp_url=mcp_url,
                         state_dir_name=adapter.slug + "-terminal-runtimes")

    def _file_identity(self, user_id):
        user_id = self._require_user_id(user_id)
        group = self._access.actor_group_key(user_id, required=True)
        config = self._group_config(group)
        return resolve_linux_identity(self._linux_user(config, group), self._passwd_lookup)

    def browse_directories(self, user_id, directory=None):
        return WorkspaceFileService().browse(self._file_identity(user_id), directory)

    def start_in_directory(self, user_id, *, directory, profile_key=None):
        with self._lock:
            identity = self._file_identity(user_id)
            result = WorkspaceFileService().browse(identity, directory)
            selected = WorkspaceDirectory.parse(result['directory'])
            state = self._read_state('personal', user_id)
            if state and (state.get('directory') != selected.payload() or state.get('linux_user') != identity.user):
                self._stop_state(state, 'personal', user_id)
            return self.authorize_workspace(user_id, profile_key=profile_key, directory=selected)

    def authorize_workspace(self, user_id, *, profile_key, scope='personal', directory=None):
        if directory is None:
            raise ValidationError('请先选择启动目录，再启动终端')
        return super().authorize_workspace(user_id, profile_key=profile_key, scope=scope, directory=directory)

    def ensure_running(self, user_id, *, workspace=None, scope='personal'):
        if workspace is None or workspace.directory is None:
            raise ValidationError('请先选择启动目录，再启动终端')
        return super().ensure_running(user_id, workspace=workspace, scope=scope)

    def files(self, user_id, action, *, path='', name='', data=b''):
        with self._lock:
            self.terminal_target(user_id, touch=False)
            state = self._read_state('personal', user_id)
            identity = self._file_identity(user_id)
            if not state or identity.user != state.get('linux_user') or not state.get('directory'):
                raise AccessDenied('终端目录或小组配置已变化，请重新选择目录')
            root = WorkspaceDirectory.parse(state['directory'])
            return WorkspaceFileService().selected(identity, root, action, path, name, data)

    def _probe_port(self, port, state=None):
        # Web adapter 可把 401/重定向视为进程就绪；PTY bridge 的 410 明确表示 Agent 已退出。
        import httpx
        if not port or not state:
            return False
        try:
            response = httpx.get(self._base_url_for_port(port) + "/health", timeout=1,
                                 headers=self.adapter.probe_auth(state))
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def terminal_target(self, user_id, *, touch: bool = True):
        """返回内部地址与 bridge 鉴权；从不进入用户态 HTTP 响应。"""
        with self._lock:
            state = self._read_state("personal", user_id)
            group = self._access.actor_group_key(user_id, required=True)
            if not state or state.get("group_key") != group:
                raise AccessDenied("终端未运行或所属小组已变化，请重新进入")
            profile = state.get("profile_key")
            if profile:
                from agent_bridge.access_control.resources import ScopedResourceType
                self._access.require_resource_read(actor=user_id, resource_type=ScopedResourceType.capability_profile,
                                                   resource_key=profile)
                capability = self.capabilities.current_for_user(user_id)
                if not capability or capability.profile_key != profile or capability.owner_group_key != group:
                    raise AccessDenied("终端能力平面授权已过期，请重新进入")
            pid, port = int(state.get("pid") or 0), int(state.get("port") or 0)
            if not self._pid_alive(pid) or not self._probe_port(port, state):
                raise AccessDenied("终端进程已结束，请重新进入")
            if touch:
                self._touch_state("personal", user_id)
            return self._base_url_for_port(port), str(state.get("auth_secret") or "")

    def recover(self):
        # PTY 会话与进程内 capability 不能跨服务进程安全复用，统一回收遗留实例。
        result = self.stop_all()
        return {"kept": 0, "cleaned": result.get("stopped", 0), "stopped_shared": 0}
