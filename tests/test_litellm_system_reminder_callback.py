from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path


CALLBACK_PATH = (
    Path(__file__).parents[1]
    / "docs"
    / "integrations"
    / "claude-mem-litellm-vllm"
    / "custom_callbacks.py"
)


def _load_callback_module(monkeypatch):
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = object
    proxy_server = types.ModuleType("litellm.proxy.proxy_server")
    proxy_server.DualCache = object
    proxy_server.UserAPIKeyAuth = object

    monkeypatch.setitem(sys.modules, "litellm", types.ModuleType("litellm"))
    monkeypatch.setitem(
        sys.modules,
        "litellm.integrations",
        types.ModuleType("litellm.integrations"),
    )
    monkeypatch.setitem(
        sys.modules,
        "litellm.integrations.custom_logger",
        custom_logger,
    )
    monkeypatch.setitem(
        sys.modules,
        "litellm.proxy",
        types.ModuleType("litellm.proxy"),
    )
    monkeypatch.setitem(
        sys.modules,
        "litellm.proxy.proxy_server",
        proxy_server,
    )

    spec = importlib.util.spec_from_file_location(
        "test_custom_callbacks",
        CALLBACK_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_callback_converts_interleaved_system_to_user_in_place(monkeypatch) -> None:
    module = _load_callback_module(monkeypatch)
    top_level_system = [{"type": "text", "text": "基础系统提示"}]
    reminder_content = [
        {
            "type": "text",
            "text": "<system-reminder>\n后台检索命中 Wiki\n</system-reminder>",
        }
    ]
    data = {
        "system": top_level_system,
        "messages": [
            {"role": "user", "content": "检查订单同步问题"},
            {"role": "assistant", "content": "我先读取代码。"},
            {"role": "system", "content": reminder_content},
            {"role": "user", "content": "继续"},
        ],
    }

    result = asyncio.run(
        module.proxy_handler_instance.async_pre_call_hook(
            user_api_key_dict=None,
            cache=None,
            data=data,
            call_type="anthropic_messages",
        )
    )

    assert result["system"] is top_level_system
    assert [message["role"] for message in result["messages"]] == [
        "user",
        "assistant",
        "user",
        "user",
    ]
    assert result["messages"][2]["content"] is reminder_content
    assert "<system-reminder>" in result["messages"][2]["content"][0]["text"]


def test_callback_does_not_rewrite_non_anthropic_requests(monkeypatch) -> None:
    module = _load_callback_module(monkeypatch)
    data = {
        "messages": [
            {
                "role": "system",
                "content": "<system-reminder>保持原样</system-reminder>",
            }
        ]
    }

    result = asyncio.run(
        module.proxy_handler_instance.async_pre_call_hook(
            user_api_key_dict=None,
            cache=None,
            data=data,
            call_type="completion",
        )
    )

    assert result is data
    assert result["messages"][0]["role"] == "system"
