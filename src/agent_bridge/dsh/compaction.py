"""将压缩预算合并进实际生效的 DSH 压缩器，包括 preset 内的隔离实例。"""

from copy import deepcopy
import logging
import math
from typing import Any

from agent_bridge.agent_workspace.compaction import compaction_reserve
from agent_bridge.core.domain import ValidationError


logger = logging.getLogger(__name__)


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
        if row.get("name") == "@deepseek-ai/dsh-compaction-basic":
            config = row.setdefault("config", {})
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
            changed = True
        config = row.get("config")
        children = config if row.get("group") and isinstance(config, list) else []
        if row.get("name") == "@deepseek-ai/dsh-agent-preset" and isinstance(config, dict):
            children = config.get("plugins", [])
        for child in children:
            if update(child):
                changed = True
        return changed

    for entry in entries:
        row = deepcopy(entry)
        if update(row):
            patches.append({"id": row["id"], "config": row["config"]})
    if not patches:
        raise ValidationError("DSH 压缩配置注入失败：生效配置中没有可用压缩器，请检查 profile/preset")
    return patches
