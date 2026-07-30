"""可编辑资源的通用乐观并发控制。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from agent_bridge.core.domain import ConflictError

logger = logging.getLogger(__name__)


def edit_token(snapshot: Any) -> str:
    """为服务端当前可编辑快照生成不泄露内容的稳定令牌。

    空字符串专门表示资源不存在，供“新建”表单防止同 key 并发占用。
    """

    if snapshot is None:
        return ""
    serialized = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def attach_edit_token(payload: dict[str, Any], snapshot: Any) -> dict[str, Any]:
    result = dict(payload)
    result["edit_token"] = edit_token(snapshot)
    return result


def require_edit_token(
    expected: str | None,
    current_snapshot: Any,
    *,
    resource_type: str,
    resource_key: str,
    actor: str,
) -> None:
    """校验客户端读取的版本；未携带令牌时兼容旧客户端。"""

    if expected is None:
        return
    current = edit_token(current_snapshot)
    if hmac.compare_digest(expected, current):
        return
    logger.warning(
        "保存被拒绝：编辑版本冲突 resource_type=%s resource_key=%s actor=%s expected=%s current=%s",
        resource_type,
        resource_key,
        actor,
        expected,
        current,
    )
    raise ConflictError("内容已在其他页面更新或目标标识已被占用，请刷新后重新编辑")
