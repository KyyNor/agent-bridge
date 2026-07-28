"""使用 OpenAI Chat 兼容接口提取检索短句。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import httpx


logger = logging.getLogger(__name__)

_STOPWORDS = {"什么", "怎么", "如何", "一下", "请", "帮我", "的", "了", "和", "与", "是", "在", "有"}
_SYSTEM_PROMPT = """你是检索短句提取器。只输出 JSON 对象，格式严格为 {\"keywords\":[\"短句\"]}。
从用户问题中提取 2 到 8 个原始且高区分度的业务短句，优先保留报表名、业务专名、指标名和状态短语。
不要拆分中文专名，不要输出单字、泛化动词、解释、补造词或任何 JSON 之外的文字。"""


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


@runtime_checkable
class ProbeKeywordExtractor(Protocol):
    def extract(
        self, prompt: str, *, max_keywords: int, timeout_seconds: float
    ) -> KeywordExtraction: ...


class OpenAIChatProbeKeywordExtractor:
    def __init__(self, *, store: Any) -> None:
        self.store = store

    def extract(
        self, prompt: str, *, max_keywords: int, timeout_seconds: float
    ) -> KeywordExtraction:
        started = time.monotonic()
        config = self.store.get_retrieval_probe_llm_config()
        base_url = str(config.get("base_url") or "").strip().rstrip("/")
        api_key = str(config.get("api_key") or "").strip()
        model = str(config.get("model") or "").strip()
        if not base_url or not api_key or not model:
            return self._result(KeywordExtractionStatus.not_configured, model, started)
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 160,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            content = str(payload["choices"][0]["message"]["content"])
            keywords = parse_probe_keywords(content, max_keywords=max_keywords)
        except httpx.TimeoutException:
            return self._result(KeywordExtractionStatus.timeout, model, started, "http_timeout")
        except ValueError:
            return self._result(KeywordExtractionStatus.invalid_output, model, started, "invalid_output")
        except (httpx.HTTPError, KeyError, IndexError, TypeError):
            logger.warning("检索短句模型调用失败 model=%s", model, exc_info=True)
            return self._result(KeywordExtractionStatus.unavailable, model, started, "chat_completion_error")
        return KeywordExtraction(
            status=KeywordExtractionStatus.success,
            keywords=keywords,
            model=model,
            duration_ms=_elapsed_ms(started),
        )

    @staticmethod
    def _result(
        status: KeywordExtractionStatus,
        model: str,
        started: float,
        error_type: str | None = None,
    ) -> KeywordExtraction:
        return KeywordExtraction(
            status=status,
            model=model,
            duration_ms=_elapsed_ms(started),
            error_type=error_type,
        )


def parse_probe_keywords(content: str, *, max_keywords: int) -> tuple[str, ...]:
    if not 2 <= max_keywords <= 8:
        raise ValueError("max_keywords must be between 2 and 8")
    raw = json.loads(content)
    if not isinstance(raw, dict) or set(raw) != {"keywords"} or not isinstance(raw["keywords"], list):
        raise ValueError("invalid keyword payload")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw["keywords"]:
        if not isinstance(item, str):
            raise ValueError("keyword must be string")
        keyword = item.strip()
        if not _is_valid_keyword(keyword):
            raise ValueError("invalid keyword")
        key = keyword.casefold()
        if key not in seen:
            seen.add(key)
            result.append(keyword)
    if not 2 <= len(result) <= max_keywords:
        raise ValueError("invalid keyword count")
    return tuple(result)


def _is_valid_keyword(value: str) -> bool:
    if not value or len(value) > 120 or value in _STOPWORDS:
        return False
    has_chinese = any("\u3400" <= char <= "\u9fff" for char in value)
    if has_chinese:
        return len(value) >= 2
    return len(value) >= 3


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
