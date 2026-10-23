"""网页终端适配：复用 Agent 注入，PTY 与 Web 工作台隔离托管。"""
from __future__ import annotations

import os
import secrets
import shutil
import sys
from pathlib import Path

from agent_bridge.agent_workspace.adapter import WorkspaceLaunchPlan
from agent_bridge.agent_workspace.adapters.dsh import DshWorkspaceAdapter
from agent_bridge.agent_workspace.adapters.pi import PiWorkspaceAdapter
from agent_bridge.agent_workspace.launcher import ensure_user_config_dir
from agent_bridge.core.domain import ValidationError
from agent_bridge.dsh import plugins
from agent_bridge.dsh.agent_runtime import dsh_binary_from_command

TERMINAL_AUTH_ENV = "AGENT_BRIDGE_TERMINAL_AUTH"
TUI_PLUGIN_LIST = Path(plugins.__file__).with_name("dsh-tui-plugins.txt")


def bridge_plan(command, env, port, log_path, display_name):
    """包裹 Agent 命令；秘密只经环境与 0600 runtime 状态传递。"""
    secret = secrets.token_urlsafe(32)
    env = dict(env)
    env.update({TERMINAL_AUTH_ENV: secret, "TERM": "xterm-256color", "COLORTERM": "truecolor"})
    return WorkspaceLaunchPlan(
        command=[sys.executable, "-m", "agent_bridge.agent_workspace.terminal_server",
                 "--port", str(port), "--", *command],
        env=env, log_path=log_path, startup_timeout_seconds=60,
        probe_path="/health", display_name=display_name, auth_secret=secret,
    )


class TerminalAdapter:
    """两种 TUI 共用的范围与本机 bridge 鉴权契约。"""

    probe_path = "/health"
    personal_proxy_prefix = "/api/v1/agent-workspace/terminal"
    shared_proxy_prefix = personal_proxy_prefix

    def supports_scope(self, scope):
        return scope == "personal"

    def probe_auth(self, state):
        return {"Authorization": "Bearer " + str(state.get("auth_secret") or "")}

    def on_recovered(self, state, *, scope, runtime_key):
        return None


class DshTerminalAdapter(TerminalAdapter, DshWorkspaceAdapter):
    """社区 TUI 安装在独立 profile；不加载 Web 插件。"""

    display_name = "DeepSeek Harness 终端"
    plugin_profile = "tui"
    mark_env = "AGENT_BRIDGE_DSH_TERMINAL_RUNTIME"
    port_base = 48600

    def config_dir(self, identity, runtime_owner):
        return ensure_user_config_dir(identity, ".config", "agent-bridge", "dsh-terminal", owner_name=runtime_owner)

    def build_launch_plan(self, *, identity, runtime_owner, scope, runtime_key, port,
                          workspace, model_binding, log_path):
        config_dir = self.config_dir(identity, runtime_owner)
        # 新的 DSH_HOME 必须先有标准存储目录（与 ACP 路径一致）。
        for name in ("sessions", "storages", "change-ledger", "task-board"):
            directory = config_dir / name
            directory.mkdir(exist_ok=True, mode=0o700)
            if os.geteuid() == 0:
                os.chown(directory, identity.uid, identity.gid)
        binary = dsh_binary_from_command(str(self._configs.runtime_config_for_runtime().get("web_command") or ""))
        specs = plugins.parse_plugin_list(TUI_PLUGIN_LIST.read_text(encoding="utf-8"))
        self._install_plugins(runtime_owner, identity=identity, config_dir=config_dir, dsh_binary=binary, specs=specs)
        if not set(specs) <= set(plugins.read_installed_specs(config_dir)):
            raise ValidationError("DSH TUI 插件未安装完成，请检查工作台日志后重试")
        env = self._build_env(identity, config_dir, model_binding, mark="")
        # 社区 TUI 的偏好固定写 HOME/.dsh-tui；独立 HOME 避免同 Linux 用户
        # 下的不同业务用户互相读取历史/偏好，真实 uid 与工作目录仍沿用组映射。
        terminal_home = ensure_user_config_dir(
            identity, ".config", "agent-bridge", "dsh-terminal", runtime_owner, owner_name="home"
        )
        env["HOME"] = str(terminal_home)
        env["DSH_TUI_BACKEND"] = "dsh"
        # Web 与终端必须使用不同进程标记，防止互相回收。
        env.pop("AGENT_BRIDGE_DSH_RUNTIME", None)
        overlay = self._write_workspace_overlay(config_dir, identity, runtime_owner, workspace)
        model_overlay = self._write_model_overlay(config_dir, identity, model_binding, env=env, dsh_binary=binary)
        command = [binary, "--profile", self.plugin_profile]
        for patch in (model_overlay, overlay):
            if patch is not None:
                command.extend(["--patch", str(patch)])
        return bridge_plan(command, env, port, log_path, self.display_name)


class PiTerminalAdapter(TerminalAdapter, PiWorkspaceAdapter):
    """复用 Pi 模型与 MCP 注入，只将启动通道替换为公开交互 CLI。"""

    display_name = "Pi Agent 终端"
    mark_env = "AGENT_BRIDGE_PI_TERMINAL_RUNTIME"
    port_base = 48700

    def config_dir(self, identity, runtime_owner):
        return ensure_user_config_dir(identity, ".config", "agent-bridge", "pi-terminal", owner_name=runtime_owner)

    def build_launch_plan(self, **kwargs):
        plan = super().build_launch_plan(**kwargs)
        command = shutil.which("pi")
        if not command:
            raise ValidationError("未安装 Pi CLI，请按 Pi 依赖版本清单安装后重试")
        env = dict(plan.env)
        env.pop("AGENT_BRIDGE_PI_RUNTIME", None)
        env.pop("PI_WEB_PASSWORD", None)
        return bridge_plan([command], env, kwargs["port"], kwargs["log_path"], self.display_name)


TERMINAL_ADAPTERS = {"dsh": DshTerminalAdapter, "pi": PiTerminalAdapter}
