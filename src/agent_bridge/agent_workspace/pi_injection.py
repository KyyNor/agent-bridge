"""Pi 工作台的托管配置注入：``models.json`` / ``settings.json`` / ``mcp.json``。

pi-coding-agent 在 ``PI_CODING_AGENT_DIR`` 下读取这三个文件。Agent Bridge 只
**合并托管条目**，绝不覆盖用户自有配置：

- ``models.json``：合并 ``providers.agent-bridge``（公共 Base URL + 可用模型），
  保留用户自有 provider；
- ``settings.json``：写入组级 ``defaultModel``，保留其余设置；
- ``mcp.json``：只写 ``agent-bridge`` 这一个 HTTP server，capability 经
  ``${AGENT_BRIDGE_PI_CAPABILITY}`` 由环境变量展开——**token 不落盘**。不选
  能力平面时移除该条目。

API Key 同样只经环境变量传递，不写入任何文件。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_bridge.agent_workspace import safe_files
from agent_bridge.agent_workspace.capability import WorkspaceSelection
from agent_bridge.agent_workspace.launcher import LinuxIdentity

logger = logging.getLogger(__name__)

# 复用通用安全读写（不跟随软链）：本模块以 root 身份在用户可控目录下工作，
# 固定名临时文件与软链都可能造成任意文件覆盖或数据外泄。
_read_json = safe_files.read_managed_json
write_managed_json = safe_files.write_managed_json_atomic
ensure_owner = safe_files.ensure_owner

# 托管条目标识；与用户自有条目按 key 区分，合并时只覆盖同 key。
MANAGED_PROVIDER_KEY = "agent-bridge"
MANAGED_MCP_SERVER_KEY = "agent-bridge"
PI_CAPABILITY_ENV = "AGENT_BRIDGE_PI_CAPABILITY"
PI_API_KEY_ENV = "AGENT_BRIDGE_PI_API_KEY"
# MCP 工具调用超时（毫秒），与 Agent Bridge HTTP MCP server 对齐。
MCP_TOOL_TIMEOUT_MS = 300_000

MODELS_FILENAME = "models.json"
SETTINGS_FILENAME = "settings.json"
MCP_FILENAME = "mcp.json"


# -- models.json --


def build_models_payload(
    existing: dict[str, Any], *, base_url: str, models: list[str], api_key_env: str
) -> dict[str, Any]:
    """合并托管 provider：``providers.agent-bridge``，保留用户自有 provider。"""
    payload = dict(existing)
    providers = dict(payload.get("providers") or {})
    if not base_url or not models:
        # 未配置接入时移除托管条目，避免留下指向空地址的死 provider。
        providers.pop(MANAGED_PROVIDER_KEY, None)
    else:
        providers[MANAGED_PROVIDER_KEY] = {
            "baseUrl": base_url,
            "api": "openai-completions",
            # API Key 只经环境变量展开，不写入文件。
            "apiKey": "${" + api_key_env + "}",
            "models": [
                {"id": name, "name": name} for name in models
            ],
        }
    payload["providers"] = providers
    return payload


def write_models(
    config_dir: Path, *, uid: int, gid: int, base_url: str, models: list[str]
) -> Path:
    """写入 ``models.json``；返回文件路径。"""
    path = config_dir / MODELS_FILENAME
    payload = build_models_payload(
        _read_json(path),
        base_url=base_url,
        models=models,
        api_key_env=PI_API_KEY_ENV,
    )
    write_managed_json(path, payload, uid=uid, gid=gid, label="Pi models")
    return path


# -- settings.json --


def build_settings_payload(existing: dict[str, Any], *, default_model: str) -> dict[str, Any]:
    """写入组级默认模型，保留其余用户设置。

    pi 解析默认模型需要 ``defaultProvider`` + ``defaultModel`` **成对**（见
    ``pi-coding-agent`` 的 ``findInitialModel``：先按 CLI 参数，再按 session，
    再读 settings 的 defaultProvider/defaultModel）。只写 ``defaultModel`` 时，
    provider 会回落到用户上一次的值或 pi 自动选择的 provider，在那里找不到
    该模型就会报错或被替换——组级默认模型将实际不生效。
    """
    payload = dict(existing)
    if default_model:
        payload["defaultModel"] = default_model
        payload["defaultProvider"] = MANAGED_PROVIDER_KEY
    return payload


def write_settings(
    config_dir: Path, *, uid: int, gid: int, default_model: str
) -> Path:
    """写入 ``settings.json``；返回文件路径。"""
    path = config_dir / SETTINGS_FILENAME
    payload = build_settings_payload(
        _read_json(path), default_model=default_model
    )
    write_managed_json(path, payload, uid=uid, gid=gid, label="Pi settings")
    return path


# -- mcp.json --


def build_mcp_servers_payload(
    existing: dict[str, Any], *, mcp_url: str, profile_key: str | None, capability_token: str | None
) -> dict[str, Any]:
    """只维护 ``agent-bridge`` 这一个 server，保留用户自有 server。

    不选能力平面（``profile_key`` 为空）或缺少 token 时移除托管条目，pi 中
    不再出现 Agent Bridge 工具。
    """
    servers = dict(existing.get("mcpServers") or {})
    if not profile_key or not capability_token:
        servers.pop(MANAGED_MCP_SERVER_KEY, None)
    else:
        servers[MANAGED_MCP_SERVER_KEY] = {
            "url": mcp_url,
            "headers": {
                "X-Agent-Bridge-MetaMCP-Profile": profile_key,
                # token 经环境变量展开：磁盘上只有变量名，没有 token 明文。
                "X-Agent-Bridge-DSH-Capability": "${" + PI_CAPABILITY_ENV + "}",
            },
            "timeout": MCP_TOOL_TIMEOUT_MS // 1000,
            "exposure": "direct",
        }
    payload = dict(existing)
    payload["mcpServers"] = servers
    return payload


def write_mcp_servers(
    config_dir: Path,
    *,
    uid: int,
    gid: int,
    mcp_url: str,
    profile_key: str | None,
    capability_token: str | None,
) -> Path:
    """写入 ``mcp.json``；返回文件路径。"""
    path = config_dir / MCP_FILENAME
    payload = build_mcp_servers_payload(
        _read_json(path),
        mcp_url=mcp_url,
        profile_key=profile_key,
        capability_token=capability_token,
    )
    write_managed_json(path, payload, uid=uid, gid=gid, label="Pi mcp")
    return path


def remove_managed_mcp_server(config_dir: Path, *, uid: int, gid: int) -> None:
    """移除托管 MCP 条目（不选能力平面 / 停止共享 runtime 时调用）。"""
    path = config_dir / MCP_FILENAME
    if not path.exists():
        return
    payload = write_mcp_servers(
        config_dir,
        uid=uid,
        gid=gid,
        mcp_url="",
        profile_key=None,
        capability_token=None,
    )
    logger.info("Pi 能力平面注入已移除 path=%s", payload)