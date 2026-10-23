"""个人终端复用公共 runtime；代理只取得当前登录用户的已登记目标。"""
from __future__ import annotations

from agent_bridge.agent_runtime.service import DEFAULT_MCP_URL
from agent_bridge.agent_workspace.launcher import PopenDshLauncher
from agent_bridge.agent_workspace.service import WorkspaceRuntimeService
from agent_bridge.core.domain import AccessDenied


class TerminalRuntimeService(WorkspaceRuntimeService):
    def __init__(self, *, adapter_type, paths, configs, access, admins, launcher=None, passwd_lookup=None,
                 mcp_url=DEFAULT_MCP_URL):
        launcher = launcher or PopenDshLauncher()
        adapter = adapter_type(configs=configs, launcher=launcher, mcp_url=mcp_url)
        super().__init__(adapter=adapter, paths=paths, configs=configs, access=access, admins=admins,
                         launcher=launcher, passwd_lookup=passwd_lookup, mcp_url=mcp_url,
                         state_dir_name=adapter.slug + "-terminal-runtimes")

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
