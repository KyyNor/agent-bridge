"""使用 OpenAI Chat 兼容接口提取检索短句。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .session_history import (
    ProbeHistoryEntry,
    ProbeSessionHistoryStoreProtocol,
    SESSION_HISTORY_PROMPT_ROUNDS,
)


logger = logging.getLogger(__name__)

_STOPWORDS = {"什么", "怎么", "如何", "一下", "请", "帮我", "的", "了", "和", "与", "是", "在", "有"}
_SYSTEM_PROMPT = """你是检索短句提取器。只输出 JSON 对象，格式严格为 {\"keywords\":[\"短句\"]}。
从用户问题中提取 0 到 8 个原始且高区分度的业务短句，优先保留报表名、业务专名、指标名和状态短语。
不要拆分中文专名，不要输出单字、泛化动词、解释、补造词或任何 JSON 之外的文字。"""
_KEYWORDS_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "keywords": {
            "type": "array",
            "minItems": 0,
            "maxItems": 8,
            "items": {"type": "string"},
        }
    },
    "required": ["keywords"],
}


class KeywordExtractionStatus(str, Enum):
    success = "success"
    not_configured = "not_configured"
    timeout = "timeout"
    unavailable = "unavailable"
    invalid_output = "invalid_output"


@dataclass(frozen=True)
class KeywordExtraction:
    status: KeywordExtractionStatus
    keywords: tuple[str, ...] = ()
    model: str = ""
    duration_ms: int = 0
    error_type: str | None = None
    history_rounds: int = 0
    filtered_keyword_count: int = 0


@runtime_checkable
class ProbeKeywordExtractor(Protocol):
    def extract(
        self,
        prompt: str,
        *,
        profile_key: str = "",
        session_id: str = "",
        max_keywords: int,
        timeout_seconds: float,
    ) -> KeywordExtraction: ...


class OpenAIChatProbeKeywordExtractor:
    def __init__(self, *, store: Any, history: ProbeSessionHistoryStoreProtocol | None = None) -> None:
        self.store = store
        self.history = history

    def extract(
        self,
        prompt: str,
        *,
        profile_key: str = "",
        session_id: str = "",
        max_keywords: int,
        timeout_seconds: float,
    ) -> KeywordExtraction:
        started = time.monotonic()
        config = self.store.get_retrieval_probe_llm_config()
        base_url = str(config.get("base_url") or "").strip().rstrip("/")
        api_key = str(config.get("api_key") or "").strip()
        model = str(config.get("model") or "").strip()
        if not base_url or not api_key or not model:
            return self._result(KeywordExtractionStatus.not_configured, model, started)
        history: tuple[ProbeHistoryEntry, ...] = ()
        if self.history:
            try:
                history = self.history.recent(profile_key, session_id, SESSION_HISTORY_PROMPT_ROUNDS)
            except Exception:
                logger.warning(
                    "检索探测会话历史读取失败 profile=%s session=%s",
                    profile_key,
                    session_id,
                    exc_info=True,
                )
        history_keywords = {
            _normalize_keyword(keyword)
            for entry in history
            for keyword in entry.keywords
        }
        context_prompt = _build_context_prompt(prompt, history)
        try:
            client = OpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=0,
            )
            completion = client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=160,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": context_prompt},
                ],
                extra_body={
                    "structured_outputs": {
                        "json_schema": _KEYWORDS_JSON_SCHEMA,
                    }
                },
            )
            content = str(completion.choices[0].message.content or "")
            parsed_keywords, provider_filtered_count = _parse_probe_keywords_with_stats(
                content,
                max_keywords=max_keywords,
            )
            keywords = tuple(
                keyword for keyword in parsed_keywords
                if _normalize_keyword(keyword) not in history_keywords
            )
        except APITimeoutError:
            return self._result(KeywordExtractionStatus.timeout, model, started, "sdk_timeout", len(history))
        except ValueError:
            return self._result(KeywordExtractionStatus.invalid_output, model, started, "invalid_output", len(history))
        except (APIConnectionError, APIStatusError, IndexError, TypeError):
            logger.warning("检索短句模型调用失败 model=%s", model, exc_info=True)
            return self._result(KeywordExtractionStatus.unavailable, model, started, "openai_sdk_error", len(history))
        result = KeywordExtraction(
            status=KeywordExtractionStatus.success,
            keywords=keywords,
            model=model,
            duration_ms=_elapsed_ms(started),
            history_rounds=len(history),
            filtered_keyword_count=provider_filtered_count + len(parsed_keywords) - len(keywords),
        )
        if self.history and session_id:
            try:
                self.history.append(profile_key, session_id, ProbeHistoryEntry(prompt, keywords, _utc_iso()))
            except Exception:
                logger.warning(
                    "检索探测会话历史写入失败 profile=%s session=%s keywords=%d",
                    profile_key,
                    session_id,
                    len(keywords),
                    exc_info=True,
                )
        return result

    @staticmethod
    def _result(
        status: KeywordExtractionStatus,
        model: str,
        started: float,
        error_type: str | None = None,
        history_rounds: int = 0,
    ) -> KeywordExtraction:
        return KeywordExtraction(
            status=status,
            model=model,
            duration_ms=_elapsed_ms(started),
            error_type=error_type,
            history_rounds=history_rounds,
        )


def parse_probe_keywords(content: str, *, max_keywords: int) -> tuple[str, ...]:
    return _parse_probe_keywords_with_stats(content, max_keywords=max_keywords)[0]


def _parse_probe_keywords_with_stats(
    content: str,
    *,
    max_keywords: int,
) -> tuple[tuple[str, ...], int]:
    if not 0 <= max_keywords <= 8:
        raise ValueError("max_keywords must be between 0 and 8")
    raw = json.loads(content)
    if not isinstance(raw, dict) or set(raw) != {"keywords"} or not isinstance(raw["keywords"], list):
        raise ValueError("invalid keyword payload")
    result: list[str] = []
    seen: set[str] = set()
    filtered_count = 0
    for item in raw["keywords"]:
        if not isinstance(item, str):
            raise ValueError("keyword must be string")
        keyword = item.strip()
        if not _is_valid_keyword(keyword):
            filtered_count += 1
            continue
        key = _normalize_keyword(keyword)
        if key not in seen:
            seen.add(key)
            result.append(keyword)
        else:
            filtered_count += 1
    if len(result) > max_keywords:
        raise ValueError("invalid keyword count")
    return tuple(result), filtered_count


def _is_valid_keyword(value: str) -> bool:
    if not value or len(value) > 120 or value in _STOPWORDS:
        return False
    has_chinese = any("\u3400" <= char <= "\u9fff" for char in value)
    if has_chinese:
        return len(value) >= 2
    return len(value) >= 3


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _normalize_keyword(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _build_context_prompt(prompt: str, history: tuple[ProbeHistoryEntry, ...]) -> str:
    sections = ["以下是此前会话的历史，仅用于避免重复提取："]
    for index, entry in enumerate(history, start=1):
        sections.append(f"历史第 {index} 轮提示词：{entry.prompt}")
        sections.append(f"历史第 {index} 轮提取结果：{json.dumps(list(entry.keywords), ensure_ascii=False)}")
    sections.append(f"本轮提示词：{prompt}")
    sections.append("只返回本轮新增短句；如无新增，返回 {\"keywords\":[]}")
    return "\n".join(sections)


def _utc_iso() -> str:
    from agent_bridge.core.timeutil import utc_iso

    return utc_iso()
