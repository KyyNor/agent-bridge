from __future__ import annotations

import asyncio
import threading

from agent_bridge.agent_runtime.live_events import LiveAgentRunEventHub


def test_live_event_hub_delivers_event_and_terminal() -> None:
    async def check() -> None:
        hub = LiveAgentRunEventHub()
        subscription = hub.subscribe("run_1")
        hub.publish_event("run_1", {"event_id": 1, "kind": "agent_message"})
        hub.publish_terminal("run_1", {"run_key": "run_1", "status": "completed", "ok": True})

        event = await subscription.receive()
        terminal = await subscription.receive()

        assert event.kind == "agent_event"
        assert event.payload["event_id"] == 1
        assert terminal.kind == "run_terminal"
        assert terminal.payload["status"] == "completed"
        hub.unsubscribe(subscription)

    asyncio.run(check())


def test_live_event_hub_marks_slow_subscriber_for_resync() -> None:
    async def check() -> None:
        hub = LiveAgentRunEventHub(max_queue_size=1)
        subscription = hub.subscribe("run_1")
        hub.publish_event("run_1", {"event_id": 1})
        hub.publish_event("run_1", {"event_id": 2})

        message = await subscription.receive()

        assert message.kind == "resync_required"
        assert message.payload["reason"] == "subscriber_queue_overflow"
        hub.unsubscribe(subscription)

    asyncio.run(check())


def test_live_event_hub_publishes_from_worker_thread() -> None:
    async def check() -> None:
        hub = LiveAgentRunEventHub()
        subscription = hub.subscribe("run_1")
        errors: list[BaseException] = []

        def publish() -> None:
            try:
                hub.publish_event("run_1", {"event_id": 1})
            except BaseException as exc:  # pragma: no cover - 断言后台线程不得泄漏异常
                errors.append(exc)

        thread = threading.Thread(target=publish)
        thread.start()
        thread.join()

        message = await asyncio.wait_for(subscription.receive(), timeout=1)

        assert errors == []
        assert message.kind == "agent_event"
        assert message.payload["event_id"] == 1
        hub.unsubscribe(subscription)

    asyncio.run(check(), debug=True)
