"""将压缩预算合并进实际生效的 DSH 压缩器，包括 preset 内的隔离实例。"""

from collections.abc import Iterator
from copy import deepcopy
import logging
import math
from typing import Any

from agent_bridge.agent_workspace.compaction import compaction_reserve
from agent_bridge.core.domain import ValidationError


logger = logging.getLogger(__name__)

_COMPRESSOR = "@deepseek-ai/dsh-compaction-basic"
_DECLARED_PRESET = "@deepseek-ai/dsh-agent-preset"
_PRESET_REGISTRY = "@deepseek-ai/dsh-agent-preset-registry"
_TUI = "@deepseek-harness-tui/dsh-tui"
_TUI_PRESET_PATCH = f"{_TUI}/preset-patch"
# 锁定版本的 TUI 在运行期注册这些 preset。其内置压缩器均使用插件默认配置。
_TUI_BUILTIN_PRESETS = ("standard", "ptc", "minimal", "cordis", "liangshen")


def _merge_model_policies(config: dict[str, Any], models: list[str], policy: dict[str, Any]) -> None:
    policies = config.setdefault("modelPolicies", [])
    for model in models:
        current = next(
            (p for p in policies if p.get("provider") == "agent-bridge" and p.get("model") == model),
            None,
        )
        if current is None:
            current = {"provider": "agent-bridge", "model": model}
            policies.append(current)
        current.update(policy)


def _active_profile_rows(entries: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for row in entries:
        # !!js 条件由 DSH 在运行期求值。TUI 的注册器用它检查包是否可用。
        if row.get("disabled") is True:
            continue
        yield row
        config = row.get("config")
        if row.get("group") and isinstance(config, list):
            yield from _active_profile_rows(config)


def _tui_preset_patches(
    entries: list[dict[str, Any]], models: list[str], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    """通过 TUI 的公开扩展点配置在 dump 之后注册的内置 preset。"""
    rows = list(_active_profile_rows(entries))
    names = {row.get("name") for row in rows}
    if not {_TUI, _PRESET_REGISTRY} <= names:
        return []
    declared: set[str] = set()
    overrides: dict[str, Any] = {}
    for row in rows:
        config = row.get("config")
        if not isinstance(config, dict):
            continue
        if row.get("name") == _DECLARED_PRESET and isinstance(config.get("id"), str):
            declared.add(config["id"])
        if row.get("name") == _TUI_PRESET_PATCH:
            preset = config.get("preset")
            targets = config.get("config")
            if isinstance(preset, str) and isinstance(targets, dict) and "compaction-basic" in targets:
                overrides[preset] = targets["compaction-basic"]

    result = []
    for preset in _TUI_BUILTIN_PRESETS:
        # TUI 跳过已有声明的同名 preset，调用方已直接合并这些声明。
        if preset in declared:
            continue
        config = deepcopy(overrides.get(preset, {}))
        if not isinstance(config, dict):
            raise ValidationError(f"DSH TUI 压缩配置注入失败：{preset} 的压缩器配置必须是映射")
        _merge_model_policies(config, models, policy)
        result.append({
            "id": f"agent-bridge-compaction-{preset}",
            "name": _TUI_PRESET_PATCH,
            "config": {"preset": preset, "config": {"compaction-basic": config}},
        })
    return result


def build_compaction_overlay(
    entries: list[dict[str, Any]], *, models: list[str], context_window: int, max_tokens: int
) -> list[dict[str, Any]]:
    reserve = compaction_reserve(context_window)
    if not reserve:
        return []
    if max_tokens > reserve:
        logger.warning(
            "DSH 输出上限超过压缩总预算，将提前压缩 context_window=%s max_tokens=%s reserve=%s",
            context_window, max_tokens, reserve,
        )
    # DSH 只把显式模型 maxTokens 作为默认输出预留；未声明时为 0。
    policy = {
        "thresholdRatio": math.nextafter((context_window - reserve) / context_window, math.inf),
        "headroomTokens": max(0, reserve - max_tokens),
        # 摘要上限独立声明，避免 headroom=0 导致插件拒绝加载。
        "maxTokens": min(reserve, 16_384),
    }
    patches: list[dict[str, Any]] = []

    def update(row: dict[str, Any]) -> bool:
        if row.get("disabled"):
            return False
        changed = False
        if row.get("name") == _COMPRESSOR:
            _merge_model_policies(row.setdefault("config", {}), models, policy)
            changed = True
        config = row.get("config")
        children = config if row.get("group") and isinstance(config, list) else []
        if row.get("name") == _DECLARED_PRESET and isinstance(config, dict):
            children = config.get("plugins", [])
        for child in children:
            if update(child):
                changed = True
        return changed

    for entry in entries:
        row = deepcopy(entry)
        if update(row):
            patches.append({"id": row["id"], "config": row["config"]})
    tui_patches = _tui_preset_patches(entries, models, policy)
    if tui_patches:
        patches.append({"insert": tui_patches})
    if not patches:
        raise ValidationError("DSH 压缩配置注入失败：生效配置中没有可用压缩器，请检查 profile/preset")
    return patches
