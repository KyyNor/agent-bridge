import time

from agent_bridge.core.timeutil import utc_iso
from agent_bridge.knowledge_management.retrieval_probe.session_history import (
    ProbeHistoryEntry,
    ProbeSessionHistoryStore,
    SESSION_HISTORY_RETAINED_ROUNDS,
    SESSION_HISTORY_TTL_SECONDS,
)


def test_history_is_profile_scoped_and_keeps_recent_twelve(tmp_path) -> None:
    store = ProbeSessionHistoryStore(tmp_path)
    for index in range(13):
        store.append("profile-a", "session-1", ProbeHistoryEntry(f"p{index}", (), utc_iso()))
    assert [item.prompt for item in store.recent("profile-a", "session-1", 3)] == ["p10", "p11", "p12"]
    assert store.recent("profile-b", "session-1", 3) == ()
    store.close()


def test_empty_keywords_are_persisted_but_missing_session_is_not(tmp_path) -> None:
    store = ProbeSessionHistoryStore(tmp_path)
    store.append("profile-a", "session-1", ProbeHistoryEntry("p", (), utc_iso()))
    store.append("profile-a", "", ProbeHistoryEntry("ignored", (), utc_iso()))
    assert store.recent("profile-a", "session-1", 3)[0].keywords == ()
    assert store.recent("profile-a", "", 3) == ()
    store.close()


def test_append_uses_sliding_ttl_and_retains_configured_window(tmp_path) -> None:
    store = ProbeSessionHistoryStore(tmp_path)
    for index in range(SESSION_HISTORY_RETAINED_ROUNDS + 1):
        store.append("profile-a", "session-1", ProbeHistoryEntry(f"p{index}", (), utc_iso()))
    entries = store.recent("profile-a", "session-1", SESSION_HISTORY_RETAINED_ROUNDS + 1)
    assert len(entries) == SESSION_HISTORY_RETAINED_ROUNDS
    value, expires_at = store._cache.get(store._key("profile-a", "session-1"), expire_time=True)
    assert isinstance(value, list)
    assert expires_at >= time.time() + SESSION_HISTORY_TTL_SECONDS - 2
    store.close()
