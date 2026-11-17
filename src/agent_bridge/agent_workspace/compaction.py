"""托管模型的压缩预算：总预留为窗口的 15%，至少 35,000 tokens。"""

from agent_bridge.core.domain import ValidationError


def compaction_reserve(context_window: int) -> int:
    """向上取整预留量；0 表示沿用上游，过小窗口明确拒绝。"""
    if context_window == 0:
        return 0
    reserve = max((context_window * 15 + 99) // 100, 35_000)
    if reserve >= context_window:
        raise ValidationError("上下文长度必须大于 35000 tokens，才能保留压缩预算；填 0 可沿用上游默认")
    return reserve
