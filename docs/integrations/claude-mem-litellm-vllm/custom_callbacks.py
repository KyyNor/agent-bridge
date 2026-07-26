from typing import Any, Literal

from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import DualCache, UserAPIKeyAuth


def _flatten_content(content: Any) -> str:
    """将 Anthropic content block 列表拍平为字符串。"""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text is None:
                    text = block.get("content")
                parts.append("" if text is None else str(text))
            else:
                parts.append(str(block))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


class SystemToMessagesHandler(CustomLogger):
    """规范 Anthropic Messages 请求中的 system 内容。

    Claude Code 与 claude-mem 可能把 system 内容放在顶层 ``system`` 字段或
    ``messages`` 内的 ``role: system`` 项。vLLM 0.17 的 Anthropic Messages
    接口只接受 user/assistant 角色，但支持顶层 system；LiteLLM 的
    Anthropic → OpenAI Chat 适配器也只会转换顶层 system。

    本 hook 收集并合并两处 system 内容，写回顶层 ``system``，并移除
    ``messages`` 内的 system 项。
    """

    def __init__(self):
        super().__init__()

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: Literal[
            "completion",
            "text_completion",
            "embeddings",
            "image_generation",
            "moderation",
            "audio_transcription",
            "anthropic_messages",
        ],
    ):
        # 仅规范 Anthropic Messages 请求，避免改写 OpenAI 兼容端点的语义。
        if call_type != "anthropic_messages":
            return data

        # vLLM 0.17 不支持 context_management。output_config 由 LiteLLM
        # Anthropic 适配器转换，不能在此处删除。
        data.pop("context_management", None)

        system_parts: list[str] = []
        top_level_system = _flatten_content(data.get("system"))
        if top_level_system:
            system_parts.append(top_level_system)

        kept_messages = []
        for message in data.get("messages", []):
            if isinstance(message, dict) and message.get("role") == "system":
                system_text = _flatten_content(message.get("content", ""))
                if system_text:
                    system_parts.append(system_text)
            else:
                kept_messages.append(message)

        if system_parts:
            data["system"] = "\n\n".join(system_parts)
        else:
            data.pop("system", None)
        data["messages"] = kept_messages
        return data


# 在 LiteLLM 配置中使用：custom_callbacks.proxy_handler_instance
proxy_handler_instance = SystemToMessagesHandler()
