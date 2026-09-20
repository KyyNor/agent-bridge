"""DSH Web Runtime 生命周期与配置管理的单元/接口测试。"""

from __future__ import annotations

import json
import os
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
    def __init__(self, pid: int = 410001) -> None:
        self.next_pid = pid
        self.starts: list[dict] = []
        self.once_calls: list[dict] = []
        self.once_handler = None
        # 跨方法的执行顺序（“先装后启”的竞态断言依赖它）
        self.order: list[tuple[str, object]] = []

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
            {
                "pid": process.pid,
                "command": list(command),
                "env": dict(env),
                "cwd": str(cwd),
                "log_path": str(log_path),
                "identity": identity,
                "port": port,
            }
        )
        self.order.append(("start", process.pid))
        return process

    def run_once(self, *, command, env, cwd, identity, timeout_seconds):
        self.once_calls.append(
            {
                "command": list(command),
                "env": dict(env),
                "cwd": str(cwd),
                "timeout": timeout_seconds,
            }
        )
        self.order.append(("install", command[-1]))
        if self.once_handler is not None:
            return self.once_handler(list(command))
        return 0, "installed"


@pytest.fixture
def service(wm_paths):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.access.upsert_group(actor="root", group_key="groupa", name="A 组")
    svc.access.create_user(actor="root", user_id="user1")
    svc.access.create_user(actor="root", user_id="user2")
    svc.access.set_user_group(actor="root", user_id="user1", group_key="groupa")
    svc.access.set_user_group(actor="root", user_id="user2", group_key="groupa")
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


def configure_runtime(service, **overrides) -> None:
    """配置全局公共接入（Base URL + 可用模型）与 groupa 的组级配置。"""
    global_payload = {
        "web_command": "dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        "idle_timeout_minutes": 120,
        "base_url": "http://model.internal/v1",
        "available_models": ["gpt-x", "gpt-y"],
    }
    global_payload.update(overrides)
    service.dsh_configs.save_runtime_config("root", **global_payload)
    service.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        linux_user="groupa",
        default_model="gpt-x",
        api_key="sk-secret",
    )


def patch_lifecycle(service, monkeypatch, *, alive_pids: set[int] | None = None, healthy_ports: set[int] | None = None, launcher: FakeLauncher | None = None):
    """替换 pid 存活/端口健康/终止三个进程级副作用，测试内闭环。

    传入 ``launcher`` 时，其启动的实例自动视为存活且健康，模拟真实进程行为。
    """
    alive = set(alive_pids or ())
    healthy = set(healthy_ports or ())
    terminated: list[int] = []

    def fake_pid_alive(pid: int) -> bool:
        if launcher is not None and any(start["pid"] == pid for start in launcher.starts):
            return True
        return pid in alive

    def fake_probe(port: int) -> bool:
        if launcher is not None and any(int(start["port"]) == port for start in launcher.starts):
            return True
        return port in healthy

    def fake_terminate(pid: int, *, grace_seconds: float) -> bool:
        terminated.append(pid)
        alive.discard(pid)
        healthy.discard(pid)
        return True

    monkeypatch.setattr(service.dsh, "_pid_alive", staticmethod(fake_pid_alive))
    monkeypatch.setattr(service.dsh, "_probe_port", staticmethod(fake_probe))
    monkeypatch.setattr(service.dsh, "_terminate_pid", fake_terminate)
    return terminated


def install_fake_launcher(service, home, passwd_lookup) -> FakeLauncher:
    launcher = FakeLauncher()
    service.dsh._launcher = launcher
    service.dsh._passwd_lookup = passwd_lookup
    return launcher


# -- 配置服务 --


def test_public_model_config_is_global_and_group_config_validation(service) -> None:
    # 默认模型必须来自全局可用模型列表
    service.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        idle_timeout_minutes=60,
        base_url="http://model.internal/v1",
        available_models=["gpt-x", "gpt-y"],
    )
    with pytest.raises(Exception) as exc_info:
        service.dsh_configs.save_group_config(
            "root", group_key="groupa", default_model="not-listed"
        )
    assert "可用模型" in str(exc_info.value)

    with pytest.raises(Exception):
        service.dsh_configs.save_group_config("root", group_key="missing-group")

    with pytest.raises(Exception):
        service.dsh_configs.save_runtime_config(
            "root",
            web_command="dsh web --port {port}",
            idle_timeout_minutes=60,
            base_url="not-a-url",
            available_models=[],
        )

    saved = service.dsh_configs.save_group_config(
        "root", group_key="groupa", default_model="gpt-x", api_key="sk-secret"
    )
    assert saved["api_key_set"] is True
    assert "api_key" not in saved
    assert saved["default_model"] == "gpt-x"
    # 组配置不再承载公共接入字段
    assert "base_url" not in saved and "available_models" not in saved
    # runtime 读取入口能拿到原值
    raw = service.dsh_configs.group_config_for_runtime("groupa")
    assert raw["api_key"] == "sk-secret"

    with pytest.raises(Exception) as conflict:
        service.dsh_configs.save_group_config(
            "root", group_key="groupa", default_model="gpt-x", expected_edit_token="stale-token"
        )
    assert "更新" in str(conflict.value) or "409" in str(conflict.value)


def test_group_base_url_falls_back_to_public_model_config(service) -> None:
    service.store.save_retrieval_probe_llm_config(
        base_url="http://public-model.internal/v1",
        model="small-model",
        api_key=None,
        clear_api_key=True,
    )
    # 全局 Base URL 留空 → 继承「公共模型配置」
    service.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        idle_timeout_minutes=60,
        base_url="",
        available_models=["gpt-x"],
    )
    resolved = service.dsh_configs.runtime_config_for_runtime()
    assert resolved["base_url"] == "http://public-model.internal/v1"
    assert resolved["base_url_source"] == "public_model_config"
    binding = service.dsh_configs.model_binding_for("groupa")
    assert binding["base_url"] == "http://public-model.internal/v1"

    # 显式配置优先
    service.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        idle_timeout_minutes=60,
        base_url="http://dsh-model.internal/v1",
        available_models=["gpt-x"],
    )
    assert service.dsh_configs.runtime_config_for_runtime()["base_url"] == "http://dsh-model.internal/v1"


def test_group_config_api_key_clear_semantics(service) -> None:
    service.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        idle_timeout_minutes=60,
        base_url="",
        available_models=["m1"],
    )
    service.dsh_configs.save_group_config("root", group_key="groupa", default_model="m1", api_key="sk-1")
    cleared = service.dsh_configs.save_group_config(
        "root",
        group_key="groupa",
        default_model="m1",
        clear_api_key=True,
        expected_edit_token=service.dsh_configs.get_group_config("root", "groupa")["edit_token"],
    )
    assert cleared["api_key_set"] is False
    assert service.dsh_configs.group_config_for_runtime("groupa")["api_key"] == ""


def test_runtime_config_roundtrip_and_defaults(service) -> None:
    default_config = service.dsh_configs.get_runtime_config("root")
    assert default_config["web_command"]
    assert default_config["idle_timeout_minutes"] > 0
    assert "{patch}" in default_config["web_command"]

    saved = service.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh-server run --port {port}",
        idle_timeout_minutes=30,
        base_url="http://model.internal/v1",
        available_models=["m1", "m1", "m2"],
        expected_edit_token=default_config["edit_token"],
    )
    assert saved["web_command"] == "dsh-server run --port {port}"
    assert saved["available_models"] == ["m1", "m2"]
    assert service.dsh_configs.runtime_config_for_runtime()["idle_timeout_minutes"] == 30

    with pytest.raises(Exception):
        service.dsh_configs.save_runtime_config(
            "root",
            web_command="",
            idle_timeout_minutes=0,
            base_url="",
            available_models=[],
            expected_edit_token=saved["edit_token"],
        )
    # 未知占位符必须被拒绝，避免启动时才炸
    with pytest.raises(Exception):
        service.dsh_configs.save_runtime_config(
            "root",
            web_command="dsh web --port {port} --unknown {x}",
            idle_timeout_minutes=30,
            base_url="",
            available_models=[],
            expected_edit_token=saved["edit_token"],
        )


def test_config_endpoints_require_admin(service) -> None:
    from fastapi.testclient import TestClient
    from agent_bridge.api.app import create_app

    client = TestClient(create_app(service.paths, {"root"}))
    assert client.get("/api/v1/dsh/group-configs", headers={"X-Agent-Bridge-User": "user1"}).status_code == 403
    assert client.get("/api/v1/dsh/group-configs", headers={"X-Agent-Bridge-User": "root"}).status_code == 200


# -- Runtime 生命周期 --


def test_ensure_running_starts_reuses_and_injects_settings(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    patch_lifecycle(service, monkeypatch, launcher=launcher)

    first = service.dsh.ensure_running("user1")
    assert first["status"] == "running"
    assert "port" not in first and "pid" not in first
    assert first["group_key"] == "groupa"

    assert len(launcher.starts) == 1
    start = launcher.starts[0]
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    assert start["env"]["DSH_HOME"] == str(config_dir)
    assert start["env"]["HOME"] == str(home / "groupa")
    # API Key 只通过环境变量传递；Base URL/模型走 DSH 原生 settings.yaml
    assert start["env"]["AGENT_BRIDGE_DSH_API_KEY"] == "sk-secret"
    for legacy_key in ("DSH_BASE_URL", "DSH_API_KEY", "DSH_DEFAULT_MODEL", "DSH_AVAILABLE_MODELS"):
        assert legacy_key not in start["env"]
    assert start["cwd"] == str(home / "groupa")
    assert "{port}" not in " ".join(start["command"]) and "{patch}" not in " ".join(start["command"])
    assert "--no-open" in start["command"]

    # 配置目录确实创建在 Linux 用户 home 下，而不是 Agent Bridge data 目录
    assert config_dir.is_dir()
    assert not (service.paths.data_dir / "dsh").exists()

    # DSH 原生 settings.yaml 收到公共 Base URL、模型目录与组级默认模型
    settings = yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8"))
    provider = settings["llm-pi-ai"]["providers"]["agent-bridge"]
    assert provider["baseURL"] == "http://model.internal/v1"
    assert provider["api"] == "openai-completions"
    assert provider["apiKeyEnv"] == "AGENT_BRIDGE_DSH_API_KEY"
    assert [model["id"] for model in provider["models"]] == ["gpt-x", "gpt-y"]
    assert settings["agent-default-model"] == {"provider": "agent-bridge", "model": "gpt-x"}

    # 第二次进入复用同一个实例
    second = service.dsh.ensure_running("user1")
    assert second["status"] == "running"
    assert len(launcher.starts) == 1

    # 同 group 的另一个业务用户目录互相隔离
    third = service.dsh.ensure_running("user2")
    assert third["status"] == "running"
    assert len(launcher.starts) == 2
    assert launcher.starts[1]["env"]["DSH_HOME"] == str(home / "groupa" / ".config" / "dsh" / "user2")


def test_injected_settings_preserve_unmanaged_user_configuration(service, home) -> None:
    configure_runtime(service)
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.yaml").write_text(
        "ui-theme:\n  preference: dark\ndsh-desktop:\n  openBrowser: false\n",
        encoding="utf-8",
    )

    service.dsh._inject_settings(
        config_dir,
        types.SimpleNamespace(uid=os.getuid(), gid=os.getgid()),
        service.dsh_configs.model_binding_for("groupa"),
    )
    settings = yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8"))
    assert settings["ui-theme"] == {"preference": "dark"}
    assert settings["dsh-desktop"] == {"openBrowser": False}
    assert settings["llm-pi-ai"]["providers"]["agent-bridge"]["baseURL"] == "http://model.internal/v1"


def test_inject_settings_warns_when_models_catalog_empty(service, home, caplog) -> None:
    """Base URL 已解析但全局可用模型为空：跳过供应商注入必须留下告警。

    此时 settings.yaml 不写 agent-bridge 供应商条目，apiKeyEnv 无处挂靠，
    组级 API Key 即使注入进程环境也不会生效——静默跳过会让“密钥没进去”
    无法从日志定位。
    """
    import logging

    service.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        idle_timeout_minutes=60,
        base_url="http://model.internal/v1",
        available_models=[],
    )
    service.dsh_configs.save_group_config(
        "root", group_key="groupa", linux_user="groupa", default_model="", api_key="sk-secret"
    )
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    config_dir.mkdir(parents=True)

    with caplog.at_level(logging.WARNING, logger="agent_bridge.dsh.service"):
        written = service.dsh._inject_settings(
            config_dir,
            types.SimpleNamespace(uid=os.getuid(), gid=os.getgid()),
            service.dsh_configs.model_binding_for("groupa"),
        )

    assert written is None
    assert not (config_dir / "settings.yaml").exists()
    assert any(
        "全局可用模型列表为空" in record.message and "API Key 不会生效" in record.message
        for record in caplog.records
    )


# -- 插件首装 --


def test_plugin_list_parsing_and_marker_roundtrip(tmp_path) -> None:
    """名单解析：忽略注释/空行/重复；marker 可读写；命令形态固定。"""
    from agent_bridge.dsh import plugins

    text = "\n".join(
        [
            "# Agent Bridge 托管的 DSH 插件名单",
            "",
            "@linxin666/dsh-client-ui-task-board@latest",
            "  dsh-context  # 行内注释",
            "@linxin666/dsh-client-ui-task-board@latest",
            "",
        ]
    )
    assert plugins.parse_plugin_list(text) == [
        "@linxin666/dsh-client-ui-task-board@latest",
        "dsh-context",
    ]
    # 包内名单随版本发布：文件必须存在且解析出非空 spec 列表
    assert plugins.plugin_list_path().name == "dsh-plugins.txt"
    packaged = plugins.read_plugin_list()
    assert packaged and "@linxin666/dsh-client-ui-task-board@0.3.23" in packaged
    plugins.write_installed_specs(tmp_path, ["a", "b"])
    assert plugins.read_installed_specs(tmp_path) == ["a", "b"]
    assert plugins.plugin_marker_path(tmp_path).name == "agent-bridge-plugins.txt"
    assert plugins.build_install_command("dsh", "dsh-context") == [
        "dsh",
        "plugin",
        "--profile",
        "web",
        "add",
        "dsh-context",
    ]


def test_packaged_plugin_list_is_pinned() -> None:
    """版本名单必须钉住精确版本：同一 Agent Bridge 版本装出一致的插件集。"""
    from agent_bridge.dsh import plugins

    specs = plugins.read_plugin_list()
    assert specs, "包内插件名单不应为空"
    for spec in specs:
        # 作用域包自身以 @ 开头，版本分隔符取最后一个 @
        name, _, version = spec.rpartition("@")
        assert name.strip("@") and version, f"插件缺少精确版本：{spec}"
        assert version[0].isdigit(), f"插件版本未钉住（浮动 tag 或范围）：{spec}"


def test_install_plugins_records_marker_and_retries_failures(
    service, home, passwd_lookup
) -> None:
    """安装成功才写 marker；失败条目下次启动只补装失败项。"""
    from agent_bridge.dsh import plugins
    from agent_bridge.dsh.launcher import resolve_linux_identity

    launcher = install_fake_launcher(service, home, passwd_lookup)
    results = {"dsh-context": (0, "ok"), "deepseek-idesign": (1, "pnpm ERR")}
    launcher.once_handler = lambda command: results[command[-1]]
    identity = resolve_linux_identity("groupa", service.dsh._passwd_lookup)
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    config_dir.mkdir(parents=True)
    specs = ["dsh-context", "deepseek-idesign"]

    service.dsh._install_plugins(
        "user1", identity=identity, config_dir=config_dir, dsh_binary="dsh", specs=specs
    )
    assert [call["command"] for call in launcher.once_calls] == [
        ["dsh", "plugin", "--profile", "web", "add", "dsh-context"],
        ["dsh", "plugin", "--profile", "web", "add", "deepseek-idesign"],
    ]
    # 安装环境指向用户 DSH_HOME，且不携带组级 API Key
    env = launcher.once_calls[0]["env"]
    assert env["DSH_HOME"] == str(config_dir)
    assert "AGENT_BRIDGE_DSH_API_KEY" not in env
    marker = config_dir / "agent-bridge-plugins.txt"
    assert plugins.read_installed_specs(config_dir) == ["dsh-context"]
    assert oct(marker.stat().st_mode & 0o777) == "0o600"

    launcher.once_handler = lambda command: (0, "ok")
    service.dsh._install_plugins(
        "user1", identity=identity, config_dir=config_dir, dsh_binary="dsh", specs=specs
    )
    assert [call["command"][-1] for call in launcher.once_calls[2:]] == ["deepseek-idesign"]
    assert plugins.read_installed_specs(config_dir) == ["dsh-context", "deepseek-idesign"]


def test_plugins_install_before_web_start_and_only_once(
    service, home, passwd_lookup, monkeypatch, tmp_path
) -> None:
    """插件必须在 web 进程启动前装完（运行中的 DSH 不热加载），且只装一次。"""
    from agent_bridge.dsh import plugins

    configure_runtime(service)
    # 名单内置于包内：测试用受控名单文件替换读取入口
    list_path = tmp_path / "dsh-plugins.txt"
    list_path.write_text("dsh-context\ndeepseek-idesign\n", encoding="utf-8")
    monkeypatch.setattr(plugins, "plugin_list_path", lambda: list_path)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    patch_lifecycle(service, monkeypatch, launcher=launcher)

    service.dsh.ensure_running("user1")
    assert len(launcher.once_calls) == 2
    # 竞态回归：所有安装动作都发生在 web 进程 start 之前
    first_start_index = next(i for i, entry in enumerate(launcher.order) if entry[0] == "start")
    assert all(entry[0] == "install" for entry in launcher.order[:first_start_index])
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    assert plugins.read_installed_specs(config_dir) == ["dsh-context", "deepseek-idesign"]

    # 重启 runtime：marker 齐全，直接启动、不再执行任何安装命令
    service.dsh._stop_state(service.dsh._read_state("user1"), "user1")
    service.dsh.ensure_running("user1")
    assert len(launcher.once_calls) == 2
    second_start_index = len(launcher.order) - 1
    assert launcher.order[second_start_index][0] == "start"

    # 名单缺失（等价于版本名单为空）同样零执行
    list_path.unlink()
    service.dsh._stop_state(service.dsh._read_state("user1"), "user1")
    service.dsh.ensure_running("user1")
    assert len(launcher.once_calls) == 2


def test_install_plugins_stops_at_total_budget(service, home, passwd_lookup, monkeypatch) -> None:
    """安装总预算耗尽后停止尝试，不影响后续启动；已装条目保留。"""
    from agent_bridge.dsh import plugins
    from agent_bridge.dsh.launcher import resolve_linux_identity
    from agent_bridge.dsh import service as service_module

    launcher = install_fake_launcher(service, home, passwd_lookup)
    launcher.once_handler = lambda command: (0, "ok")
    monkeypatch.setattr(service_module, "PLUGIN_INSTALL_TOTAL_BUDGET_SECONDS", 0.0)
    identity = resolve_linux_identity("groupa", service.dsh._passwd_lookup)
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    config_dir.mkdir(parents=True)

    service.dsh._install_plugins(
        "user1",
        identity=identity,
        config_dir=config_dir,
        dsh_binary="dsh",
        specs=["dsh-context", "deepseek-idesign"],
    )
    assert launcher.once_calls == []
    assert plugins.read_installed_specs(config_dir) == []


def test_command_template_renders_patch_placeholder(service) -> None:
    command = service.dsh._build_command(
        "dsh web {patch} --host 127.0.0.1 --port {port} --no-open", 48500
    )
    assert command == ["dsh", "web", "--host", "127.0.0.1", "--port", "48500", "--no-open"]
    with_patch = service.dsh._build_command(
        "dsh web {patch} --port {port}",
        48500,
        patch_path=Path("/run/agent-bridge/dsh-workspaces/user1.patch.yml"),
    )
    assert with_patch[:3] == ["dsh", "web", "--patch"]
    assert with_patch[3] == "/run/agent-bridge/dsh-workspaces/user1.patch.yml"
    assert with_patch[-1] == "48500"


def test_auth_entry_is_captured_from_start_log(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    patch_lifecycle(service, monkeypatch, launcher=launcher)

    log_path = service.paths.logs_dir / "dsh-runtimes" / "user1.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "dsh web: http://127.0.0.1:48500/?token=launch-token-abc (LAN: http://10.0.0.2:48500/?token=launch-token-abc)\n",
        encoding="utf-8",
    )
    service.dsh.ensure_running("user1")
    state = service.dsh._read_state("user1")
    assert state["auth_path"] == "/"
    assert state["auth_query"] == "token=launch-token-abc"
    assert service.dsh.workspace_auth("user1") == ("/", "token=launch-token-abc")


def test_auth_entry_missing_is_tolerated(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    patch_lifecycle(service, monkeypatch, launcher=launcher)
    result = service.dsh.ensure_running("user1")
    assert result["status"] == "running"
    assert service.dsh.workspace_auth("user1") is None


def test_ensure_running_rejects_unassigned_or_unconfigured(service) -> None:
    service.access.create_user(actor="root", user_id="lonely")
    with pytest.raises(Exception):
        service.dsh.ensure_running("lonely")

    configure_runtime(service)
    service.access.create_user(actor="root", user_id="other-group-user")
    service.access.upsert_group(actor="root", group_key="groupb", name="B 组")
    service.access.set_user_group(actor="root", user_id="other-group-user", group_key="groupb")
    with pytest.raises(Exception) as exc_info:
        service.dsh.ensure_running("other-group-user")
    assert "尚未配置" in str(exc_info.value)


def test_dead_process_state_is_restarted(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    terminated = patch_lifecycle(service, monkeypatch, launcher=launcher)

    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": 400123,
        "port": 48400,
        "config_dir": str(home / "groupa" / ".config" / "dsh" / "user1"),
        "log_path": str(service.paths.logs_dir / "dsh-runtimes" / "user1.log"),
        "started_at": time.time() - 3600,
        "last_access_at": time.time() - 3600,
    }
    service.dsh._write_state("user1", state)

    result = service.dsh.ensure_running("user1")
    assert result["status"] == "running"
    assert len(launcher.starts) == 1
    assert terminated == []  # 进程已死，无需发信号
    assert service.dsh._read_state("user1")["pid"] == launcher.starts[0]["pid"]


def test_wedged_process_is_restarted_after_timeout(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    # 老实例存活但端口长期不健康（启动早已超过就绪窗口）
    terminated = patch_lifecycle(service, monkeypatch, alive_pids={400123}, launcher=launcher)

    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": 400123,
        "port": 48400,
        "config_dir": "",
        "log_path": "",
        "started_at": time.time() - 3600,
        "last_access_at": time.time() - 3600,
    }
    service.dsh._write_state("user1", state)

    result = service.dsh.ensure_running("user1")
    assert result["status"] == "running"
    assert terminated == [400123]
    assert len(launcher.starts) == 1


def test_starting_instance_is_reused_without_restart(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    # 老实例存活、尚未通过健康探测，但仍在启动窗口内：不重启，返回 starting
    patch_lifecycle(service, monkeypatch, alive_pids={400124})

    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": 400124,
        "port": 48400,
        "config_dir": "",
        "log_path": "",
        "started_at": time.time() - 5,
        "last_access_at": time.time() - 5,
    }
    service.dsh._write_state("user1", state)

    result = service.dsh.ensure_running("user1")
    assert result["status"] == "starting"
    assert launcher.starts == []


def test_group_change_restarts_runtime(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    service.access.upsert_group(actor="root", group_key="groupb", name="B 组")
    service.dsh_configs.save_group_config("root", group_key="groupb", default_model="gpt-x")

    old_pid = 410100
    terminated = patch_lifecycle(
        service, monkeypatch, alive_pids={old_pid}, healthy_ports={48400}, launcher=launcher
    )
    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": old_pid,
        "port": 48400,
        "config_dir": "",
        "log_path": "",
        "started_at": time.time(),
        "last_access_at": time.time(),
    }
    service.dsh._write_state("user1", state)

    service.access.set_user_group(actor="root", user_id="user1", group_key="groupb")
    result = service.dsh.ensure_running("user1")
    assert result["status"] == "running"
    assert result["group_key"] == "groupb"
    assert terminated == [old_pid]


def test_stop_runtime_keeps_config_dir(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    launcher = install_fake_launcher(service, home, passwd_lookup)
    pid = 410200
    patch_lifecycle(service, monkeypatch, alive_pids={pid}, healthy_ports={48400})
    config_dir = home / "groupa" / ".config" / "dsh" / "user1"
    config_dir.mkdir(parents=True)
    (config_dir / "session.json").write_text("{}", encoding="utf-8")
    state = {
        "user_id": "user1",
        "group_key": "groupa",
        "linux_user": "groupa",
        "pid": pid,
        "port": 48400,
        "config_dir": str(config_dir),
        "log_path": "",
        "started_at": time.time(),
        "last_access_at": time.time(),
    }
    service.dsh._write_state("user1", state)

    stopped = service.dsh.stop_runtime("user1")
    assert stopped["stopped"] is True
    assert service.dsh._read_state("user1") is None
    # 停止 Runtime 不删除用户 DSH 配置和 session 数据
    assert (config_dir / "session.json").exists()


def test_idle_reaper_stops_expired_only(service, home, passwd_lookup, monkeypatch) -> None:
    configure_runtime(service)
    install_fake_launcher(service, home, passwd_lookup)
    terminated = patch_lifecycle(service, monkeypatch, alive_pids={410300, 410301}, healthy_ports={48400, 48401})
    old = time.time() - 10 * 24 * 60 * 60
    for user_id, pid, port, last_access in (("user1", 410300, 48400, old), ("user2", 410301, 48401, time.time())):
        service.dsh._write_state(
            user_id,
            {
                "user_id": user_id,
                "group_key": "groupa",
                "linux_user": "groupa",
                "pid": pid,
                "port": port,
                "config_dir": "",
                "log_path": "",
                "started_at": last_access,
                "last_access_at": last_access,
            },
        )

    stopped = service.dsh.stop_idle_expired()
    assert stopped == ["user1"]
    assert service.dsh._read_state("user1") is None
    assert service.dsh._read_state("user2") is not None
    assert 410300 in terminated and 410301 not in terminated


def test_touch_refreshes_last_access(service, monkeypatch) -> None:
    old = time.time() - 3600
    service.dsh._write_state(
        "user1",
        {
            "user_id": "user1",
            "group_key": "groupa",
            "linux_user": "groupa",
            "pid": 410400,
            "port": 48400,
            "config_dir": "",
            "log_path": "",
            "started_at": old,
            "last_access_at": old,
        },
    )
    service.dsh.touch_runtime("user1")
    assert service.dsh._read_state("user1")["last_access_at"] > old


def test_recover_cleans_dead_and_keeps_alive(service, monkeypatch) -> None:
    patch_lifecycle(service, monkeypatch, alive_pids={410500}, healthy_ports={48400})
    for user_id, pid in (("user1", 410500), ("user2", 410501)):
        service.dsh._write_state(
            user_id,
            {
                "user_id": user_id,
                "group_key": "groupa",
                "linux_user": "groupa",
                "pid": pid,
                "port": 48400,
                "config_dir": "",
                "log_path": "",
                "started_at": time.time(),
                "last_access_at": time.time(),
            },
        )
    result = service.dsh.recover()
    assert result == {"kept": 1, "cleaned": 1}
    assert service.dsh._read_state("user1") is not None
    assert service.dsh._read_state("user2") is None


def test_port_pool_skips_occupied(service, monkeypatch) -> None:
    service.dsh._write_state(
        "user2",
        {
            "user_id": "user2",
            "group_key": "groupa",
            "linux_user": "groupa",
            "pid": 410600,
            "port": 48400,
            "config_dir": "",
            "log_path": "",
            "started_at": time.time(),
            "last_access_at": time.time(),
        },
    )
    patch_lifecycle(service, monkeypatch, alive_pids={410600})
    monkeypatch.setattr(service.dsh, "_port_in_use", staticmethod(lambda port: port == 48401))
    assert service.dsh._available_port() == 48402


def test_require_runtime_target_reads_registered_state_only(service, monkeypatch) -> None:
    patch_lifecycle(service, monkeypatch, alive_pids={410700}, healthy_ports={48410})
    service.dsh._write_state(
        "user1",
        {
            "user_id": "user1",
            "group_key": "groupa",
            "linux_user": "groupa",
            "pid": 410700,
            "port": 48410,
            "config_dir": "",
            "log_path": "",
            "started_at": time.time(),
            "last_access_at": time.time() - 600,
        },
    )
    target = service.dsh.require_runtime_target("user1")
    assert target == "http://127.0.0.1:48410"
    # 访问刷新了空闲时间
    assert service.dsh._read_state("user1")["last_access_at"] > time.time() - 60
    assert service.dsh.require_runtime_target("user2") is None


# -- API 集成 --


def test_dsh_runtime_api_flow(service, home, passwd_lookup, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from agent_bridge.api.app import create_app

    configure_runtime(service)
    client = TestClient(create_app(service.paths, {"root"}))
    headers_user = {"X-Agent-Bridge-User": "user1"}
    headers_root = {"X-Agent-Bridge-User": "root"}

    # create_app 会基于同一数据目录装配新的 service；进程类 fakes 需装在 app 实例上
    app_service = client.app.state.agent_bridge_service
    install_fake_launcher(app_service, home, passwd_lookup)

    status = client.get("/api/v1/dsh/runtime", headers=headers_user).json()
    assert status["status"] == "stopped"

    # 先经 API 保存全局运行配置与组级配置
    runtime_saved = client.put(
        "/api/v1/dsh/runtime-config",
        headers=headers_root,
        json={
            "web_command": "dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
            "idle_timeout_minutes": 120,
            "base_url": "http://model.internal/v1",
            "available_models": ["gpt-x", "gpt-y"],
            "expected_edit_token": client.get("/api/v1/dsh/runtime-config", headers=headers_root).json()["edit_token"],
        },
    )
    assert runtime_saved.status_code == 200, runtime_saved.text
    assert runtime_saved.json()["available_models"] == ["gpt-x", "gpt-y"]

    saved = client.put(
        "/api/v1/dsh/group-configs/groupa",
        headers=headers_root,
        json={"linux_user": "groupa", "default_model": "gpt-x", "api_key": "sk-secret"},
    )
    assert saved.status_code == 200
    assert saved.json()["api_key_set"] is True
    assert "api_key" not in saved.json()
    # API 也拒绝不在全局列表中的默认模型
    rejected = client.put(
        "/api/v1/dsh/group-configs/groupa",
        headers=headers_root,
        json={"default_model": "nope", "expected_edit_token": saved.json()["edit_token"]},
    )
    assert rejected.status_code == 400

    launcher = app_service.dsh._launcher
    patch_lifecycle(app_service, monkeypatch, launcher=launcher)

    ensured = client.post("/api/v1/dsh/runtime/ensure", headers=headers_user)
    assert ensured.status_code == 200
    payload = ensured.json()
    assert payload["status"] == "running"
    assert "port" not in payload and "pid" not in payload

    # 管理端可以看到端口等细节，普通用户被拒绝
    listed = client.get("/api/v1/dsh/runtimes", headers=headers_root).json()
    assert listed["runtimes"][0]["port"] > 0
    assert client.get("/api/v1/dsh/runtimes", headers=headers_user).status_code == 403

    stopped = client.post("/api/v1/dsh/runtime/stop", headers=headers_user)
    assert stopped.status_code == 200
    assert stopped.json()["stopped"] is True

    # 未识别身份的调用被拒绝
    assert client.get("/api/v1/dsh/runtime").status_code == 401


def test_dsh_runtime_config_api_roundtrip(service) -> None:
    from fastapi.testclient import TestClient
    from agent_bridge.api.app import create_app

    client = TestClient(create_app(service.paths, {"root"}))
    headers_root = {"X-Agent-Bridge-User": "root"}

    current = client.get("/api/v1/dsh/runtime-config", headers=headers_root)
    assert current.status_code == 200
    saved = client.put(
        "/api/v1/dsh/runtime-config",
        headers=headers_root,
        json={
            "web_command": "dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
            "idle_timeout_minutes": 90,
            "base_url": "http://model.internal/v1",
            "available_models": ["m1"],
            "expected_edit_token": current.json()["edit_token"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["idle_timeout_minutes"] == 90

    conflict = client.put(
        "/api/v1/dsh/runtime-config",
        headers=headers_root,
        json={
            "web_command": "dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
            "idle_timeout_minutes": 90,
            "base_url": "http://model.internal/v1",
            "available_models": ["m1"],
            "expected_edit_token": "stale",
        },
    )
    assert conflict.status_code == 409


# -- 公共模型接入迁移 --

_LEGACY_GROUP_LLM_COLUMNS = {
    "base_url": "TEXT NOT NULL DEFAULT ''",
    "available_models_json": "TEXT NOT NULL DEFAULT '[]'",
}


def _legacy_dsh_store(tmp_path, *, group_rows: list[tuple], runtime_row: tuple | None):
    """构造升级前的库：base_url/可用模型还挂在组配置上。"""
    from agent_bridge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "legacy.db")
    store.init_schema()
    with store.connect() as conn:
        for column, definition in _LEGACY_GROUP_LLM_COLUMNS.items():
            conn.execute(f"ALTER TABLE dsh_group_configs ADD COLUMN {column} {definition}")
        for group_key, base_url, models_json in group_rows:
            conn.execute(
                "INSERT INTO dsh_group_configs"
                " (group_key, linux_user, default_model, base_url, available_models_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (group_key, group_key, "gpt-x", base_url, models_json),
            )
        if runtime_row is not None:
            conn.execute(
                "INSERT INTO dsh_runtime_config (id, web_command, idle_timeout_minutes, base_url, available_models_json)"
                " VALUES (1, ?, ?, ?, ?)",
                runtime_row,
            )
    return store


def _dsh_public_config(store) -> tuple[str, list[str]]:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT base_url, available_models_json FROM dsh_runtime_config WHERE id = 1"
        ).fetchone()
    return (row[0], json.loads(row[1])) if row else ("", [])


def test_dsh_public_llm_config_backfilled_from_legacy_group_columns(tmp_path: Path) -> None:
    """升级不得静默丢弃已保存的 Base URL / 可用模型（否则 DSH 退回内置供应商）。"""
    store = _legacy_dsh_store(
        tmp_path,
        group_rows=[
            ("groupa", "https://gateway.internal/v1", '["gpt-x"]'),
            ("groupb", "", '["gpt-y"]'),
            ("groupc", "", "not-json"),
        ],
        runtime_row=("dsh web {port}", 120, "", "[]"),
    )

    store.migrate_phase2()

    assert _dsh_public_config(store) == ("https://gateway.internal/v1", ["gpt-x", "gpt-y"])
    with store.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dsh_group_configs)")}
    assert "base_url" not in columns
    assert "available_models_json" not in columns

    # 幂等：重复迁移不再改动
    store.migrate_phase2()
    assert _dsh_public_config(store) == ("https://gateway.internal/v1", ["gpt-x", "gpt-y"])


def test_dsh_public_llm_config_backfill_keeps_newer_global_values(tmp_path: Path) -> None:
    store = _legacy_dsh_store(
        tmp_path,
        group_rows=[("groupa", "https://legacy.internal/v1", '["legacy-model"]')],
        runtime_row=("dsh web {port}", 120, "https://current.internal/v1", '["current-model"]'),
    )

    store.migrate_phase2()

    assert _dsh_public_config(store) == ("https://current.internal/v1", ["current-model"])


def test_dsh_public_llm_config_backfill_without_legacy_values(tmp_path: Path) -> None:
    """旧组也没有配置时不建全局行，保持“未配置”的语义与告警。"""
    store = _legacy_dsh_store(
        tmp_path,
        group_rows=[("groupa", "", "[]")],
        runtime_row=None,
    )

    store.migrate_phase2()

    assert _dsh_public_config(store) == ("", [])
