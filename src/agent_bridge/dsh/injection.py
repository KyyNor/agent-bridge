"""把 Agent Bridge 的公共/组级模型配置经 ``--patch`` 覆盖层注入 DSH。

DSH（DeepSeek Harness）0.2 起移除了 ``$DSH_HOME/settings.yaml``：启动时只把
遗留文件一次性迁入当前 profile 的 ``cordis.patch.yml`` 并改名为
``settings.yaml.imported``。因此模型接入不再写该文件，而是在每次启动前生成
一份 loader patch，随启动命令以 ``--patch`` 叠加在 profile 层之后：

- ``llm-pi-ai`` 行声明 ``agent-bridge`` 供应商（Base URL、模型目录与承载
  API Key 的环境变量名 ``apiKeyEnv``）；
- ``agent-default-model`` 行指定默认供应商与模型。

DSH 的 patch 会**整体替换**目标行的 ``config``，而 ``llm-pi-ai`` 只允许一个
实例（再插入一行会因内置供应商重复声明而激活失败）。为保留用户在 DSH 内
自行配置的供应商，注入前先用 ``dsh --profile <p> --dump-config`` 读取该行
当前生效的配置，只合并 ``agent-bridge`` 供应商后再整体覆盖。读取失败时仅
注入托管供应商并告警——覆盖层不改写 profile 文件，用户配置不会丢失。

代价：DSH 的配置编辑器拒绝修改被命令行覆盖层接管的行，因此注入期间
``llm-pi-ai`` 与 ``agent-default-model`` 在 DSH 设置界面中只读；遗留
``settings.yaml`` 的同名段也会被 DSH 迁移拒绝、只留在
``settings.yaml.imported``，所以合并时同时读取该文件。

API Key 本身不写入文件，只通过进程环境变量传递。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from agent_bridge.agent_workspace.launcher import LinuxIdentity, demotion_kwargs

logger = logging.getLogger(__name__)

# Web Runtime 的模型覆盖文件（位于 DSH_HOME，随每次启动重写）。
MODEL_OVERLAY_FILENAME = "agent-bridge-model.patch.yml"
# DSH 0.2 之前的模型配置文件及其被 DSH 迁移后的改名；仅用于合并遗留供应商。
LEGACY_SETTINGS_FILENAMES = ("settings.yaml", "settings.yaml.imported")
LLM_ENTRY_ID = "llm-pi-ai"
DEFAULT_MODEL_ENTRY_ID = "agent-default-model"
# Agent Bridge 托管的供应商条目名（同时也是 DSH 侧 provider 标识）。
MANAGED_PROVIDER_KEY = "agent-bridge"
MANAGED_PROVIDER_DISPLAY_NAME = "Agent Bridge"
# 承载组级 API Key 的环境变量名；值由 runtime 服务在启动进程时注入。
MANAGED_API_KEY_ENV = "AGENT_BRIDGE_DSH_API_KEY"
# OpenAI 兼容网关使用的 wire protocol（DSH 的 llm-pi-ai ``api`` 字段）。
MANAGED_WIRE_PROTOCOL = "openai-completions"
# ``--dump-config`` 是纯配置组合，不启动插件；超时只防御异常挂起。
DUMP_CONFIG_TIMEOUT_SECONDS = 30.0
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class _Tagged:
    """保留 DSH entry-list 方言中的自定义标签（如 ``!!js`` 表达式）原样往返。"""

    __slots__ = ("tag", "value")

    def __init__(self, tag: str, value: Any) -> None:
        self.tag = tag
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Tagged) and (self.tag, self.value) == (other.tag, other.value)

    def __repr__(self) -> str:
        return f"_Tagged({self.tag!r}, {self.value!r})"


class _EntryListLoader(yaml.SafeLoader):
    pass


class _EntryListDumper(yaml.SafeDumper):
    pass


def _construct_tagged(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> _Tagged:
    tag = node.tag
    if isinstance(node, yaml.ScalarNode):
        return _Tagged(tag, loader.construct_scalar(node))
    if isinstance(node, yaml.SequenceNode):
        return _Tagged(tag, loader.construct_sequence(node, deep=True))
    return _Tagged(tag, loader.construct_mapping(node, deep=True))


def _represent_tagged(dumper: yaml.SafeDumper, data: _Tagged) -> yaml.Node:
    if isinstance(data.value, dict):
        return dumper.represent_mapping(data.tag, data.value)
    if isinstance(data.value, list):
        return dumper.represent_sequence(data.tag, data.value)
    return dumper.represent_scalar(data.tag, str(data.value))


# 未注册的标签（``tag:yaml.org,2002:js`` 与本地 ``!xxx``）一律按原样保留。
_EntryListLoader.add_multi_constructor("tag:yaml.org,2002:js", _construct_tagged)
_EntryListLoader.add_multi_constructor("!", _construct_tagged)
_EntryListDumper.add_representer(_Tagged, _represent_tagged)


def load_entry_list(text: str) -> Any:
    """按 DSH entry-list 方言解析 YAML（保留自定义标签）。"""
    return yaml.load(text, Loader=_EntryListLoader)  # noqa: S506 - 仅扩展了标签保留


def dump_entry_list(entries: list[dict[str, Any]]) -> str:
    return yaml.dump(entries, Dumper=_EntryListDumper, allow_unicode=True, sort_keys=False)


def model_overlay_path(dsh_home: Path) -> Path:
    return Path(dsh_home) / MODEL_OVERLAY_FILENAME


def build_provider_entry(*, base_url: str, models: list[str]) -> dict[str, Any]:
    """构造 DSH ``llm-pi-ai.providers`` 中的托管供应商条目。"""
    return {
        "displayName": MANAGED_PROVIDER_DISPLAY_NAME,
        "api": MANAGED_WIRE_PROTOCOL,
        "baseURL": base_url,
        "models": [{"id": model, "name": model} for model in models],
        "apiKeyEnv": MANAGED_API_KEY_ENV,
    }


def read_entry_config(
    *,
    dsh_binary: str,
    profile: str,
    entry_id: str,
    env: dict[str, str],
    cwd: Path,
    identity: LinuxIdentity,
) -> Any:
    """以目标 Linux 用户执行 ``dsh --profile <p> --dump-config``，返回指定行的 config。

    行不存在或未配置时返回 ``None``；命令失败、超时或输出无法解析时抛
    ``RuntimeError``，由调用方决定降级策略。
    """
    command = [dsh_binary, "--profile", profile, "--dump-config"]
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=DUMP_CONFIG_TIMEOUT_SECONDS,
            **demotion_kwargs(identity),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"执行 {' '.join(command)} 失败：{exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} 退出码 {result.returncode}：{(result.stderr or '')[-500:]}"
        )
    try:
        entries = load_entry_list(result.stdout)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"{' '.join(command)} 输出解析失败：{exc}") from exc
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("id") == entry_id:
            return entry.get("config")
    return None


def build_model_overlay(
    *,
    base_url: str,
    models: list[str],
    default_model: str,
    llm_config: Any = None,
    legacy_llm_config: Any = None,
) -> list[dict[str, Any]] | None:
    """构造模型接入的 ``--patch`` 条目；接入未配置（无 Base URL 或模型）时返回 ``None``。

    ``llm_config`` 是 ``llm-pi-ai`` 行当前生效的 config，``legacy_llm_config``
    是尚未被 DSH 迁移的 ``settings.yaml`` 中的同名段；两者的其余键与供应商
    保持不变，只覆盖 ``agent-bridge`` 供应商。
    """
    if not base_url or not models:
        return None
    merged: dict[str, Any] = {}
    providers: dict[str, Any] = {}
    for source in (legacy_llm_config, llm_config):
        section = _mapping(source)
        providers.update(_mapping(section.get("providers")))
        merged.update(section)
    providers[MANAGED_PROVIDER_KEY] = build_provider_entry(base_url=base_url, models=models)
    merged["providers"] = providers
    entries: list[dict[str, Any]] = [{"id": LLM_ENTRY_ID, "config": merged}]
    if default_model and default_model in models:
        entries.append(
            {
                "id": DEFAULT_MODEL_ENTRY_ID,
                "config": {"provider": MANAGED_PROVIDER_KEY, "model": default_model},
            }
        )
    return entries


def resolve_model_overlay(
    *,
    dsh_home: Path,
    dsh_binary: str,
    profile: str,
    base_url: str,
    models: list[str],
    default_model: str,
    env: dict[str, str],
    cwd: Path,
    identity: LinuxIdentity,
    config_reader: Any = None,
) -> list[dict[str, Any]] | None:
    """读取当前生效的 ``llm-pi-ai`` 配置并构造合并后的模型覆盖条目。"""
    if not base_url or not models:
        return None
    reader = config_reader or read_entry_config
    try:
        llm_config = reader(
            dsh_binary=dsh_binary,
            profile=profile,
            entry_id=LLM_ENTRY_ID,
            env=env,
            cwd=cwd,
            identity=identity,
        )
    except RuntimeError as exc:
        logger.warning(
            "DSH 当前供应商配置读取失败，本次仅注入托管供应商（profile 文件不受影响）"
            " dsh_home=%s profile=%s 原因=%s",
            dsh_home,
            profile,
            exc,
        )
        llm_config = None
    return build_model_overlay(
        base_url=base_url,
        models=models,
        default_model=default_model,
        llm_config=llm_config,
        legacy_llm_config=_legacy_llm_config(dsh_home),
    )


def write_overlay(path: Path, entries: list[dict[str, Any]]) -> Path:
    """写入覆盖文件；DSH 以目标 Linux 用户读取，权限收紧到 0600。内容未变化时不触盘。"""
    text = dump_entry_list(entries)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return path
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError as exc:
        logger.warning("DSH 模型覆盖文件 chmod 失败 path=%s 原因=%s", path, exc)
    return path


def remove_overlay(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("DSH 模型覆盖文件删除失败 path=%s 原因=%s", path, exc)


def _legacy_llm_config(dsh_home: Path) -> Any:
    """读取遗留 ``settings.yaml``（或 DSH 迁移后的 ``.imported``）中的 ``llm-pi-ai`` 段。

    覆盖层接管 ``llm-pi-ai`` 后，DSH 的迁移会拒绝写入该段，用户供应商只留在
    改名后的文件里；未迁移前则仍在原文件。两者都不存在时返回 ``None``。
    """
    for name in LEGACY_SETTINGS_FILENAMES:
        path = Path(dsh_home) / name
        if not path.exists():
            continue
        try:
            payload = load_entry_list(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            logger.warning("DSH 遗留 settings 解析失败，忽略其中的供应商 path=%s", path, exc_info=True)
            return None
        return _mapping(payload).get(LLM_ENTRY_ID)
    return None


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
