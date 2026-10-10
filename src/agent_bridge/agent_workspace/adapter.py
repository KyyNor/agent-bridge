"""Workspace Agent adapter 契约。

每个工作台后端（DSH、Pi）实现 :class:`WorkspaceAgentAdapter`，把「与后端无关的
进程管理」和「后端自己的启动/注入/就绪判定」分开。通用 runtime 只依赖本模块的
协议，新增一种 Agent 只需注册一个 adapter，不修改中心分发链。

与能力中心的 ``CapabilitySourceAdapter`` 规范一致：不写中心 ``if/elif``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agent_bridge.agent_workspace.capability import WorkspaceSelection
from agent_bridge.agent_workspace.launcher import LinuxIdentity


class WorkspaceLaunchPlan:
    """一次 runtime 启动所需的全部后端特定信息。

    通用 runtime 负责进程、端口、状态文件与回收；adapter 只负责把「配置目录 +
    能力平面 + 组级模型接入」翻译成一条可执行命令与一份环境变量。
    """

    __slots__ = (
        "command",
        "env",
        "log_path",
        "startup_timeout_seconds",
        "probe_path",
        "display_name",
        "auth_secret",
    )

    def __init__(
        self,
        *,
        command: list[str],
        env: dict[str, str],
        log_path: Path,
        startup_timeout_seconds: float,
        probe_path: str = "/",
        display_name: str = "",
        auth_secret: str | None = None,
    ) -> None:
        self.command = command
        self.env = env
        self.log_path = log_path
        self.startup_timeout_seconds = startup_timeout_seconds
        self.probe_path = probe_path
        self.display_name = display_name
        #: 代理完成首访鉴权所需的秘密（DSH 的启动 token、Pi 的会话密码）。
        #: 只写入 0600 的 runtime 状态文件，**不得**进日志或用户态响应。
        self.auth_secret = auth_secret


@runtime_checkable
class WorkspaceAgentAdapter(Protocol):
    """一种工作台 Agent 后端。"""

    #: 稳定的机器标识（API 的 ``agent=`` 参数、状态与配置分组都用它）。
    slug: str
    #: 面向用户与日志的展示名。
    display_name: str
    #: 个人范围的浏览器入口前缀（``/agent-workspace`` / ``/agent-workspace-pi``）。
    personal_proxy_prefix: str
    #: 共享范围的浏览器入口前缀。
    shared_proxy_prefix: str
    #: runtime 进程标记的环境变量名（各后端独立，避免跨后端误杀）。
    mark_env: str
    #: 本地端口池起始端口。
    port_base: int
    #: 本地端口池容量。
    port_pool_size: int
    #: 停止时的优雅退出窗口（秒）。
    stop_grace_seconds: float

    def supports_scope(self, scope: str) -> bool:
        """该 adapter 是否支持此工作空间范围。"""

    def config_dir(self, identity: LinuxIdentity, runtime_owner: str) -> Path:
        """该 runtime 的配置/会话目录（个人用业务用户，共享用 Linux 用户）。"""

    def build_launch_plan(
        self,
        *,
        identity: LinuxIdentity,
        runtime_owner: str,
        scope: str,
        runtime_key: str,
        port: int,
        workspace: WorkspaceSelection | None,
        model_binding: dict[str, Any],
        log_path: Path,
    ) -> WorkspaceLaunchPlan:
        """准备启动：写注入文件、渲染命令与环境变量。"""

    def on_stopped(self, state: dict[str, Any], *, scope: str, runtime_key: str) -> None:
        """runtime 停止后清理该后端的托管注入（覆盖文件/MCP 配置等）。"""

    def on_recovered(self, state: dict[str, Any], *, scope: str, runtime_key: str) -> None:
        """服务重启识别到遗留实例时的后端特定收尾。"""

    def probe_auth(self, state: dict[str, Any]) -> dict[str, str]:
        """就绪探测所需的鉴权头；默认后端无需鉴权，返回空。

        返回值直接作为 HTTP 头发给 ``probe_path``。启用鉴权的后端必须提供，
        否则探测恒为 401/307，永远等不到就绪。
        """
        return {}

    def blocked_paths(self) -> tuple[str, ...]:
        """该后端下必须硬拦的上游路径（相对代理前缀之外的上游路径）。

        用于阻断会越权影响整个源站的上游资源（例如以 ``scope: "/"`` 注册的
        service worker）。代理对这些路径直接返回 404，不转发。
        """
        return ()

    def filter_request_cookies(
        self, cookie_header: str | None, *, authority: str
    ) -> str | None:
        """收敛发往该后端的 Cookie；默认不透传任何浏览器 Cookie。

        ``authority`` 是上游 ``host:port``：部分后端（如 DSH）把会话 Cookie 名
        绑定到 authority，因此过滤必须知道目标端口。
        """
        return None

    def needs_proxy_auth(self) -> bool:
        """该后端是否需要代理在转发前完成上游鉴权。

        DSH 由首访 token 换取流程处理（根导航单独走 ``_proxy_root_navigation``），
        返回 False；Pi 的鉴权对每个请求都成立，返回 True。
        """
        return False

    async def resolve_upstream_auth(
        self,
        service,
        *,
        user_id: str,
        target: str,
        workspace_scope: str,
        browser_cookie: str | None,
    ) -> str | None:
        """返回该后端上游所需的 Cookie 值；无需鉴权时返回 None。

        代理只负责调用，实现细节（DSH 的 token 换取、Pi 的会话密码换取）留在
        adapter 内，避免代理出现按 agent 的中心分支。
        """
        return None