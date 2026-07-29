"""多来源检索探测编排服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from agent_bridge.capability_hub.models import CallLogStatus, SourceType
from agent_bridge.core.domain import NotFound, ValidationError
from agent_bridge.core.ids import new_run_id
from agent_bridge.hooks.claude_code import audit_claude_code_hook_call
from agent_bridge.knowledge_management.memory.models import NOOP_HOOK_STDOUT

from .adapters import RetrievalProbeAdapter
from .extractor import KeywordExtraction, KeywordExtractionStatus, ProbeKeywordExtractor
from .models import (
    KeywordProbeResult,
    ProbeResponse,
    ProbeStatus,
    ProbeTarget,
    TargetProbeSummary,
)
from .registry import RetrievalProbeRegistry
from .reminder import render_probe_reminder


logger = logging.getLogger(__name__)

_STATUS_PRIORITY = {
    ProbeStatus.not_configured: 0,
    ProbeStatus.no_hit: 1,
    ProbeStatus.unavailable: 2,
    ProbeStatus.timeout: 3,
    ProbeStatus.hit: 4,
}


@dataclass(frozen=True)
class _ProbeJob:
    adapter: RetrievalProbeAdapter
    target: ProbeTarget
    keyword: str


class RetrievalProbeService:
    def __init__(
        self,
        *,
        store: Any,
        registry: RetrievalProbeRegistry,
        governance: Any,
        keyword_extractor: ProbeKeywordExtractor,
        concurrency: int = 8,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self.store = store
        self.registry = registry
        self.governance = governance
        self.keyword_extractor = keyword_extractor
        self.concurrency = concurrency

    async def probe(
        self,
        *,
        actor: str,
        profile_key: str,
        prompt: str,
        session_id: str = "",
        keyword_limit: int = 8,
        result_limit: int = 3,
        timeout_seconds: float = 10.0,
        audit: bool = True,
    ) -> ProbeResponse:
        self._validate_request(
            profile_key=profile_key,
            prompt=prompt,
            keyword_limit=keyword_limit,
            result_limit=result_limit,
            timeout_seconds=timeout_seconds,
        )
        probe_id = new_run_id("probe")
        started = time.monotonic()
        deadline = started + timeout_seconds
        extraction = await asyncio.to_thread(
            self.keyword_extractor.extract,
            prompt,
            profile_key=profile_key,
            session_id=session_id,
            max_keywords=keyword_limit,
            timeout_seconds=min(10.0, max(0.0, deadline - time.monotonic())),
        )
        if extraction.status is not KeywordExtractionStatus.success:
            response = ProbeResponse(
                probe_id=probe_id,
                profile_key=profile_key,
                session_id=session_id,
                keywords=(),
                source_statuses={},
                targets=(),
                duration_ms=int((time.monotonic() - started) * 1000),
                keyword_extraction=extraction,
            )
            if audit:
                self._audit(actor=actor, profile_key=profile_key, prompt=prompt, response=response,
                            keyword_limit=keyword_limit, result_limit=result_limit, timeout_seconds=timeout_seconds)
            return response
        keywords = extraction.keywords
        if not keywords:
            response = ProbeResponse(
                probe_id=probe_id,
                profile_key=profile_key,
                session_id=session_id,
                keywords=(),
                source_statuses={},
                targets=(),
                duration_ms=int((time.monotonic() - started) * 1000),
                keyword_extraction=extraction,
            )
            if audit:
                self._audit(actor=actor, profile_key=profile_key, prompt=prompt, response=response,
                            keyword_limit=keyword_limit, result_limit=result_limit, timeout_seconds=timeout_seconds)
            return response
        logger.info(
            "全量检索探测开始 probe=%s profile=%s 关键词数=%d",
            probe_id,
            profile_key,
            len(keywords),
        )
        adapters = self.registry.list()
        targets_by_source, source_boot_status = await self._discover_targets(
            probe_id=probe_id,
            actor=actor,
            profile_key=profile_key,
            adapters=adapters,
            timeout_seconds=max(0.0, deadline - time.monotonic()),
        )
        target_order: list[ProbeTarget] = []
        jobs: list[_ProbeJob] = []
        for adapter in adapters:
            targets = targets_by_source.get(adapter.source_type, ())
            target_order.extend(targets)
            jobs.extend(
                _ProbeJob(adapter=adapter, target=target, keyword=keyword)
                for target in targets
                for keyword in keywords
            )

        results = await self._run_jobs(
            probe_id=probe_id,
            actor=actor,
            profile_key=profile_key,
            jobs=jobs,
            result_limit=result_limit,
            timeout_seconds=max(0.0, deadline - time.monotonic()),
        )
        summaries = self._summaries(target_order, results)
        source_statuses = self._source_statuses(
            adapters=adapters,
            summaries=summaries,
            boot_status=source_boot_status,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        response = ProbeResponse(
            probe_id=probe_id,
            profile_key=profile_key,
            session_id=session_id,
            keywords=keywords,
            source_statuses=source_statuses,
            targets=tuple(summaries),
            duration_ms=duration_ms,
            keyword_extraction=extraction,
        )
        if audit:
            self._audit(
                actor=actor,
                profile_key=profile_key,
                prompt=prompt,
                response=response,
                keyword_limit=keyword_limit,
                result_limit=result_limit,
                timeout_seconds=timeout_seconds,
            )
        logger.info(
            "全量检索探测完成 probe=%s profile=%s 目标数=%d 命中目标=%d 耗时=%dms",
            probe_id,
            profile_key,
            len(summaries),
            sum(item.status is ProbeStatus.hit for item in summaries),
            duration_ms,
        )
        return response

    async def handle_claude_code_hook(
        self,
        *,
        actor: str,
        profile_key: str,
        event_name: str | None,
        matcher: str | None,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """执行 full-probe 并返回 Claude Code 的标准 Hook 结果。"""
        started = time.monotonic()
        result: dict[str, Any] = {}
        try:
            response = await self.probe(
                actor=actor,
                profile_key=profile_key,
                prompt=str(payload.get("prompt") or ""),
                session_id=str(payload.get("session_id") or ""),
                timeout_seconds=float(timeout_seconds),
                audit=False,
            )
            reminder = render_probe_reminder(response)
            stdout = NOOP_HOOK_STDOUT
            if reminder:
                stdout = json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": event_name or "UserPromptSubmit",
                            "additionalContext": reminder,
                        }
                    },
                    ensure_ascii=False,
                )
            result = {"stdout": stdout, "stderr": "", "exit_code": 0, "status": "ok"}
        except Exception as exc:
            audit_claude_code_hook_call(
                self.governance,
                actor=actor,
                profile_key=profile_key,
                entrypoint="retrieval_probe_hook_claude_code",
                action="full-probe",
                event_name=event_name,
                matcher=matcher,
                payload=payload,
                timeout_seconds=timeout_seconds,
                result=result,
                duration_ms=int((time.monotonic() - started) * 1000),
                exception=exc,
            )
            raise
        audit_claude_code_hook_call(
            self.governance,
            actor=actor,
            profile_key=profile_key,
            entrypoint="retrieval_probe_hook_claude_code",
            action="full-probe",
            event_name=event_name,
            matcher=matcher,
            payload=payload,
            timeout_seconds=timeout_seconds,
            result=result,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    async def _discover_targets(
        self,
        *,
        probe_id: str,
        actor: str,
        profile_key: str,
        adapters: tuple[RetrievalProbeAdapter, ...],
        timeout_seconds: float,
    ) -> tuple[dict[str, tuple[ProbeTarget, ...]], dict[str, ProbeStatus]]:
        tasks = [
            asyncio.create_task(
                asyncio.to_thread(
                    adapter.list_targets,
                    actor=actor,
                    profile_key=profile_key,
                )
            )
            for adapter in adapters
        ]
        if not tasks:
            return {}, {}

        done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        task_adapter = {
            task: adapter
            for task, adapter in zip(tasks, adapters, strict=True)
        }
        targets_by_source: dict[str, tuple[ProbeTarget, ...]] = {}
        boot_status: dict[str, ProbeStatus] = {}
        for task in done:
            adapter = task_adapter[task]
            try:
                targets = tuple(task.result())
            except Exception as exc:
                logger.warning(
                    "检索探测资源枚举失败 probe=%s profile=%s source=%s 原因=%s",
                    probe_id,
                    profile_key,
                    adapter.source_type,
                    exc,
                    exc_info=True,
                )
                boot_status[adapter.source_type] = ProbeStatus.unavailable
                continue
            targets_by_source[adapter.source_type] = targets
            if not targets:
                boot_status[adapter.source_type] = ProbeStatus.not_configured

        for task in pending:
            adapter = task_adapter[task]
            boot_status[adapter.source_type] = ProbeStatus.timeout
            task.cancel()
            logger.warning(
                "检索探测资源枚举超时 probe=%s profile=%s source=%s",
                probe_id,
                profile_key,
                adapter.source_type,
            )
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return targets_by_source, boot_status

    def _validate_request(
        self,
        *,
        profile_key: str,
        prompt: str,
        keyword_limit: int,
        result_limit: int,
        timeout_seconds: float,
    ) -> None:
        if not profile_key.strip():
            raise ValidationError("profile_key is required")
        if not prompt.strip():
            raise ValidationError("prompt is required")
        if not 0 <= keyword_limit <= 8:
            raise ValidationError("keyword_limit must be between 0 and 8")
        if result_limit < 1:
            raise ValidationError("result_limit must be positive")
        if not 0 < timeout_seconds <= 20:
            raise ValidationError("timeout_seconds must be between 0 and 20")
        profile = self.store.get_project_profile(profile_key)
        if profile is None:
            raise NotFound("profile not found")
        if profile.get("status") != "active":
            raise ValidationError("profile is disabled")

    async def _run_jobs(
        self,
        *,
        probe_id: str,
        actor: str,
        profile_key: str,
        jobs: list[_ProbeJob],
        result_limit: int,
        timeout_seconds: float,
    ) -> list[KeywordProbeResult]:
        semaphore = asyncio.Semaphore(self.concurrency)
        tasks = [
            asyncio.create_task(
                self._run_job(
                    semaphore=semaphore,
                    probe_id=probe_id,
                    actor=actor,
                    profile_key=profile_key,
                    job=job,
                    result_limit=result_limit,
                )
            )
            for job in jobs
        ]
        if not tasks:
            return []
        done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        resolved: dict[int, KeywordProbeResult] = {}
        task_index = {task: index for index, task in enumerate(tasks)}
        for task in done:
            resolved[task_index[task]] = task.result()
        for task in pending:
            index = task_index[task]
            job = jobs[index]
            resolved[index] = KeywordProbeResult(
                target=job.target,
                keyword=job.keyword,
                status=ProbeStatus.timeout,
                error_type="probe_timeout",
                duration_ms=int(timeout_seconds * 1000),
            )
            task.cancel()
            logger.warning(
                "检索探测超时 probe=%s profile=%s source=%s resource=%s keyword=%s",
                probe_id,
                profile_key,
                job.target.source_type,
                job.target.resource_key,
                job.keyword,
            )
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return [resolved[index] for index in range(len(jobs))]

    async def _run_job(
        self,
        *,
        semaphore: asyncio.Semaphore,
        probe_id: str,
        actor: str,
        profile_key: str,
        job: _ProbeJob,
        result_limit: int,
    ) -> KeywordProbeResult:
        async with semaphore:
            started = time.monotonic()
            try:
                result = await asyncio.to_thread(
                    job.adapter.probe,
                    actor=actor,
                    profile_key=profile_key,
                    target=job.target,
                    keyword=job.keyword,
                    limit=result_limit,
                )
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "检索探测失败 probe=%s profile=%s source=%s resource=%s keyword=%s 原因=%s 耗时=%dms",
                    probe_id,
                    profile_key,
                    job.target.source_type,
                    job.target.resource_key,
                    job.keyword,
                    exc,
                    duration_ms,
                    exc_info=True,
                )
                return KeywordProbeResult(
                    target=job.target,
                    keyword=job.keyword,
                    status=ProbeStatus.unavailable,
                    error_type=type(exc).__name__,
                    duration_ms=duration_ms,
                )
            return result

    def _summaries(
        self,
        target_order: list[ProbeTarget],
        results: list[KeywordProbeResult],
    ) -> list[TargetProbeSummary]:
        by_target: dict[tuple[str, str], list[KeywordProbeResult]] = {}
        for result in results:
            key = (result.target.source_type, result.target.resource_key)
            by_target.setdefault(key, []).append(result)
        summaries: list[TargetProbeSummary] = []
        for target in target_order:
            keyword_results = tuple(
                by_target.get((target.source_type, target.resource_key), [])
            )
            candidate_keys = {
                candidate_key
                for result in keyword_results
                for candidate_key in result.candidate_keys
            }
            summaries.append(
                TargetProbeSummary(
                    target=target,
                    status=_aggregate_status(
                        [result.status for result in keyword_results]
                    ),
                    unique_hit_count=len(candidate_keys),
                    keyword_hits=keyword_results,
                )
            )
        return summaries

    def _source_statuses(
        self,
        *,
        adapters: tuple[RetrievalProbeAdapter, ...],
        summaries: list[TargetProbeSummary],
        boot_status: dict[str, ProbeStatus],
    ) -> dict[str, ProbeStatus]:
        statuses: dict[str, ProbeStatus] = {}
        for adapter in adapters:
            source_summaries = [
                summary
                for summary in summaries
                if summary.target.source_type == adapter.source_type
            ]
            if source_summaries:
                statuses[adapter.source_type] = _aggregate_status(
                    [summary.status for summary in source_summaries]
                )
            else:
                statuses[adapter.source_type] = boot_status.get(
                    adapter.source_type,
                    ProbeStatus.not_configured,
                )
        return statuses

    def _audit(
        self,
        *,
        actor: str,
        profile_key: str,
        prompt: str,
        response: ProbeResponse,
        keyword_limit: int,
        result_limit: int,
        timeout_seconds: float,
    ) -> None:
        try:
            self.governance.log_tool_call(
                actor=actor,
                profile_key=profile_key,
                entrypoint="retrieval_probe_api",
                source_type=SourceType.builtin.value,
                source_key="retrieval_probe",
                tool_name="retrieval-probe",
                request={
                    "prompt_length": len(prompt),
                    "prompt_sha256": hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest(),
                    "keywords": list(response.keywords),
                    "keyword_limit": keyword_limit,
                    "result_limit": result_limit,
                    "timeout_seconds": timeout_seconds,
                    "session_id": response.session_id,
                    "keyword_extraction": {
                        "status": response.keyword_extraction.status.value,
                        "model": response.keyword_extraction.model,
                        "duration_ms": response.keyword_extraction.duration_ms,
                        "error_type": response.keyword_extraction.error_type,
                        "history_rounds": response.keyword_extraction.history_rounds,
                        "filtered_keyword_count": response.keyword_extraction.filtered_keyword_count,
                    },
                },
                response={
                    "probe_id": response.probe_id,
                    "source_statuses": {
                        key: status.value
                        for key, status in response.source_statuses.items()
                    },
                    "target_count": len(response.targets),
                    "hit_target_count": sum(
                        target.status is ProbeStatus.hit
                        for target in response.targets
                    ),
                },
                status=CallLogStatus.success.value,
                error_message=None,
                duration_ms=response.duration_ms,
            )
        except Exception:
            logger.warning(
                "检索探测审计写入失败 probe=%s profile=%s",
                response.probe_id,
                profile_key,
                exc_info=True,
            )


def _aggregate_status(statuses: list[ProbeStatus]) -> ProbeStatus:
    if not statuses:
        return ProbeStatus.not_configured
    return max(statuses, key=_STATUS_PRIORITY.__getitem__)
