"""记忆后端契约与注册表。claude-mem 的进程服务保持兼容。"""
from __future__ import annotations

import logging
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from agent_bridge.core.domain import ValidationError, NotFound

logger = logging.getLogger(__name__)


class MemoryBackend(Protocol):
    slug: str
    supports_claude_hooks: bool

    def search(self, block: dict, *, query: str, limit: int) -> dict: ...
    def timeline(self, block: dict, *, limit: int, cursor: str | None) -> dict: ...
    def get_observation(self, block: dict, observation_id: str) -> dict: ...
    def health(self, block: dict) -> dict: ...
    def dashboard_status(self, block: dict) -> dict: ...
    def start_dashboard(self, block: dict) -> dict: ...
    def stop_dashboard(self, block: dict) -> dict: ...
    def touch_dashboard(self, block: dict) -> None: ...


class ClaudeMemBackend:
    slug = "claude-mem"
    supports_claude_hooks = True

    def __init__(self, worker_provider):
        self._worker_provider = worker_provider

    @property
    def worker(self):
        return self._worker_provider()

    def search(self, block, **kwargs):
        return self.worker.search(block, **kwargs)

    def timeline(self, block, **kwargs):
        return self.worker.timeline(block, **kwargs)

    def get_observation(self, block, observation_id):
        return self.worker.get_observation(block, observation_id)

    def health(self, block):
        return self.worker.health(block)

    def dashboard_status(self, block):
        return self.worker.dashboard_status(block)

    def start_dashboard(self, block):
        return self.worker.start_dashboard(block)

    def stop_dashboard(self, block):
        return self.worker.stop_dashboard(block)

    def touch_dashboard(self, block):
        return self.worker.touch_dashboard(block)


class HindsightBackend:
    slug = "hindsight"
    supports_claude_hooks = False

    def __init__(self, config, repository):
        self.config = config
        self.repository = repository

    def bank_id(self, block):
        return self.repository.ensure_hindsight_bank_id(block["block_key"])

    def bank_path(self, block):
        return "/v1/default/banks/" + quote(self.bank_id(block), safe="")

    def request(self, method, path, *, config=None, body=None, params=None, allow_not_found=False):
        settings = config or self.config.read()
        headers = {"Authorization": "Bearer " + settings["api_token"]} if settings["api_token"] else {}
        try:
            response = httpx.request(method, settings["url"] + path, headers=headers, json=body,
                                     params=params, timeout=120, follow_redirects=False)
        except httpx.HTTPError as exc:
            logger.warning("Hindsight 请求失败阶段=连接 状态=失败 类型=%s", type(exc).__name__)
            raise ValidationError("Hindsight 连接失败，请检查服务地址与网络") from exc
        if response.status_code == 404 and allow_not_found:
            return response
        if response.status_code >= 400 or response.is_redirect:
            logger.warning("Hindsight 请求失败阶段=上游 状态码=%s", response.status_code)
            raise ValidationError(f"Hindsight 上游请求失败（HTTP {response.status_code}）")
        return response

    def ensure_bank(self, block, config):
        # bank 由平台创建，原生插件无权创建、删除或修改 bank 配置。
        self.request("PUT", self.bank_path(block), config=config, body={"name": block["name"]})

    def search(self, block, *, query, limit):
        payload = self.request("POST", self.bank_path(block) + "/memories/recall",
                               body={"query": query, "max_tokens": max(256, min(limit, 50) * 256)}).json()
        return {"status": "ok", "block_key": block["block_key"], "items": [{"id": item.get("id", ""), "summary": item.get("text", ""),
                            "content_preview": item.get("text", ""), "score": item.get("score"),
                            "timestamp": item.get("occurred_start"), "metadata": item}
                           for item in payload.get("results", [])[:limit]]}

    def timeline(self, block, *, limit, cursor=None):
        try:
            offset = max(0, int(cursor or 0))
        except ValueError as exc:
            raise ValidationError("记忆分页游标无效") from exc
        data = self.request("GET", self.bank_path(block) + "/documents",
                            params={"limit": limit, "offset": offset}).json()
        items = data.get("items", [])
        return {"status": "ok", "block_key": block["block_key"], "items": [
                    {"id": item.get("id", ""), "event_type": "document", "summary": item.get("id", ""),
                     "timestamp": item.get("updated_at") or item.get("created_at"), "metadata": item} for item in items],
                "next_cursor": str(offset + limit) if offset + len(items) < data.get("total", 0) else None}

    def get_observation(self, block, observation_id):
        identifier = quote(observation_id, safe="")
        response = self.request("GET", self.bank_path(block) + "/memories/" + identifier, allow_not_found=True)
        if response.status_code == 404:
            response = self.request("GET", self.bank_path(block) + "/documents/" + identifier, allow_not_found=True)
        if response.status_code == 404:
            raise NotFound("记忆记录不存在")
        item = response.json()
        return {"status": "ok", "block_key": block["block_key"],
                "item": {**item, "content": item.get("text") or item.get("original_text", "")}}

    def health(self, block):
        self.request("GET", "/v1/version")
        return {"status": "ok", "backend": self.slug}

    def dashboard_status(self, block):
        return {"running": False, "url": None, "status": "unsupported", "backend": self.slug,
                "error": "Hindsight 通过本页检索与时间线查看记忆；管理控制台由管理员独立部署"}

    start_dashboard = dashboard_status
    stop_dashboard = dashboard_status

    def touch_dashboard(self, block):
        return None


class MemoryBackendRegistry:
    def __init__(self, adapters: list[MemoryBackend]):
        self.adapters = {adapter.slug: adapter for adapter in adapters}

    def require(self, slug: str) -> MemoryBackend:
        try:
            return self.adapters[slug]
        except KeyError as exc:
            raise ValidationError("未知记忆后端，禁止自动降级") from exc
