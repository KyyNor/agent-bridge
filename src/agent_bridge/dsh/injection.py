"""把 Agent Bridge 的公共/组级模型配置注入 DSH 原生配置。

DSH（DeepSeek Harness）读取 ``$DSH_HOME/settings.yaml`` 决定可用供应商、
模型与默认模型：``llm-pi-ai.providers.<key>`` 声明 Base URL、模型目录和
承载 API Key 的环境变量名（``apiKeyEnv``），``agent-default-model`` 指定
默认供应商与模型。API Key 本身不写入文件，只通过进程环境变量传递。

本模块只管理 ``agent-bridge`` 这一个供应商条目与默认模型键，其余用户配置
（主题、onboarding 等）原样保留；写入结果与当前内容一致时不触盘。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.yaml"
# Agent Bridge 托管的供应商条目名（同时也是 DSH 侧 provider 标识）。
MANAGED_PROVIDER_KEY = "agent-bridge"
MANAGED_PROVIDER_DISPLAY_NAME = "Agent Bridge"
# 承载组级 API Key 的环境变量名；值由 runtime 服务在启动进程时注入。
MANAGED_API_KEY_ENV = "AGENT_BRIDGE_DSH_API_KEY"
# OpenAI 兼容网关使用的 wire protocol（DSH 的 llm-pi-ai ``api`` 字段）。
MANAGED_WIRE_PROTOCOL = "openai-completions"
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def settings_path(dsh_home: Path) -> Path:
    return Path(dsh_home) / SETTINGS_FILENAME


def build_provider_entry(*, base_url: str, models: list[str]) -> dict[str, Any]:
    """构造 DSH ``llm-pi-ai.providers`` 中的托管供应商条目。"""
    return {
        "displayName": MANAGED_PROVIDER_DISPLAY_NAME,
        "api": MANAGED_WIRE_PROTOCOL,
        "baseURL": base_url,
        "models": [{"id": model, "name": model} for model in models],
        "apiKeyEnv": MANAGED_API_KEY_ENV,
    }


def write_settings(
    dsh_home: Path,
    *,
    base_url: str,
    models: list[str],
    default_model: str,
) -> Path | None:
    """把托管供应商与默认模型合并进 ``$DSH_HOME/settings.yaml``。

    ``base_url`` 为空或模型列表为空时不写托管条目（避免生成不可用供应商），
    仅保留既有用户配置。返回写入路径；内容未变化或无需托管时返回 ``None``。
    """
    path = settings_path(dsh_home)
    existing = _read_settings(path)
    updated = dict(existing)

    if base_url and models:
        providers_section = dict(_mapping(updated.get("llm-pi-ai")).get("providers") or {})
        providers_section[MANAGED_PROVIDER_KEY] = build_provider_entry(base_url=base_url, models=models)
        llm_section = dict(_mapping(updated.get("llm-pi-ai")))
        llm_section["providers"] = providers_section
        updated["llm-pi-ai"] = llm_section
        if default_model and default_model in models:
            default_section = dict(_mapping(updated.get("agent-default-model")))
            default_section["provider"] = MANAGED_PROVIDER_KEY
            default_section["model"] = default_model
            updated["agent-default-model"] = default_section
    elif updated.get("llm-pi-ai") is None and updated.get("agent-default-model") is None:
        return None

    if updated == existing:
        return None
    dsh_home.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(updated, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    logger.info(
        "DSH settings.yaml 已更新 path=%s provider=%s models=%d default_model=%s",
        path,
        MANAGED_PROVIDER_KEY,
        len(models),
        default_model or "-",
    )
    return path


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("DSH settings.yaml 解析失败，将按空配置重写 path=%s", path, exc_info=True)
        return {}
    return payload if isinstance(payload, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def is_managed_provider_key(provider_key: str) -> bool:
    return bool(_PROVIDER_ID_RE.fullmatch(provider_key)) and provider_key == MANAGED_PROVIDER_KEY


def managed_api_key_env_value(api_key: str) -> dict[str, str]:
    """返回注入 DSH 进程的环境变量；未配置 API Key 时返回空字典。"""
    if not api_key:
        return {}
    return {MANAGED_API_KEY_ENV: api_key}


def ensure_owner(path: Path, *, uid: int, gid: int) -> None:
    """root 下把托管文件归属目标 Linux 用户（非 root 或同用户时跳过）。"""
    if os.geteuid() != 0 or uid == os.geteuid():
        return
    try:
        os.chown(path, uid, gid)
    except OSError as exc:
        logger.warning("DSH 托管文件 chown 失败 path=%s uid=%s 原因=%s", path, uid, exc)
