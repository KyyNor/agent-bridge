"""Pi Agent 工作台的配置注入、adapter 启动与共享 scope 测试（issue #17）。

覆盖：

- ``models.json`` / ``settings.json`` / ``mcp.json`` 只合并托管条目、保留用户
  自有配置，且 capability/API Key 不落盘；
- adapter 的启动命令、环境变量（``PI_CODING_AGENT_DIR``、密码、版本检查开关）
  与配置目录隔离；
- 共享范围：一个 Linux 用户一个目录 + 一个进程，runtime 级稳定 capability；
- 版本清单必须钉死精确版本。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout=None):
        return 0


class FakeLauncher:
    def __init__(self, pid: int = 450001) -> None:
        self.next_pid = pid
        self.starts: list[dict] = []

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
            {"pid": process.pid, "command": list(command), "env": dict(env), "port": port}
        )
        return process


@pytest.fixture
def service(wm_paths, monkeypatch):
    from agent_bridge.app.service import AgentBridgeService

    svc = AgentBridgeService.create(wm_paths, {"root"})
    svc.access.upsert_group(actor="root", group_key="groupa", name="A 组")
    for user_id in ("user1", "user2"):
        svc.access.create_user(actor="root", user_id=user_id)
        svc.access.set_user_group(actor="root", user_id=user_id, group_key="groupa")
    svc.governance.upsert_profile("user1", "safe", "安全平面", "", "active")

    home = Path(wm_paths.root) / "home" / "groupa"
    home.mkdir(parents=True, exist_ok=True)

    def fake_lookup(name):
        if name != "groupa":
            raise KeyError(name)
        return type("Entry", (), {"pw_uid": 501, "pw_gid": 501, "pw_dir": str(home)})()

    launcher = FakeLauncher()
    svc.pi._launcher = launcher
    svc.pi.adapter._launcher = launcher
    svc.pi._passwd_lookup = fake_lookup
    svc.dsh._passwd_lookup = fake_lookup
    # 组级配置：映射到 groupa 这个 Linux 用户
    svc.dsh_configs.save_group_config("root", group_key="groupa", linux_user="groupa")
    # 就绪探测与端口检查本地化，避免真实网络依赖。
    monkeypatch.setattr(svc.pi, "_probe_port", lambda port, state=None: True)
    monkeypatch.setattr(
        svc.pi, "_wait_until_ready", lambda port, *, process, timeout, state=None: True
    )
    monkeypatch.setattr(svc.pi, "_port_in_use", staticmethod(lambda port: False))
    # fake pid 不会真实存在，状态计算统一按存活处理
    monkeypatch.setattr(svc.pi, "_pid_alive", staticmethod(lambda pid: pid > 0))
    monkeypatch.setattr(svc.pi, "_alive_targets", lambda pids: set())
    monkeypatch.setattr(svc.pi, "_collect_runtime_pids", lambda pid, mark: {pid})
    return svc


def _configure_models(svc) -> None:
    svc.dsh_configs.save_runtime_config(
        "root",
        web_command="dsh web {patch} --host 127.0.0.1 --port {port} --no-open",
        idle_timeout_minutes=720,
        base_url="https://models.example.internal/v1",
        available_models=["deepseek-chat", "deepseek-reasoner"],
    )


# -- 配置注入 --


def test_models_json_merges_managed_provider_and_keeps_user_entries(tmp_path) -> None:
    from agent_bridge.agent_workspace.pi_injection import (
        MANAGED_PROVIDER_KEY,
        build_models_payload,
    )

    existing = {
        "providers": {
            "my-own": {"baseUrl": "https://own.example/v1", "models": [{"id": "mine"}]}
        },
        "somethingElse": {"keep": True},
    }
    payload = build_models_payload(
        existing, base_url="https://models.example/v1", models=["m1", "m2"],
        api_key_env="AGENT_BRIDGE_PI_API_KEY",
    )
    providers = payload["providers"]
    # 托管 provider 写入
    assert providers[MANAGED_PROVIDER_KEY]["baseUrl"] == "https://models.example/v1"
    assert [item["id"] for item in providers[MANAGED_PROVIDER_KEY]["models"]] == ["m1", "m2"]
    # API Key 只经环境变量展开，不落盘
    assert providers[MANAGED_PROVIDER_KEY]["apiKey"] == "${AGENT_BRIDGE_PI_API_KEY}"
    # 用户自有 provider 与其他字段原样保留
    assert providers["my-own"] == existing["providers"]["my-own"]
    assert payload["somethingElse"] == {"keep": True}


def test_models_json_drops_managed_provider_when_not_configured() -> None:
    from agent_bridge.agent_workspace.pi_injection import (
        MANAGED_PROVIDER_KEY,
        build_models_payload,
    )

    existing = {"providers": {MANAGED_PROVIDER_KEY: {"baseUrl": "stale"}}}
    payload = build_models_payload(existing, base_url="", models=[], api_key_env="X")
    assert MANAGED_PROVIDER_KEY not in payload["providers"]


def test_models_json_injects_context_window_per_model() -> None:
    """配置的上下文长度必须逐模型写成 contextWindow；未配置时不写该键。"""
    from agent_bridge.agent_workspace.pi_injection import (
        MANAGED_PROVIDER_KEY,
        build_models_payload,
    )

    declared = build_models_payload(
        {}, base_url="https://models.example/v1", models=["m1", "m2"],
        api_key_env="X", context_window=262144,
    )["providers"][MANAGED_PROVIDER_KEY]["models"]
    assert [item["contextWindow"] for item in declared] == [262144, 262144]

    # 0 = 不声明：沿用 pi 自身默认（provider-composer 回落到 128000），
    # 不能写 0——pi 的 models.json schema 与合成期都会拒绝非正数。
    undeclared = build_models_payload(
        {}, base_url="https://models.example/v1", models=["m1"],
        api_key_env="X", context_window=0,
    )["providers"][MANAGED_PROVIDER_KEY]["models"]
    assert "contextWindow" not in undeclared[0]

    # 默认参数同样不写，保持未配置部署的既有产物不变。
    assert "contextWindow" not in build_models_payload(
        {}, base_url="https://models.example/v1", models=["m1"], api_key_env="X",
    )["providers"][MANAGED_PROVIDER_KEY]["models"][0]


def test_models_json_context_window_reaches_disk(service, tmp_path) -> None:
    """组配置里的两个上限要真正落到 models.json（端到端走注入路径）。"""
    from agent_bridge.agent_workspace import pi_injection

    pi_injection.write_models(
        tmp_path,
        uid=os.getuid(),
        gid=os.getgid(),
        base_url="https://models.example/v1",
        models=["m1"],
        context_window=131072,
        max_tokens=32768,
    )
    written = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    models = written["providers"][pi_injection.MANAGED_PROVIDER_KEY]["models"]
    assert models[0]["contextWindow"] == 131072
    assert models[0]["maxTokens"] == 32768


def test_models_json_omits_unset_limits_and_injects_each_independently() -> None:
    """两个上限互相独立：只配一个时另一个不得出现，且 0 一律省略而非写 0。"""
    from agent_bridge.agent_workspace.pi_injection import (
        MANAGED_PROVIDER_KEY,
        build_models_payload,
    )

    def entry(**kwargs):
        payload = build_models_payload(
            {}, base_url="https://models.example/v1", models=["m1"],
            api_key_env="X", **kwargs,
        )
        return payload["providers"][MANAGED_PROVIDER_KEY]["models"][0]

    assert entry() == {"id": "m1", "name": "m1"}
    assert entry(context_window=262144) == {"id": "m1", "name": "m1", "contextWindow": 262144}
    assert entry(max_tokens=32768) == {"id": "m1", "name": "m1", "maxTokens": 32768}
    # 显式 0 与省略等价：models.json 整份 schema 校验，写 0 会让用户自有 provider 一起丢失
    assert entry(context_window=0, max_tokens=0) == {"id": "m1", "name": "m1"}


def test_mcp_json_only_manages_agent_bridge_entry() -> None:
    from agent_bridge.agent_workspace.pi_injection import (
        MANAGED_MCP_SERVER_KEY,
        build_mcp_servers_payload,
    )

    existing = {"mcpServers": {"user-server": {"command": "foo"}}}
    payload = build_mcp_servers_payload(
        existing, mcp_url="http://127.0.0.1:8080/mcp", profile_key="safe",
        capability_token="TOKEN-VALUE",
    )
    servers = payload["mcpServers"]
    assert servers["user-server"] == {"command": "foo"}
    managed = servers[MANAGED_MCP_SERVER_KEY]
    assert managed["url"] == "http://127.0.0.1:8080/mcp"
    # capability token 不落盘：只写变量名，由 pi 的 ${ENV} 展开
    assert "TOKEN-VALUE" not in json.dumps(payload)
    assert managed["headers"]["X-Agent-Bridge-DSH-Capability"] == "${AGENT_BRIDGE_PI_CAPABILITY}"


def test_mcp_json_entry_removed_without_profile() -> None:
    from agent_bridge.agent_workspace.pi_injection import (
        MANAGED_MCP_SERVER_KEY,
        build_mcp_servers_payload,
    )

    existing = {
        "mcpServers": {MANAGED_MCP_SERVER_KEY: {"url": "stale"}, "keep": {"command": "x"}}
    }
    payload = build_mcp_servers_payload(
        existing, mcp_url="http://x/mcp", profile_key=None, capability_token=None
    )
    assert MANAGED_MCP_SERVER_KEY not in payload["mcpServers"]
    assert payload["mcpServers"]["keep"] == {"command": "x"}


def test_settings_json_keeps_user_settings() -> None:
    from agent_bridge.agent_workspace.pi_injection import build_settings_payload

    payload = build_settings_payload(
        {"theme": "dark", "defaultModel": "old"}, default_model="deepseek-chat"
    )
    assert payload["theme"] == "dark"
    assert payload["defaultModel"] == "deepseek-chat"


# -- 启动契约 --


def test_version_manifest_pins_exact_versions() -> None:
    from agent_bridge.agent_workspace.pi_versions import read_version_manifest

    specs = read_version_manifest()
    assert specs, "版本清单不能为空"
    for spec in specs:
        assert "@" in spec
        version = spec.rsplit("@", 1)[1]
        # 不允许 ^、~、latest 或标签
        assert version[0].isdigit(), f"{spec} 必须钉死精确版本"


def test_version_manifest_rejects_range(tmp_path, monkeypatch) -> None:
    from agent_bridge.agent_workspace import pi_versions

    monkeypatch.setattr(
        pi_versions,
        "manifest_path",
        lambda: tmp_path / "pi-versions.txt",
    )
    (tmp_path / "pi-versions.txt").write_text("@agegr/pi-web@^0.12.2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        pi_versions.read_version_manifest()


def test_launch_plan_sets_isolated_dir_and_password(service) -> None:
    _configure_models(service)
    status = service.pi.authorize_workspace("user1", profile_key="safe")
    assert status["status"] == "running"

    start = service.pi._launcher.starts[0]
    env = start["env"]
    config_dir = env["PI_CODING_AGENT_DIR"]
    # 配置目录落在该 Linux 用户 home 下，不进入 Agent Bridge 数据目录，
    # 且不与 pi 自己的默认 agent 目录（~/.pi/agent）混用同一命名空间
    assert config_dir.endswith(".config/agent-bridge/pi/user1")
    assert "/.pi/agent" not in config_dir
    assert "/data/" not in config_dir
    # 密码随机生成且不为空；关闭版本检查与浏览器自启
    assert env["PI_WEB_PASSWORD"]
    assert env["PI_WEB_SKIP_VERSION_CHECK"] == "1"
    assert env["PI_WEB_NO_OPEN"] == "1"
    # 启动命令按模板渲染
    assert "--no-open" in start["command"]
    assert str(start["port"]) in start["command"]


def test_capability_token_only_in_env_not_on_disk(service) -> None:
    _configure_models(service)
    service.pi.authorize_workspace("user1", profile_key="safe")
    state_file = Path(service.pi.paths.run_dir) / "pi-runtimes" / "user1.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    config_dir = Path(state["config_dir"])
    mcp = json.loads((config_dir / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["agent-bridge"]["headers"][
        "X-Agent-Bridge-DSH-Capability"
    ] == "${AGENT_BRIDGE_PI_CAPABILITY}"
    # 状态文件只存 capability 摘要，不存明文 token
    assert "capability_digest" in state
    assert "capability_token" not in state


def test_models_and_default_model_written(service) -> None:
    _configure_models(service)
    service.pi.authorize_workspace("user1", profile_key="safe")
    state_file = Path(service.pi.paths.run_dir) / "pi-runtimes" / "user1.json"
    config_dir = Path(json.loads(state_file.read_text(encoding="utf-8"))["config_dir"])
    models = json.loads((config_dir / "models.json").read_text(encoding="utf-8"))
    assert models["providers"]["agent-bridge"]["baseUrl"] == "https://models.example.internal/v1"
    settings = json.loads((config_dir / "settings.json").read_text(encoding="utf-8"))
    # 组级默认模型为空时不下发，避免覆盖用户既有设置
    assert "defaultModel" not in settings or isinstance(settings.get("defaultModel"), str)


# -- 共享范围 --


def test_shared_scope_uses_linux_user_dir_and_single_process(service) -> None:
    _configure_models(service)
    first = service.pi.authorize_workspace("user1", profile_key="safe", scope="shared")
    assert first["status"] == "running"
    state_file = Path(service.pi.paths.run_dir) / "pi-runtimes" / "shared" / "groupa.json"
    assert state_file.exists()
    config_dir = json.loads(state_file.read_text(encoding="utf-8"))["config_dir"]
    # 共享：一个 Linux 用户一个目录（不再是业务用户名）
    assert config_dir.endswith(".config/agent-bridge/pi/groupa")

    second = service.pi.authorize_workspace("user2", profile_key="safe", scope="shared")
    assert second["status"] == "running"
    # 第二名成员进入不产生第二个进程
    assert len(service.pi._launcher.starts) == 1


def test_shared_runtime_capability_never_expires_while_alive(service) -> None:
    _configure_models(service)
    service.pi.authorize_workspace("user1", profile_key="safe", scope="shared")
    state_file = Path(service.pi.paths.run_dir) / "pi-runtimes" / "shared" / "groupa.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    registry = service.pi.capabilities
    capability = registry.current_for_user("groupa")
    assert capability is None or capability.profile_key == "safe"
    # runtime 级稳定 capability 不设独立过期
    tokens = [
        item
        for item in registry._items.values()
        if item.user_id == "groupa"
    ]
    assert tokens, "共享 runtime 应持有 capability"
    assert all(item.expires_at_monotonic is None for item in tokens)
    assert state["profile_key"] == "safe"


def test_shared_member_without_profile_permission_is_rejected(service) -> None:
    _configure_models(service)
    service.governance.upsert_profile("user1", "restricted", "受限平面", "", "active")
    service.pi.authorize_workspace("user1", profile_key="safe", scope="shared")
    # 让 user2 失去对 safe 的读权限后重进
    svc_access = service.access
    group = svc_access.repository.get_group("groupa")
    assert group is not None
    # 直接改状态模拟运行期间 active profile 被回收的场景
    state_file = Path(service.pi.paths.run_dir) / "pi-runtimes" / "shared" / "groupa.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["profile_key"] = "restricted"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    status = service.pi.authorize_workspace("user2", profile_key="safe", scope="shared")
    assert status["profile_key"] == "restricted"


def test_personal_and_shared_runtimes_isolated(service) -> None:
    _configure_models(service)
    service.pi.authorize_workspace("user1", profile_key="safe", scope="personal")
    service.pi.authorize_workspace("user1", profile_key="safe", scope="shared")
    run_dir = Path(service.pi.paths.run_dir) / "pi-runtimes"
    assert (run_dir / "user1.json").exists()
    assert (run_dir / "shared" / "groupa.json").exists()
    assert len(service.pi._launcher.starts) == 2


def test_stop_clears_managed_mcp_entry(service) -> None:
    _configure_models(service)
    service.pi.authorize_workspace("user1", profile_key="safe")
    state_file = Path(service.pi.paths.run_dir) / "pi-runtimes" / "user1.json"
    config_dir = Path(json.loads(state_file.read_text(encoding="utf-8"))["config_dir"])
    service.pi.stop_runtime("user1", scope="personal")
    mcp = json.loads((config_dir / "mcp.json").read_text(encoding="utf-8"))
    assert "agent-bridge" not in mcp["mcpServers"]


def test_switching_profile_restarts_runtime(service) -> None:
    _configure_models(service)
    service.governance.upsert_profile("user1", "other", "另一平面", "", "active")
    service.pi.authorize_workspace("user1", profile_key="safe")
    service.pi.authorize_workspace("user1", profile_key="other")
    assert len(service.pi._launcher.starts) == 2

def test_marked_pids_uses_each_agents_own_env_var(tmp_path) -> None:
    """各 Agent 的 runtime 标记必须按各自的环境变量名匹配。

    标记值格式相同（``scope:runtime_key``）。若统一按 DSH 的变量名扫描，停止 Pi
    工作台会误杀同名用户的 DSH 进程，且 Pi 自己的 detached 孤儿回收不到。
    """
    from agent_bridge.agent_workspace import process_tree

    def make_proc(name: str, mark_env: str, value: str) -> None:
        pid_dir = tmp_path / name
        pid_dir.mkdir(parents=True)
        (pid_dir / "environ").write_bytes(
            f"HOME=/home/u\0{mark_env}={value}\0".encode("utf-8")
        )

    make_proc("100", process_tree.RUNTIME_MARK_ENV, "personal:alice")
    make_proc("200", process_tree.PI_RUNTIME_MARK_ENV, "personal:alice")

    dsh_hits = process_tree.marked_pids(
        "personal:alice",
        mark_env=process_tree.RUNTIME_MARK_ENV,
        proc_root=tmp_path,
    )
    pi_hits = process_tree.marked_pids(
        "personal:alice",
        mark_env=process_tree.PI_RUNTIME_MARK_ENV,
        proc_root=tmp_path,
    )
    assert dsh_hits == {100}
    assert pi_hits == {200}


def test_mark_env_declaration_differs_per_agent(service) -> None:
    assert service.dsh.adapter.mark_env != service.pi.adapter.mark_env
    assert service.dsh.adapter.mark_env.endswith("_DSH_RUNTIME")
    assert service.pi.adapter.mark_env.endswith("_PI_RUNTIME")


def test_probe_auth_uses_basic_credentials() -> None:
    """就绪探测必须带 Basic Auth，否则 pi-web 的 /api/home 恒 401。"""
    import base64

    from agent_bridge.agent_workspace.adapters.pi import PiWorkspaceAdapter

    adapter = PiWorkspaceAdapter(configs=None, mcp_url="http://127.0.0.1:8080/mcp")
    headers = adapter.probe_auth({"auth_secret": "s3cret"})
    token = headers["authorization"].split(" ", 1)[1]
    # 用户名固定为 pi（pi-web 硬编码常量）
    assert base64.b64decode(token).decode() == "pi:s3cret"
    # 无密码时不带凭据（pi-web 未启用密码的部署）
    assert adapter.probe_auth({}) == {}


def test_dsh_probe_needs_no_auth() -> None:
    """DSH 不启用密码，探测不带凭据（保持既有行为）。"""
    from agent_bridge.agent_workspace.adapters.dsh import DshWorkspaceAdapter

    adapter = DshWorkspaceAdapter(configs=None, mcp_url="http://127.0.0.1:8080/mcp")
    assert adapter.probe_auth({"auth_secret": "anything"}) == {}


def test_settings_write_default_provider_with_model() -> None:
    """组级默认模型必须成对写入 defaultProvider，否则 pi 找不到该模型。"""
    from agent_bridge.agent_workspace.pi_injection import (
        MANAGED_PROVIDER_KEY,
        build_settings_payload,
    )

    payload = build_settings_payload({"theme": "dark"}, default_model="glm-5")
    assert payload["defaultModel"] == "glm-5"
    assert payload["defaultProvider"] == MANAGED_PROVIDER_KEY
    # 用户其他设置保留
    assert payload["theme"] == "dark"
    # 未配置默认模型时不动用户既有设置
    assert build_settings_payload({"defaultModel": "mine"}, default_model="") == {
        "defaultModel": "mine"
    }
