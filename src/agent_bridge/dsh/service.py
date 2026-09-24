"""用户级 DSH Web Runtime 生命周期管理。

每个业务用户至多一个个人 DSH Web 实例：按需启动、动态端口、健康探测、显式
停止、空闲自动回收与服务重启后的遗留实例识别。进程以业务用户所属 group
映射出的 Linux uid/gid 运行，个人配置目录固定在该 Linux 用户的
``<home>/.config/dsh/<business-user>/`` 下，不进入 Agent Bridge 数据目录。

工作空间范围（scope）：

- ``personal``（默认）：即上述模式，一人一实例、一份 DSH_HOME；
- ``shared``：同一 Linux 用户下全部业务用户共享同一个 DSH Web Runtime，
  runtime 身份以 Linux 用户为核心（``runtime key = linux_user``），共享
  DSH_HOME 为 ``<home>/.config/dsh/<linux-user>/``。共享实例运行期间能力
  平面锁定为启动时选定的 active profile，后续成员直接进入、不改平面、不
  产生第二个进程；空闲回收/显式停止后下次进入重新允许选择 Profile。

进程生命周期语义与 claude-mem worker 保持一致：state 文件记录 pid/port/
访问时间，SIGTERM→SIGKILL 升级回收，按进程组发信号。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import signal
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from agent_bridge.agent_runtime.service import DEFAULT_MCP_URL
from agent_bridge.access_control.resources import ScopedResourceType
from agent_bridge.core.domain import ValidationError, require_admin_user
from agent_bridge.core.timeutil import utc_iso
from agent_bridge.dsh import injection
from agent_bridge.dsh import plugins
from agent_bridge.dsh.agent_runtime import dsh_binary_from_command
from agent_bridge.dsh.launcher import (
    DshProcessLauncher,
    LinuxIdentity,
    PopenDshLauncher,
    resolve_linux_identity,
)
from agent_bridge.dsh.workspace import (
    DEFAULT_WORKSPACE_SCOPE,
    WORKSPACE_PROXY_PREFIX,
    WORKSPACE_PROXY_SHARED_PREFIX,
    WORKSPACE_SCOPE_PERSONAL,
    WORKSPACE_SCOPE_SHARED,
    DshWorkspaceCapability,
    DshWorkspaceCapabilityRegistry,
    WorkspaceSelection,
    build_mcp_overlay,
    ensure_owner as ensure_overlay_owner,
    mcp_overlay_path as workspace_overlay_path,
    normalize_workspace_scope,
    remove_mcp_overlay,
    write_mcp_overlay,
)

logger = logging.getLogger(__name__)

DSH_HOST = "127.0.0.1"
DSH_PORT_BASE = 48400
DSH_MAX_INSTANCES = 50
DSH_STARTUP_TIMEOUT_SECONDS = 60.0
DSH_STOP_GRACE_SECONDS = 5.0
REAP_INTERVAL_SECONDS = 60.0
# 单个插件安装（含 npm/pnpm 下载）的最长等待；超时按失败处理、下次启动重试。
PLUGIN_INSTALL_TIMEOUT_SECONDS = 240.0
# 一次 runtime 启动内插件安装的总预算：安装先于 web 进程同步执行，冷缓存下
# 必须封顶以免长时间占用服务锁；超出部分留待下次启动补装。
PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS = 600.0

STATE_DIR_NAME = "dsh-runtimes"
LOG_DIR_NAME = "dsh-runtimes"
# 共享实例的 state/log 子目录：与个人实例的 <user_id>.json 文件名空间隔离，
# 业务用户名永远不可能与共享实例路径冲突。
SHARED_STATE_SUBDIR = "shared"

# 各 scope 的浏览器入口路径（代理前缀 + 尾斜杠）。
WORKSPACE_URLS = {
    WORKSPACE_SCOPE_PERSONAL: WORKSPACE_PROXY_PREFIX + "/",
    WORKSPACE_SCOPE_SHARED: WORKSPACE_PROXY_SHARED_PREFIX + "/",
}

# 共享 Runtime 的 capability 记账槽位前缀：与任何业务用户的个人槽位
# （key = user_id）隔离，成员的个人签发/撤销不会触达共享 Runtime 的
# 稳定 capability。
SHARED_RUNTIME_CAPABILITY_PREFIX = "shared-runtime:"


def shared_runtime_capability_key(linux_user: str) -> str:
    return f"{SHARED_RUNTIME_CAPABILITY_PREFIX}{linux_user}"

_USER_ID_PATTERN = re.compile(r"^[^/\\\s]+$")
# DSH 启动横幅：``dsh web: http://127.0.0.1:<port>/?token=<launch-token>``（可能附带 LAN 地址）
_DSH_WEB_URL_RE = re.compile(r"dsh web:\s*(?P<url>https?://[^\s()]+)")


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def _epoch_to_iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return utc_iso(datetime.fromtimestamp(float(epoch), tz=UTC))


class DshRuntimeService:
    """按业务用户托管 DSH Web 实例，并提供状态查询与空闲回收。"""

    def __init__(
        self,
        *,
        paths,
        configs,
        access,
        admins: set[str],
        launcher: DshProcessLauncher | None = None,
        passwd_lookup: Callable[[str], Any] | None = None,
        mcp_url: str | None = None,
    ) -> None:
        self.paths = paths
        self.admins = admins
        self.mcp_url = mcp_url or DEFAULT_MCP_URL
        self._configs = configs
        self._access = access
        self._launcher = launcher or PopenDshLauncher()
        self._passwd_lookup = passwd_lookup
        self.capabilities = DshWorkspaceCapabilityRegistry()
        self._lock = threading.RLock()
        self._reaper_stop = threading.Event()
        self._reaper_thread: threading.Thread | None = None
        # 本进程启动的子进程句柄；用于停止时 wait() 回收，避免僵尸进程被
        # kill(pid, 0) 误判为存活而升级 SIGKILL。重启后经状态文件识别的
        # 遗留 pid 不在此列，走信号回退路径。
        self._processes: dict[int, Any] = {}

    # -- 对外生命周期入口 --

    def ensure_running(
        self,
        user_id: str,
        *,
        workspace: WorkspaceSelection | None = None,
        scope: str = DEFAULT_WORKSPACE_SCOPE,
    ) -> dict[str, Any]:
        """确保目标 DSH Web 存活：健康则复用，缺失/异常则（重）启动。

        传入 ``workspace`` 时按所选能力平面注入 MCP；``profile_key`` 为空表示
        不注入任何能力（等价于清空既有注入）。``scope=shared`` 时 runtime 身份
        为 Linux 用户：已运行实例始终复用——能力平面锁定为启动时的 active
        profile（忽略本次传入的 profile 差异），不会因平面不同重启或并行
        启动第二个进程；``scope=personal`` 保持“平面变化即回收重启”语义。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        group_key = self._access.actor_group_key(normalized_user, required=True)
        config = self._configs.group_config_for_runtime(group_key)
        if config is None:
            raise ValidationError(f"小组 {group_key} 尚未配置 DSH，请先在系统管理页完成组级配置")
        linux_user = str(config.get("linux_user") or group_key)
        runtime_key = self._runtime_key(scope, normalized_user, linux_user)

        with self._lock:
            state = self._read_state(scope, runtime_key)
            if state:
                # 共享实例按 Linux 用户身份解析：不同成员（甚至映射同一 Linux
                # 用户的不同小组）进入都复用现有进程，不因 group/profile 变化重启。
                group_changed = (
                    scope != WORKSPACE_SCOPE_SHARED
                    and str(state.get("group_key") or "") != group_key
                )
                profile_changed = (
                    scope != WORKSPACE_SCOPE_SHARED
                    and workspace is not None
                    and (state.get("profile_key") or None) != workspace.profile_key
                )
                alive = self._pid_alive(int(state.get("pid") or 0))
                healthy = alive and self._probe_port(int(state.get("port") or 0))
                if not group_changed and not profile_changed and healthy:
                    self._touch_state(scope, runtime_key)
                    if workspace is not None and scope != WORKSPACE_SCOPE_SHARED:
                        # 共享 Runtime 的 MCP patch 是 runtime 级配置（capability
                        # 绑定 linux_user + active profile），复用时不重写。
                        self._refresh_workspace_injection(
                            normalized_user, group_key, config, workspace, state=state
                        )
                    logger.info(
                        "DSH Runtime 复用存量实例 scope=%s runtime=%s pid=%s port=%s user=%s",
                        scope,
                        runtime_key,
                        state.get("pid"),
                        state.get("port"),
                        normalized_user,
                    )
                    return self._public_status(
                        self._compute_status(scope, runtime_key, group_key)
                    )
                wedged = (
                    not group_changed
                    and not profile_changed
                    and alive
                    and not healthy
                    and (time.time() - float(state.get("started_at") or 0)) < DSH_STARTUP_TIMEOUT_SECONDS
                )
                if wedged:
                    return self._public_status(
                        self._compute_status(scope, runtime_key, group_key)
                    )
                # 进程退出、启动超时仍不健康、用户换组或（仅个人模式）切换能力
                # 平面：回收后重启。
                if alive:
                    logger.warning(
                        "DSH Runtime 回收后重启 scope=%s runtime=%s user=%s pid=%s 原因=%s",
                        scope,
                        runtime_key,
                        normalized_user,
                        state.get("pid"),
                        "切换能力平面" if profile_changed else ("换组" if group_changed else "启动超时或进程退出"),
                    )
                    self._stop_state(state, scope, runtime_key)

            state = self._start_runtime(
                normalized_user,
                group_key,
                config,
                workspace=self._resume_shared_workspace(state, scope, group_key, linux_user, workspace),
                scope=scope,
                runtime_key=runtime_key,
            )
            return self._public_status(self._compute_status(scope, runtime_key, group_key))

    def _resume_shared_workspace(
        self,
        state: dict[str, Any] | None,
        scope: str,
        group_key: str,
        linux_user: str,
        workspace: WorkspaceSelection | None,
    ) -> WorkspaceSelection | None:
        """共享 Runtime 重启（进程退出/unhealthy 回收）时恢复 active profile。

        共享实例的 MCP 注入是 runtime 级配置：重启前若已有 active profile，
        必须按原 profile 重新签发 runtime capability 并重建 patch，否则会
        以无 MCP 状态启动。调用点必须在 ``_stop_state``（撤销旧 capability）
        之后——重签使用同一记账槽位，先撤销后签发。
        """
        if (
            scope != WORKSPACE_SCOPE_SHARED
            or workspace is not None
            or state is None
        ):
            return workspace
        profile = str(state.get("profile_key") or "")
        if not profile:
            return WorkspaceSelection(profile_key=None)
        # 归属组沿用启动该实例时的组（可能与本成员当前组不同，审计保持一致）。
        owner_group_key = str(state.get("group_key") or group_key)
        capability = self.capabilities.issue(
            user_id=linux_user,
            profile_key=profile,
            owner_group_key=owner_group_key,
            ttl_seconds=None,
            key=shared_runtime_capability_key(linux_user),
        )
        logger.info(
            "DSH 共享 Runtime 重启恢复 active profile shared=%s profile=%s",
            linux_user,
            profile,
        )
        return WorkspaceSelection(profile_key=profile, capability=capability)

    def authorize_workspace(
        self,
        user_id: str,
        *,
        profile_key: str | None,
        scope: str = DEFAULT_WORKSPACE_SCOPE,
    ) -> dict[str, Any]:
        """选定工作空间范围与能力平面（可不选）并确保工作台可用。

        选择 Profile 时先按既有资源读取规则校验权限，再签发用户唯一的短期
        capability 并注入 MCP；不选 Profile 时清空注入，工作台不带任何 MCP。
        共享范围下，已运行的共享实例锁定为其 active profile：请求的其他
        平面被忽略（以 runtime 当前平面为准），无权访问该平面的成员被拒绝
        进入，避免以他人 capability 越权使用 MCP。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        cleaned_profile = str(profile_key or "").strip()

        if scope == WORKSPACE_SCOPE_SHARED:
            return self._authorize_shared_workspace(
                normalized_user, profile_key=cleaned_profile
            )

        if not cleaned_profile:
            self.capabilities.revoke_for_user(normalized_user)
            status = self.ensure_running(
                normalized_user, workspace=WorkspaceSelection(profile_key=None)
            )
            logger.info("DSH Workspace 进入（不注入能力平面）user=%s", normalized_user)
            return {**status, "profile_key": None, "workspace_url": WORKSPACE_URLS[scope]}

        group_key = self._access.actor_group_key(normalized_user, required=True)
        # 切换到无权限 Profile 会被拒绝：沿既有资源读取规则校验能力平面。
        self._access.require_resource_read(
            actor=normalized_user,
            resource_type=ScopedResourceType.capability_profile,
            resource_key=cleaned_profile,
        )
        capability = self.capabilities.issue(
            user_id=normalized_user,
            profile_key=cleaned_profile,
            owner_group_key=group_key,
        )
        status = self.ensure_running(
            normalized_user,
            workspace=WorkspaceSelection(profile_key=cleaned_profile, capability=capability),
        )
        logger.info(
            "DSH Workspace 授权完成 user=%s profile=%s group=%s runtime_status=%s",
            normalized_user,
            cleaned_profile,
            group_key,
            status.get("status"),
        )
        return {**status, "profile_key": cleaned_profile, "workspace_url": WORKSPACE_URLS[scope]}

    def _authorize_shared_workspace(
        self, user_id: str, *, profile_key: str
    ) -> dict[str, Any]:
        """小组共享工作台的进入语义：运行中锁定 active profile，未运行按选择启动。

        共享 Runtime 使用 **runtime 级稳定 capability**：启动时按
        ``linux_user + active_profile`` 签发（user_id = Linux 用户，MCP 调用
        以共享 runtime/Linux 用户身份审计）并写入共享 MCP patch；后续成员
        进入只校验对 active profile 的访问权限，**不重签发、不重写 patch**。
        个人 capability 与共享 capability 记账槽位互相隔离，互不失效。

        全程持有服务锁（``_lock`` 为 RLock，``ensure_running`` 重入安全）：
        「读取 runtime 状态 → 决定 effective profile → 启动/复用」对并发进入
        原子化，后到成员必然观察到先到成员启动的共享实例并被锁定到同一
        active profile，不会出现两个进程或两个平面。
        """
        group_key = self._access.actor_group_key(user_id, required=True)
        config = self._configs.group_config_for_runtime(group_key)
        if config is None:
            raise ValidationError(f"小组 {group_key} 尚未配置 DSH，请先在系统管理页完成组级配置")
        linux_user = str(config.get("linux_user") or group_key)

        with self._lock:
            state = self._read_state(WORKSPACE_SCOPE_SHARED, linux_user)
            running = bool(state) and self._pid_alive(int(state.get("pid") or 0))
            if running:
                active_profile = (state.get("profile_key") or None) if state else None
                if active_profile:
                    # 共享平面是 runtime 属性：无权访问 active profile 的成员不得
                    # 进入（capability 以 runtime/Linux 用户身份审计，成员权限
                    # 只在进入口校验）。
                    self._access.require_resource_read(
                        actor=user_id,
                        resource_type=ScopedResourceType.capability_profile,
                        resource_key=str(active_profile),
                    )
                # 直接进入现有 Runtime：不签发新 capability、不触碰共享 patch。
                status = self.ensure_running(user_id, scope=WORKSPACE_SCOPE_SHARED)
                logger.info(
                    "DSH 共享工作台复用现有 Runtime user=%s shared=%s profile=%s",
                    user_id,
                    linux_user,
                    active_profile,
                )
                return {
                    **status,
                    "profile_key": active_profile,
                    "workspace_url": WORKSPACE_URLS["shared"],
                }

            # 未运行：本次请求（首位成员）决定下一次共享 Runtime 的 active profile。
            effective_profile = profile_key or None
            if effective_profile:
                self._access.require_resource_read(
                    actor=user_id,
                    resource_type=ScopedResourceType.capability_profile,
                    resource_key=str(effective_profile),
                )
                runtime_capability = self.capabilities.issue(
                    user_id=linux_user,
                    profile_key=str(effective_profile),
                    owner_group_key=group_key,
                    # 不设独立过期：capability 绑定共享 Runtime 生命周期，
                    # 随停止/回收/重启显式撤销重签，持续活跃的 runtime 不会
                    # 因固定 TTL 到期而突然失去 MCP。
                    ttl_seconds=None,
                    key=shared_runtime_capability_key(linux_user),
                )
                workspace: WorkspaceSelection | None = WorkspaceSelection(
                    profile_key=str(effective_profile), capability=runtime_capability
                )
            else:
                workspace = WorkspaceSelection(profile_key=None)
            status = self.ensure_running(
                user_id, workspace=workspace, scope=WORKSPACE_SCOPE_SHARED
            )
        logger.info(
            "DSH 共享工作台授权完成 user=%s shared=%s profile=%s group=%s runtime_status=%s",
            user_id,
            linux_user,
            effective_profile,
            group_key,
            status.get("status"),
        )
        return {
            **status,
            "profile_key": effective_profile,
            "workspace_url": WORKSPACE_URLS["shared"],
        }

    def require_workspace_capability(
        self, token: str, *, profile_key: str | None = None
    ) -> DshWorkspaceCapability:
        """MetaMCP 入口校验 DSH capability 并返回绑定的授权上下文。"""
        return self.capabilities.require(token, profile_key=profile_key)

    def runtime_status(
        self, user_id: str, *, scope: str = DEFAULT_WORKSPACE_SCOPE
    ) -> dict[str, Any]:
        """查询业务用户的目标 runtime 状态；不包含 pid/端口等内部细节。"""
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        group_key = self._access.actor_group_key(normalized_user, required=False)
        runtime_key, resolved_group = self._resolve_runtime_key(
            normalized_user, scope, group_key
        )
        return self._public_status(self._compute_status(scope, runtime_key, resolved_group))

    def stop_runtime(
        self, user_id: str, *, scope: str = DEFAULT_WORKSPACE_SCOPE
    ) -> dict[str, Any]:
        """停止目标 runtime；保留其 DSH 配置与 session 数据。

        共享范围下停止会影响同一 Linux 用户下的其他成员，调用方（前端）应
        先给出明确提示。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            stopped = self._stop_state(state, scope, runtime_key) if state else False
            if scope == WORKSPACE_SCOPE_PERSONAL:
                # 只撤销个人 capability；共享 Runtime 的 capability 在
                # _stop_state 内按 runtime 槽位撤销，成员进入共享不触碰其
                # 个人 capability（反之亦然）。
                self.capabilities.revoke_for_user(normalized_user)
        return {
            "user_id": normalized_user,
            "scope": scope,
            "runtime_key": runtime_key,
            "stopped": bool(stopped),
        }

    def touch_runtime(
        self, user_id: str, *, scope: str = DEFAULT_WORKSPACE_SCOPE
    ) -> None:
        """刷新访问时间，供代理请求与显式保活调用。"""
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            self._touch_state(scope, runtime_key)

    def list_runtimes(self, actor: str) -> list[dict[str, Any]]:
        """管理端全量视图：包含 pid、端口、日志路径等内部细节。"""
        require_admin_user(actor, self.admins)
        return [self._compute_status_by_state(state) for state in self._all_states()]

    def require_runtime_target(
        self, user_id: str, *, scope: str = DEFAULT_WORKSPACE_SCOPE
    ) -> str | None:
        """返回目标 runtime 的 localhost base_url；未运行时返回 None。

        代理层用它解析转发目标，目标只能来自已登记的 runtime 状态，
        不接受调用方通过 URL 指定任意端口。共享范围的目标由当前业务用户
        所属小组映射的 Linux 用户推导（映射不到即视为未运行），因此业务
        用户只能进入其 Linux 用户对应的共享 Runtime。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            if not state:
                return None
            pid = int(state.get("pid") or 0)
            port = int(state.get("port") or 0)
            if not (pid and port and self._pid_alive(pid) and self._probe_port(port)):
                return None
            self._touch_state(scope, runtime_key)
            return self._base_url_for_port(port)

    def workspace_auth(
        self, user_id: str, *, scope: str = DEFAULT_WORKSPACE_SCOPE
    ) -> tuple[str, str] | None:
        """返回 DSH 启动时打印的鉴权入口路径与查询串，例如 ``("/", "token=…")``。

        DSH Web 只接受带启动 token 的首次导航：``GET /?token=…`` 校验后写入
        会话 Cookie 并 303 回 ``/``，后续请求凭 Cookie 通过。代理层用该入口
        完成首次导航，浏览器无需感知 token。非 DSH 实现或日志缺失时返回 None。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            if not state:
                return None
            auth_path = str(state.get("auth_path") or "")
            auth_query = str(state.get("auth_query") or "")
            if auth_path and auth_query:
                return auth_path, auth_query
            captured = self._capture_auth_entry(state)
            if captured is None:
                return None
            state.update(captured)
            self._write_state(scope, runtime_key, state)
            return str(captured["auth_path"]), str(captured["auth_query"])

    def refresh_workspace_auth(
        self, user_id: str, *, scope: str = DEFAULT_WORKSPACE_SCOPE
    ) -> tuple[str, str] | None:
        """重扫运行日志中的最新 ``dsh web:`` 横幅，更新已轮换的鉴权入口。

        DSH 的启动 token 绑定进程内 owner，Connection 重载会静默轮换 token 并
        重印横幅；启动时捕获的入口可能已过期（表现为交换 401）。本方法返回
        按最新横幅刷新后的入口；横幅与已存入口一致时只回读、不落盘。日志缺失
        或无横幅时返回 None，调用方应沿用既有入口继续处理。
        """
        normalized_user = self._require_user_id(user_id)
        scope = normalize_workspace_scope(scope)
        with self._lock:
            runtime_key, _ = self._resolve_runtime_key(normalized_user, scope, None)
            state = self._read_state(scope, runtime_key)
            if not state:
                return None
            captured = self._capture_auth_entry(state)
            if captured is None:
                return None
            new_path = str(captured["auth_path"])
            new_query = str(captured["auth_query"])
            old_path = str(state.get("auth_path") or "")
            old_query = str(state.get("auth_query") or "")
            if new_path == old_path and new_query == old_query:
                return old_path, old_query
            state.update(captured)
            self._write_state(scope, runtime_key, state)
            logger.info(
                "DSH 启动 token 已轮换，按最新横幅刷新鉴权入口 user=%s scope=%s",
                normalized_user,
                scope,
            )
            return new_path, new_query

    # -- 启动与回收 --

    @staticmethod
    def _runtime_key(scope: str, user_id: str, linux_user: str) -> str:
        """runtime 状态身份：个人 = 业务用户，共享 = Linux 用户。"""
        if scope == WORKSPACE_SCOPE_SHARED:
            return linux_user
        return user_id

    def _resolve_runtime_key(
        self, user_id: str, scope: str, group_key: str | None
    ) -> tuple[str, str | None]:
        """把业务用户解析为 (runtime_key, group_key)。

        共享范围必须能映射出 Linux 用户（未分配小组/组未配置时返回原组并让
        状态计算给出 unassigned/unconfigured），个人范围恒等于业务用户。
        """
        if scope != WORKSPACE_SCOPE_SHARED:
            return user_id, group_key
        if group_key is None:
            group_key = self._access.actor_group_key(user_id, required=False)
        if group_key is None:
            return user_id, None
        config = self._configs.group_config_for_runtime(group_key)
        if config is None:
            return user_id, group_key
        linux_user = str(config.get("linux_user") or group_key)
        return linux_user, group_key

    def _start_runtime(
        self,
        user_id: str,
        group_key: str,
        config: dict[str, Any],
        *,
        workspace: WorkspaceSelection | None = None,
        scope: str = DEFAULT_WORKSPACE_SCOPE,
        runtime_key: str | None = None,
    ) -> dict[str, Any]:
        total_started = time.monotonic()
        linux_user = str(config.get("linux_user") or group_key)
        if runtime_key is None:
            runtime_key = self._runtime_key(scope, user_id, linux_user)
        identity = resolve_linux_identity(linux_user, self._passwd_lookup)
        # 个人 DSH_HOME：<linux home>/.config/dsh/<business-user>/；
        # 共享 DSH_HOME：<linux home>/.config/dsh/<linux-user>/（同组共享）。
        home_owner_name = linux_user if scope == WORKSPACE_SCOPE_SHARED else user_id
        config_dir = self._ensure_config_dir(identity, home_owner_name)
        port = self._available_port()
        runtime_config = self._configs.runtime_config_for_runtime()
        model_binding = self._configs.model_binding_for(group_key)
        self._inject_settings(config_dir, identity, model_binding)
        overlay_path = self._write_workspace_overlay(config_dir, identity, user_id, workspace)
        command = self._build_command(
            str(runtime_config.get("web_command") or ""), port, patch_path=overlay_path
        )
        env = self._build_env(identity, config_dir, model_binding)
        log_path = self._log_path(scope, runtime_key)
        # 插件必须先于 web 进程安装完毕：运行中的 DSH 不会热加载 profile 变更，
        # 后装插件只会在下次重启后出现（首访竞态）。名单内置于包内、随版本
        # 发布；依赖指纹一致且上次全部安装成功时直接跳过，失败不阻塞启动，
        # 未就位条目下次启动自动重试。
        plugin_started = time.monotonic()
        self._install_plugins(
            user_id,
            identity=identity,
            config_dir=config_dir,
            dsh_binary=self._dsh_binary(str(runtime_config.get("web_command") or "")),
            specs=plugins.read_plugin_list(),
        )
        plugin_elapsed = time.monotonic() - plugin_started
        logger.info(
            "DSH Runtime 开始启动 scope=%s runtime=%s user=%s group=%s linux_user=%s"
            " port=%s config_dir=%s 插件阶段耗时=%.1fs",
            scope,
            runtime_key,
            user_id,
            group_key,
            linux_user,
            port,
            config_dir,
            plugin_elapsed,
        )
        try:
            process = self._launcher.start(
                command=command,
                env=env,
                cwd=identity.home if identity.home.is_dir() else config_dir,
                log_path=log_path,
                identity=identity,
            )
        except Exception as exc:
            logger.error(
                "DSH Runtime 启动失败 scope=%s runtime=%s user=%s group=%s 原因=%s",
                scope,
                runtime_key,
                user_id,
                group_key,
                exc,
                exc_info=True,
            )
            raise

        now = time.time()
        state = {
            "user_id": runtime_key,
            "scope": scope,
            "group_key": group_key,
            "linux_user": linux_user,
            "pid": int(process.pid),
            "port": port,
            "config_dir": str(config_dir),
            "log_path": str(log_path),
            "profile_key": workspace.profile_key if workspace is not None else None,
            "started_at": now,
            "last_access_at": now,
        }
        self._processes[int(process.pid)] = process
        self._write_state(scope, runtime_key, state)

        if not self._wait_until_ready(port, process=process):
            exit_code = process.poll()
            tail = self._tail_log(log_path)
            self._terminate_pid(int(process.pid), grace_seconds=DSH_STOP_GRACE_SECONDS)
            self._remove_state(scope, runtime_key)
            if exit_code is not None:
                raise ValidationError(
                    f"DSH Web 进程启动后异常退出（exit={exit_code}），日志尾部：{tail}"
                )
            raise ValidationError(
                f"DSH Web 在 {DSH_STARTUP_TIMEOUT_SECONDS:.0f} 秒内未就绪（127.0.0.1:{port}），日志尾部：{tail}"
            )
        captured = self._capture_auth_entry(state)
        if captured is None:
            logger.warning(
                "DSH Runtime 未在启动日志中找到鉴权入口 user=%s log=%s（工作台将无法自动完成首次鉴权）",
                runtime_key,
                log_path,
            )
        else:
            state.update(captured)
            self._write_state(scope, runtime_key, state)
        logger.info(
            "DSH Runtime 就绪 scope=%s runtime=%s pid=%s port=%s 插件阶段=%.1fs 总耗时=%.1fs",
            scope,
            runtime_key,
            process.pid,
            port,
            plugin_elapsed,
            time.monotonic() - total_started,
        )
        return state

    def _stop_state(self, state: dict[str, Any], scope: str, runtime_key: str) -> bool:
        pid = int(state.get("pid") or 0)
        stopped = self._terminate_pid(pid, grace_seconds=DSH_STOP_GRACE_SECONDS)
        if scope == WORKSPACE_SCOPE_SHARED:
            # 共享 Runtime 的稳定 capability 随 runtime 生命周期撤销；patch 内
            # 的 token 同步失效，一并移除覆盖文件，避免下次按需启动带着死 token。
            self.capabilities.revoke_by_key(shared_runtime_capability_key(runtime_key))
            config_dir = str(state.get("config_dir") or "")
            if config_dir:
                remove_mcp_overlay(workspace_overlay_path(Path(config_dir)))
        self._remove_state(scope, runtime_key)
        logger.info(
            "DSH Runtime 已停止 scope=%s runtime=%s pid=%s signaled=%s config_dir=%s（用户配置已保留）",
            scope,
            runtime_key,
            pid,
            stopped,
            state.get("config_dir"),
        )
        return stopped

    # -- 插件首装 --

    @staticmethod
    def _dsh_binary(web_command: str) -> str:
        """从启动命令模板推导 dsh 可执行名（插件安装与 web 进程同一通道）。"""
        return dsh_binary_from_command(web_command)

    def _plugin_env(self, identity: LinuxIdentity, config_dir: Path) -> dict[str, str]:
        """插件安装进程环境：与 web 进程同源，但不携带 API Key。"""
        env = os.environ.copy()
        env["HOME"] = str(identity.home)
        env["USER"] = identity.user
        env["LOGNAME"] = identity.user
        env["DSH_HOME"] = str(config_dir)
        return env

    def _run_dsh_plugin_command(
        self,
        runner: Callable[..., tuple[int, str]],
        *,
        user_id: str,
        command: list[str],
        env: dict[str, str],
        cwd: Path,
        identity: LinuxIdentity,
    ) -> tuple[int, str] | None:
        """执行一条一次性 dsh 插件命令；进程异常时返回 None（已记日志）。"""
        try:
            return runner(
                command=command,
                env=env,
                cwd=cwd,
                identity=identity,
                timeout_seconds=PLUGIN_INSTALL_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.error(
                "DSH 插件命令异常 user=%s command=%s 原因=%s", user_id, command[1:], exc
            )
            return None

    def _install_plugins(
        self,
        user_id: str,
        *,
        identity: LinuxIdentity,
        config_dir: Path,
        dsh_binary: str,
        specs: list[str],
    ) -> None:
        """在 web 进程启动前按名单补装插件（幂等、可断点续装、指纹跳过）。

        ``_start_runtime`` 在持有服务锁的情况下同步调用本方法：安装完成后
        才启动 DSH Web，浏览器首访即可用到全部插件。安装前先初始化 profile
        并显式声明不构建原生依赖（``ensure_build_blocks``）；成功条目立即写
        入 marker，失败/超时条目不写 marker、不阻塞启动，下次 runtime 启动
        自动重试。全部所需插件成功安装后写入依赖指纹状态；此后依赖未变化
        的冷启动直接跳过全部插件命令。名单为空时零开销。
        """
        if not specs:
            return
        runner = getattr(self._launcher, "run_once", None)
        if runner is None:
            logger.warning(
                "DSH 插件安装跳过：当前 launcher 不支持一次性命令 user=%s", user_id
            )
            return
        fingerprint = plugins.compute_plugin_fingerprint(specs)
        state_path = plugins.plugin_state_path(config_dir)
        plugin_state = plugins.read_plugin_state(config_dir)
        if plugin_state is not None and plugin_state.get("fingerprint") == fingerprint:
            logger.info(
                "DSH 插件依赖未变化，跳过安装 user=%s config_dir=%s fingerprint=%s",
                user_id,
                config_dir,
                fingerprint[:19],
            )
            return
        logger.info(
            "DSH 插件依赖发生变化，开始安装 user=%s config_dir=%s count=%d",
            user_id,
            config_dir,
            len(specs),
        )
        env = self._plugin_env(identity, config_dir)
        cwd = identity.home if identity.home.is_dir() else config_dir
        profile_dir = plugins.profile_dir_for(config_dir)
        # profile 模板（package.json + pnpm-workspace.yaml）由 dsh 在首次插件
        # 命令时生成；先落初始化，才能在其上写构建声明。
        if not (profile_dir / "package.json").exists():
            init = self._run_dsh_plugin_command(
                runner,
                user_id=user_id,
                command=plugins.build_profile_install_command(dsh_binary),
                env=env,
                cwd=cwd,
                identity=identity,
            )
            if init is None or init[0] != 0:
                logger.warning(
                    "DSH profile 初始化失败 user=%s profile=%s exit=%s（继续尝试插件安装）",
                    user_id,
                    profile_dir,
                    init[0] if init is not None else "-",
                )
        plugins.ensure_build_blocks(profile_dir)
        installed = plugins.read_installed_specs(config_dir)
        succeeded: list[str] = list(installed)
        marker_path = plugins.plugin_marker_path(config_dir)
        attempted = 0
        budget_deadline = time.monotonic() + PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS
        for spec in specs:
            if spec in installed:
                continue
            if time.monotonic() >= budget_deadline:
                logger.warning(
                    "DSH 插件安装超出总预算 %.0fs，剩余条目下次启动重试 user=%s 剩余=%d",
                    PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS,
                    user_id,
                    sum(1 for item in specs if item not in succeeded),
                )
                break
            attempted += 1
            started = time.monotonic()
            result = self._run_dsh_plugin_command(
                runner,
                user_id=user_id,
                command=plugins.build_install_command(dsh_binary, spec),
                env=env,
                cwd=cwd,
                identity=identity,
            )
            if result is None:
                continue
            exit_code, output = result
            elapsed = time.monotonic() - started
            if exit_code != 0:
                logger.warning(
                    "DSH 插件安装失败 user=%s plugin=%s exit=%s 耗时=%.1fs 输出尾部=%s（下次启动重试）",
                    user_id, spec, exit_code, elapsed, output[-500:] or "(空)",
                )
                continue
            succeeded.append(spec)
            plugins.write_installed_specs(config_dir, succeeded)
            try:
                marker_path.chmod(0o600)
            except OSError:
                logger.warning("DSH 插件 marker chmod 失败 path=%s", marker_path)
            plugins.ensure_marker_owner(marker_path, uid=identity.uid, gid=identity.gid)
            logger.info(
                "DSH 插件安装完成 user=%s plugin=%s 耗时=%.1fs", user_id, spec, elapsed
            )
        if set(specs) <= set(succeeded):
            # 只有全部所需插件成功安装才写入成功指纹；失败/超时留待下次重试。
            plugins.write_plugin_state(config_dir, fingerprint=fingerprint, specs=specs)
            try:
                state_path.chmod(0o600)
            except OSError:
                logger.warning("DSH 插件状态 chmod 失败 path=%s", state_path)
            plugins.ensure_marker_owner(state_path, uid=identity.uid, gid=identity.gid)
            if attempted:
                logger.info(
                    "DSH 插件名单已全部就位 user=%s count=%d fingerprint=%s",
                    user_id,
                    len(specs),
                    fingerprint[:19],
                )

    def _wait_until_ready(self, port: int, *, process) -> bool:
        deadline = time.monotonic() + DSH_STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._probe_port(port):
                return True
            if process.poll() is not None:
                logger.error(
                    "DSH Web 子进程启动期退出 port=%s exit_code=%s",
                    port,
                    process.returncode,
                )
                return False
            time.sleep(0.3)
        logger.warning(
            "DSH Web 等待就绪超时 port=%s timeout=%.0fs", port, DSH_STARTUP_TIMEOUT_SECONDS
        )
        return False

    def _probe_port(self, port: int) -> bool:
        if port <= 0:
            return False
        try:
            response = httpx.get(self._base_url_for_port(port) + "/", timeout=1.0)
        except Exception:
            return False
        return response.status_code < 500

    def _available_port(self) -> int:
        occupied = {
            int(state.get("port") or 0)
            for state in self._all_states()
            if self._pid_alive(int(state.get("pid") or 0))
        }
        for candidate in range(DSH_PORT_BASE, DSH_PORT_BASE + DSH_MAX_INSTANCES):
            if candidate in occupied or self._port_in_use(candidate):
                continue
            return candidate
        raise ValidationError(
            "DSH Runtime 本地端口池已耗尽"
            f"（{DSH_PORT_BASE}-{DSH_PORT_BASE + DSH_MAX_INSTANCES - 1}），"
            "请先停止不再使用的实例或联系管理员扩大端口池"
        )

    def _write_workspace_overlay(
        self,
        config_dir: Path,
        identity: LinuxIdentity,
        user_id: str,
        workspace: WorkspaceSelection | None,
    ) -> Path | None:
        """按能力平面写入/清除 DSH ``--patch`` 覆盖文件，返回需注入的路径。"""
        overlay_path = workspace_overlay_path(config_dir)
        if workspace is None:
            # 未指定选择：沿用已存在的注入（例如仅调用 /runtime/ensure 保活）。
            return overlay_path if overlay_path.exists() else None
        capability = workspace.capability
        if not workspace.profile_key or capability is None:
            if overlay_path.exists():
                remove_mcp_overlay(overlay_path)
                logger.info("DSH Workspace 已清除能力平面注入 user=%s", user_id)
            return None
        write_mcp_overlay(
            overlay_path,
            build_mcp_overlay(
                mcp_url=self.mcp_url,
                profile_key=workspace.profile_key,
                capability_token=capability.token,
            ),
        )
        ensure_overlay_owner(overlay_path, uid=identity.uid, gid=identity.gid)
        logger.info(
            "DSH Workspace MCP 覆盖文件已写入 user=%s profile=%s path=%s",
            user_id,
            workspace.profile_key,
            overlay_path,
        )
        return overlay_path

    def _refresh_workspace_injection(
        self,
        user_id: str,
        group_key: str,
        config: dict[str, Any],
        workspace: WorkspaceSelection,
        *,
        state: dict[str, Any] | None = None,
    ) -> None:
        """复用实例时重写覆盖文件，使新签发的 capability 立即生效。

        共享实例的覆盖文件位于共享 DSH_HOME：写入的是本次进入成员的
        capability（能力平面即 runtime active profile），其他成员的 MCP
        调用随 DSH 重载覆盖文件后改用最新 capability。
        """
        state = state or self._read_state(WORKSPACE_SCOPE_PERSONAL, user_id) or {}
        config_dir = Path(str(state.get("config_dir") or ""))
        if not config_dir or not config_dir.is_dir():
            logger.warning("DSH Workspace 覆盖文件刷新跳过（配置目录缺失）user=%s", user_id)
            return
        linux_user = str(config.get("linux_user") or group_key)
        identity = resolve_linux_identity(linux_user, self._passwd_lookup)
        self._write_workspace_overlay(config_dir, identity, user_id, workspace)

    def _build_command(self, template: str, port: int, *, patch_path: Path | None = None) -> list[str]:
        """渲染启动命令；``{patch}`` 在注入配置文件时展开为 ``--patch <路径>``。"""
        patch_arg = f'--patch "{patch_path}"' if patch_path is not None else ""
        try:
            rendered = template.format(port=port, patch=patch_arg)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValidationError(
                f"DSH 启动命令模板不合法：{template!r}（仅支持 {{port}} 与 {{patch}} 占位符）"
            ) from exc
        command = shlex.split(rendered)
        if not command:
            raise ValidationError(f"DSH 启动命令模板不合法：{template!r}")
        return command

    def _inject_settings(
        self,
        config_dir: Path,
        identity: LinuxIdentity,
        binding: dict[str, Any],
    ) -> Path | None:
        """把公共接入（Base URL / 模型）与组级默认模型写入 DSH settings.yaml。"""
        written = injection.write_settings(
            config_dir,
            base_url=str(binding.get("base_url") or ""),
            models=list(binding.get("available_models") or []),
            default_model=str(binding.get("default_model") or ""),
        )
        # DSH 会热写该文件保存用户偏好，必须归属目标 Linux 用户（root 运行时）。
        settings_file = injection.settings_path(config_dir)
        if settings_file.exists():
            injection.ensure_owner(settings_file, uid=identity.uid, gid=identity.gid)
        if written is None and not binding.get("base_url"):
            logger.warning(
                "DSH 模型接入未配置 Base URL（全局与公共模型配置均为空）config_dir=%s，DSH 将使用自身默认供应商",
                config_dir,
            )
        elif written is None and not binding.get("available_models"):
            # Base URL 已解析（含公共模型配置回落）但全局可用模型为空：供应商
            # 条目与 apiKeyEnv 均不落盘，组级 API Key 不会生效，必须留痕。
            logger.warning(
                "DSH 模型接入已解析 Base URL（source=%s）但全局可用模型列表为空，"
                "跳过供应商注入，组级 API Key 不会生效 config_dir=%s，请在 DSH 运行配置中补充可用模型",
                str(binding.get("base_url_source") or "-"),
                config_dir,
            )
        return written

    def _build_env(
        self,
        identity: LinuxIdentity,
        config_dir: Path,
        binding: dict[str, Any],
    ) -> dict[str, str]:
        """构造 DSH 进程环境：DSH 只从 settings.yaml 读模型配置，密钥走环境变量。"""
        env = os.environ.copy()
        env["HOME"] = str(identity.home)
        env["USER"] = identity.user
        env["LOGNAME"] = identity.user
        env["DSH_HOME"] = str(config_dir)
        env.update(injection.managed_api_key_env_value(str(binding.get("api_key") or "")))
        return env

    @staticmethod
    def _capture_auth_entry(state: dict[str, Any]) -> dict[str, str] | None:
        """从 DSH 运行日志解析 ``dsh web: http://…/?token=…`` 鉴权入口。

        DSH 会在 token 轮换（Connection 重载）时重印横幅，因此以日志中
        最后一条横幅为当前有效入口。
        """
        log_path = Path(str(state.get("log_path") or ""))
        if not log_path or not log_path.exists():
            return None
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        captured: dict[str, str] | None = None
        for match in _DSH_WEB_URL_RE.finditer(text):
            parsed = urlparse(match.group("url"))
            if not parsed.query:
                continue
            captured = {
                "auth_path": parsed.path or "/",
                "auth_query": parsed.query,
            }
        return captured

    def _ensure_config_dir(self, identity: LinuxIdentity, user_id: str) -> Path:
        """创建 ``<linux home>/.config/dsh/<business-user>/`` 并归属目标用户。

        只对本次新建的目录段执行 chown；已存在的用户目录保持原样。
        """
        dsh_root = identity.home / ".config" / "dsh"
        user_dir = dsh_root / user_id
        created: list[Path] = []
        probe = dsh_root
        while not probe.exists():
            created.append(probe)
            parent = probe.parent
            if parent == probe:
                break
            probe = parent
        user_dir.mkdir(parents=True, exist_ok=True)
        if user_dir not in created:
            created.append(user_dir)
        running_uid = os.geteuid()
        if running_uid == 0 and identity.uid != running_uid:
            for path in created:
                try:
                    os.chown(path, identity.uid, identity.gid)
                except OSError as exc:
                    logger.warning(
                        "DSH 配置目录 chown 失败 path=%s uid=%s 原因=%s",
                        path,
                        identity.uid,
                        exc,
                    )
        try:
            user_dir.chmod(0o700)
        except OSError as exc:
            logger.warning("DSH 配置目录 chmod 失败 path=%s 原因=%s", user_dir, exc)
        return user_dir

    # -- 状态计算 --

    def _compute_status_by_state(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime_key = str(state.get("user_id") or "")
        scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
        group_key = str(state.get("group_key") or "")
        pid = int(state.get("pid") or 0)
        port = int(state.get("port") or 0)
        started_at = float(state.get("started_at") or 0.0)
        last_access_at = float(state.get("last_access_at") or started_at)
        alive = self._pid_alive(pid)
        healthy = alive and self._probe_port(port)
        if healthy:
            status = "running"
        elif alive and (time.time() - started_at) < DSH_STARTUP_TIMEOUT_SECONDS:
            status = "starting"
        elif alive:
            status = "unhealthy"
        else:
            status = "stopped"
        return {
            "user_id": runtime_key,
            "scope": scope,
            "group_key": group_key,
            "linux_user": str(state.get("linux_user") or ""),
            "config_dir": str(state.get("config_dir") or ""),
            "status": status,
            "profile_key": state.get("profile_key"),
            "started_at": _epoch_to_iso(started_at),
            "last_access_at": _epoch_to_iso(last_access_at),
            "idle_minutes": round(max(0.0, time.time() - last_access_at) / 60.0, 1),
            "pid": pid if alive else None,
            "port": port,
            "base_url": self._base_url_for_port(port),
            "log_path": str(state.get("log_path") or ""),
        }

    def _compute_status(
        self, scope: str, runtime_key: str, group_key: str | None
    ) -> dict[str, Any]:
        state = self._read_state(scope, runtime_key)
        if state is not None:
            return self._compute_status_by_state(state)
        linux_user = ""
        if scope == WORKSPACE_SCOPE_SHARED and group_key:
            config = self._configs.group_config_for_runtime(group_key)
            if config is not None:
                linux_user = str(config.get("linux_user") or group_key)
        payload: dict[str, Any] = {
            "user_id": runtime_key,
            "scope": scope,
            "group_key": group_key,
            "linux_user": linux_user,
            "config_dir": "",
            "status": "stopped",
            "profile_key": None,
            "started_at": None,
            "last_access_at": None,
            "idle_minutes": None,
            "pid": None,
            "port": 0,
            "base_url": "",
            "log_path": "",
        }
        if group_key is None:
            payload["status"] = "unassigned"
        elif self._configs.group_config_for_runtime(group_key) is None:
            payload["status"] = "unconfigured"
        return payload

    @staticmethod
    def _public_status(status: dict[str, Any]) -> dict[str, Any]:
        """面向业务用户的状态视图：不暴露动态端口与进程细节。"""
        keys = (
            "user_id",
            "scope",
            "group_key",
            "linux_user",
            "config_dir",
            "status",
            "profile_key",
            "started_at",
            "last_access_at",
            "idle_minutes",
        )
        return {key: status.get(key) for key in keys}

    # -- 空闲回收与服务启停 --

    def start(self) -> None:
        """启动空闲回收线程；服务启动期调用。"""
        if self._reaper_thread is not None:
            return
        self._reaper_stop.clear()
        self._reaper_thread = threading.Thread(
            target=self._reap_loop,
            name="agent-bridge-dsh-reaper",
            daemon=True,
        )
        self._reaper_thread.start()
        logger.info("DSH Runtime 空闲回收线程已启动 interval=%.0fs", REAP_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._reaper_stop.set()
        thread = self._reaper_thread
        self._reaper_thread = None
        if thread is not None:
            thread.join(timeout=REAP_INTERVAL_SECONDS + 5.0)
        logger.info("DSH Runtime 空闲回收线程已停止")

    def _reap_loop(self) -> None:
        while not self._reaper_stop.wait(REAP_INTERVAL_SECONDS):
            try:
                self.stop_idle_expired()
            except Exception:
                logger.warning("DSH Runtime 空闲回收扫描失败", exc_info=True)

    def stop_idle_expired(self) -> list[str]:
        """停止超过空闲阈值的实例；返回被停止的 runtime 标识列表。

        空闲严格按 ``last_access_at``（最后一次实际访问/操作）判断；任何成员
        的进入/代理访问都会刷新对应 runtime（含共享实例）的访问时间。
        """
        timeout_minutes = int(
            self._configs.runtime_config_for_runtime().get("idle_timeout_minutes") or 0
        )
        if timeout_minutes <= 0:
            return []
        cutoff = time.time() - timeout_minutes * 60
        stopped: list[str] = []
        with self._lock:
            for state in self._all_states():
                scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
                runtime_key = str(state.get("user_id") or "")
                last_access_at = float(state.get("last_access_at") or 0.0)
                if last_access_at >= cutoff:
                    continue
                logger.info(
                    "DSH Runtime 空闲回收 scope=%s runtime=%s idle_minutes=%.1f threshold=%d",
                    scope,
                    runtime_key,
                    max(0.0, time.time() - last_access_at) / 60.0,
                    timeout_minutes,
                )
                self._stop_state(state, scope, runtime_key)
                stopped.append(runtime_key)
        return stopped

    def recover(self) -> dict[str, Any]:
        """服务启动期识别上一进程遗留的 runtime 实例并清理失效状态。

        共享 Runtime 的稳定 capability 只存在于 Agent Bridge 进程内存，
        而 DSH patch 里的 token 持久留在 DSH_HOME：跨进程恢复的共享实例
        必然持有死 token（MCP 请求全部无效），且后续成员进入不会重签。
        因此对存活共享实例直接安全停止并清理 state/patch，让首位成员下次
        进入时重新按 active profile 启动并生成新 capability；个人实例维持
        「存活保留」语义（个人 capability 本就随进程内存失效，重进会重签）。
        """
        kept = 0
        cleaned = 0
        stopped_shared = 0
        with self._lock:
            for state in self._all_states():
                scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
                runtime_key = str(state.get("user_id") or "")
                alive = self._pid_alive(int(state.get("pid") or 0))
                if scope == WORKSPACE_SCOPE_SHARED:
                    # _stop_state 会撤销 capability 并移除共享 patch 覆盖文件。
                    self._stop_state(state, scope, runtime_key)
                    stopped_shared += 1
                    logger.info(
                        "DSH 共享 Runtime 遗留实例已回收（token 不可跨进程复用，待成员重新进入）"
                        " shared=%s pid=%s alive=%s",
                        runtime_key,
                        state.get("pid"),
                        alive,
                    )
                    continue
                if alive:
                    kept += 1
                    logger.info(
                        "识别到存活的 DSH Runtime 遗留实例 scope=%s runtime=%s pid=%s port=%s",
                        scope,
                        runtime_key,
                        state.get("pid"),
                        state.get("port"),
                    )
                else:
                    self._remove_state(scope, runtime_key)
                    cleaned += 1
                    logger.info(
                        "清理已退出的 DSH Runtime 遗留状态 scope=%s runtime=%s pid=%s",
                        scope,
                        runtime_key,
                        state.get("pid"),
                    )
        return {"kept": kept, "cleaned": cleaned, "stopped_shared": stopped_shared}

    def stop_all(self) -> dict[str, Any]:
        """服务停止期回收全部实例（含共享）；用户 DSH 配置与 session 数据保留。"""
        stopped = 0
        with self._lock:
            for state in self._all_states():
                scope = str(state.get("scope") or WORKSPACE_SCOPE_PERSONAL)
                runtime_key = str(state.get("user_id") or "")
                if self._stop_state(state, scope, runtime_key):
                    stopped += 1
            # capability 按业务用户签发（共享 runtime 有多名进入成员），
            # 进程收尾时统一清空。
            self.capabilities.revoke_all()
        logger.info("DSH Runtime 全部回收完成 stopped=%d", stopped)
        return {"stopped": stopped}

    # -- 进程与工具方法 --

    @staticmethod
    def _require_user_id(user_id: str) -> str:
        normalized = str(user_id or "").strip()
        if not normalized or not _USER_ID_PATTERN.fullmatch(normalized) or normalized in {".", ".."}:
            raise ValidationError(f"业务用户 ID 不合法：{user_id!r}")
        return normalized

    @staticmethod
    def _base_url_for_port(port: int) -> str:
        return f"http://{DSH_HOST}:{port}"

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def _port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex((DSH_HOST, port)) == 0

    def _terminate_pid(self, pid: int, *, grace_seconds: float) -> bool:
        if pid <= 0 or not self._pid_alive(pid):
            return False
        self._signal_process(pid, signal.SIGTERM)
        self._wait_for_exit(pid, grace_seconds=grace_seconds)
        if self._pid_alive(pid):
            logger.warning("DSH Runtime 优雅退出超时，升级为 SIGKILL pid=%s", pid)
            self._signal_process(pid, signal.SIGKILL)
            self._wait_for_exit(pid, grace_seconds=grace_seconds)
        self._processes.pop(pid, None)
        return True

    def _wait_for_exit(self, pid: int, *, grace_seconds: float) -> None:
        process = self._processes.get(pid)
        if process is not None:
            try:
                process.wait(timeout=max(0.0, grace_seconds))
            except Exception:
                pass
            return
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while time.monotonic() < deadline and self._pid_alive(pid):
            time.sleep(0.1)

    @staticmethod
    def _signal_process(pid: int, sig: int) -> None:
        try:
            process_group_id = os.getpgid(pid)
        except OSError:
            process_group_id = pid
        try:
            os.killpg(process_group_id, sig)
        except ProcessLookupError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        except OSError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def _state_path(self, scope: str, runtime_key: str) -> Path:
        """runtime 状态文件：个人 ``<key>.json``，共享 ``shared/<key>.json``。"""
        state_dir = self._state_dir()
        if scope == WORKSPACE_SCOPE_SHARED:
            return state_dir / SHARED_STATE_SUBDIR / f"{_safe_key(runtime_key)}.json"
        return state_dir / f"{_safe_key(runtime_key)}.json"

    def _state_dir(self) -> Path:
        return self.paths.run_dir / STATE_DIR_NAME

    def _log_path(self, scope: str, runtime_key: str) -> Path:
        base = self.paths.logs_dir / LOG_DIR_NAME
        if scope == WORKSPACE_SCOPE_SHARED:
            return base / SHARED_STATE_SUBDIR / f"{_safe_key(runtime_key)}.log"
        return base / f"{_safe_key(runtime_key)}.log"

    def _read_state(self, scope: str, runtime_key: str) -> dict[str, Any] | None:
        path = self._state_path(scope, runtime_key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "DSH Runtime 状态文件损坏 scope=%s runtime=%s path=%s",
                scope,
                runtime_key,
                path,
            )
            return None
        return payload if isinstance(payload, dict) and payload.get("pid") else None

    def _write_state(self, scope: str, runtime_key: str, state: dict[str, Any]) -> None:
        path = self._state_path(scope, runtime_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _touch_state(self, scope: str, runtime_key: str) -> None:
        state = self._read_state(scope, runtime_key)
        if not state:
            return
        state["last_access_at"] = time.time()
        self._write_state(scope, runtime_key, state)

    def _remove_state(self, scope: str, runtime_key: str) -> None:
        self._state_path(scope, runtime_key).unlink(missing_ok=True)

    def _all_states(self) -> list[dict[str, Any]]:
        """读取全部 runtime 状态（个人 + 共享子目录）。"""
        state_dir = self._state_dir()
        if not state_dir.exists():
            return []
        state_paths = sorted(state_dir.glob("*.json"))
        shared_dir = state_dir / SHARED_STATE_SUBDIR
        if shared_dir.is_dir():
            state_paths.extend(sorted(shared_dir.glob("*.json")))
        states: list[dict[str, Any]] = []
        for state_path in state_paths:
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("DSH Runtime 状态文件损坏，已移除 path=%s", state_path)
                state_path.unlink(missing_ok=True)
                continue
            if isinstance(payload, dict) and payload.get("pid"):
                states.append(payload)
        return states

    @staticmethod
    def _tail_log(log_path: Path, *, max_chars: int = 2000) -> str:
        if not log_path.exists():
            return ""
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        return text[-max_chars:].strip()
