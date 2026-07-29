import pytest

from agent_bridge.knowledge_management.retrieval_probe.extractor import (
    KeywordExtractionStatus,
    OpenAIChatProbeKeywordExtractor,
    parse_probe_keywords,
)
from agent_bridge.knowledge_management.retrieval_probe.session_history import (
    ProbeHistoryEntry,
    ProbeSessionHistoryStore,
)
from agent_bridge.core.timeutil import utc_iso
from agent_bridge.storage.sqlite import SQLiteStore


def _store(wm_paths) -> SQLiteStore:
    store = SQLiteStore(wm_paths.db_path)
    store.init_schema()
    store.save_retrieval_probe_llm_config(
        base_url="https://llm.test/v1",
        model="small",
        api_key="secret",
        clear_api_key=False,
    )
    return store


def test_openai_extractor_returns_business_phrases(monkeypatch, wm_paths) -> None:
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return type("Completion", (), {
                "choices": [type("Choice", (), {
                    "message": type("Message", (), {
                        "content": '{"keywords":["新开发对公基础客户明细","当年新开未提升"]}'
                    })()
                })()]
            })()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    def fake_openai(**kwargs):
        captured["client"] = kwargs
        return FakeClient()

    monkeypatch.setattr(
        "agent_bridge.knowledge_management.retrieval_probe.extractor.OpenAI",
        fake_openai,
    )
    result = OpenAIChatProbeKeywordExtractor(store=_store(wm_paths)).extract(
        "新开发对公基础客户明细中‘当年新开未提升’中的‘提升’指的是什么",
        max_keywords=8,
        timeout_seconds=10,
    )
    assert result.status is KeywordExtractionStatus.success
    assert result.keywords == ("新开发对公基础客户明细", "当年新开未提升")
    assert captured["client"] == {
        "base_url": "https://llm.test/v1",
        "api_key": "secret",
        "timeout": 10,
        "max_retries": 0,
    }
    assert captured["request"]["extra_body"] == {
        "structured_outputs": {
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"keywords": {"type": "array", "minItems": 0, "maxItems": 8, "items": {"type": "string"}}},
                "required": ["keywords"],
            }
        }
    }


def test_empty_and_low_quality_output_is_filtered() -> None:
    assert parse_probe_keywords('{"keywords":[]}', max_keywords=8) == ()
    assert parse_probe_keywords('{"keywords":["一", "的", ""]}', max_keywords=8) == ()


@pytest.mark.parametrize("content", ["not json", '{"keywords":[1]}', '{"keywords":"新词"}'])
def test_structurally_invalid_output_is_rejected(content) -> None:
    with pytest.raises(ValueError):
        parse_probe_keywords(content, max_keywords=8)


def test_extractor_uses_latest_history_and_persists_filtered_result(monkeypatch, wm_paths) -> None:
    history = ProbeSessionHistoryStore(wm_paths.retrieval_probe_session_cache_dir)
    for index in range(4):
        history.append(
            "profile-a",
            "session-1",
            ProbeHistoryEntry(f"历史提示词 {index}", ("历史词",), utc_iso()),
        )
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return type("Completion", (), {
                "choices": [type("Choice", (), {
                    "message": type("Message", (), {
                        "content": '{"keywords":["历史词","新词"]}'
                    })()
                })()]
            })()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(
        "agent_bridge.knowledge_management.retrieval_probe.extractor.OpenAI",
        lambda **kwargs: FakeClient(),
    )
    result = OpenAIChatProbeKeywordExtractor(
        store=_store(wm_paths),
        history=history,
    ).extract(
        "本轮提示词",
        profile_key="profile-a",
        session_id="session-1",
        max_keywords=8,
        timeout_seconds=10,
    )

    assert result.status is KeywordExtractionStatus.success
    assert result.keywords == ("新词",)
    assert result.history_rounds == 3
    assert result.filtered_keyword_count == 1
    assert "历史提示词 1" in captured["request"]["messages"][1]["content"]
    assert "历史提示词 0" not in captured["request"]["messages"][1]["content"]
    assert history.recent("profile-a", "session-1", 1)[0].keywords == ("新词",)
    history.close()


def test_extractor_reports_low_quality_filter_count(monkeypatch, wm_paths) -> None:
    class FakeCompletions:
        def create(self, **kwargs):
            return type("Completion", (), {
                "choices": [type("Choice", (), {
                    "message": type("Message", (), {
                        "content": '{"keywords":["一","有效短句","有效短句"]}'
                    })()
                })()]
            })()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(
        "agent_bridge.knowledge_management.retrieval_probe.extractor.OpenAI",
        lambda **kwargs: FakeClient(),
    )
    result = OpenAIChatProbeKeywordExtractor(store=_store(wm_paths)).extract(
        "问题",
        max_keywords=8,
        timeout_seconds=10,
    )
    assert result.keywords == ("有效短句",)
    assert result.filtered_keyword_count == 2
