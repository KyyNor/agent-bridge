"""已耐久 Agent 运行事件的进程内发布与订阅。

JSONL 仍是事件的可靠重放来源；此模块仅把已经写入 JSONL 的新事件低延迟分发给同一
服务进程中的 SSE 订阅者。当前 server runtime 只启动一个 uvicorn worker，因此不需要
跨进程 broker。若未来改为多 worker/多实例，必须替换这个实现。
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LiveAgentRunMessage:
    """一个要交给 SSE 路由的运行期消息。"""

    kind: str
    payload: dict[str, Any]


class LiveAgentRunSubscription:
    """单个 SSE 连接的有界消息队列。"""

    def __init__(self, run_key: str, queue: asyncio.Queue[LiveAgentRunMessage]) -> None:
        self.run_key = run_key
        self._queue = queue

    async def receive(self) -> LiveAgentRunMessage:
        return await self._queue.get()


class LiveAgentRunEventHub:
    """按 run_key 分发已持久化事件，慢订阅者显式要求重新同步。"""

    def __init__(self, max_queue_size: int = 256) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: dict[str, set[asyncio.Queue[LiveAgentRunMessage]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, run_key: str) -> LiveAgentRunSubscription:
        queue: asyncio.Queue[LiveAgentRunMessage] = asyncio.Queue(maxsize=self._max_queue_size)
        with self._lock:
            self._subscribers.setdefault(run_key, set()).add(queue)
        return LiveAgentRunSubscription(run_key, queue)

    def unsubscribe(self, subscription: LiveAgentRunSubscription) -> None:
        with self._lock:
            queues = self._subscribers.get(subscription.run_key)
            if queues is None:
                return
            queues.discard(subscription._queue)
            if not queues:
                self._subscribers.pop(subscription.run_key, None)

    def publish_event(self, run_key: str, event: dict[str, Any]) -> None:
        self._publish(run_key, LiveAgentRunMessage("agent_event", event))

    def publish_terminal(self, run_key: str, payload: dict[str, Any]) -> None:
        self._publish(run_key, LiveAgentRunMessage("run_terminal", payload))

    def _publish(self, run_key: str, message: LiveAgentRunMessage) -> None:
        with self._lock:
            queues = list(self._subscribers.get(run_key, ()))
        for queue in queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # 不再继续积压旧事件。客户端拿到此信号后会走 REST 快照再重连。
                self._replace_with_resync(queue)

    @staticmethod
    def _replace_with_resync(queue: asyncio.Queue[LiveAgentRunMessage]) -> None:
        try:
            while True:
                queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(LiveAgentRunMessage("resync_required", {"reason": "subscriber_queue_overflow"}))
        except asyncio.QueueFull:
            # 队列刚被清空，理论上不会到这里；保留防御分支以免发布路径抛异常。
            pass
