import httpx
import pytest

from agent_bridge.knowledge_management.retrieval_probe.extractor import (
    KeywordExtractionStatus,
    OpenAIChatProbeKeywordExtractor,
    parse_probe_keywords,
)
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


def test_openai_extractor_returns_business_phrases(respx_mock, wm_paths) -> None:
    route = respx_mock.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": '{"keywords":["新开发对公基础客户明细","当年新开未提升"]}'}}]
        })
    )
    result = OpenAIChatProbeKeywordExtractor(store=_store(wm_paths)).extract(
        "新开发对公基础客户明细中‘当年新开未提升’中的‘提升’指的是什么",
        max_keywords=8,
        timeout_seconds=10,
    )
    assert result.status is KeywordExtractionStatus.success
    assert result.keywords == ("新开发对公基础客户明细", "当年新开未提升")
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret"


@pytest.mark.parametrize("content", ["not json", '{"keywords":["只有一个"]}', '{"keywords":[]}'])
def test_invalid_output_is_rejected(content) -> None:
    with pytest.raises(ValueError):
        parse_probe_keywords(content, max_keywords=8)
