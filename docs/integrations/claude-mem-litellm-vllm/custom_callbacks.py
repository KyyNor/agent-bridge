from typing import Literal

from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import DualCache, UserAPIKeyAuth


class SystemToMessagesHandler(CustomLogger):
    """规范 Anthropic Messages 请求中的 system 内容。

    Anthropic 顶层 ``system`` 保持原样。Claude Code 插入 ``messages`` 的
    ``role: system`` 项则在原位置改为 ``role: user``，content 和其中的
    ``<system-reminder>`` 标签保持原样。

    这样既满足 vLLM Anthropic Messages 入口只接受 user/assistant 的约束，也
    避免把会话中途到达的补充信息提升到开头而改变其时间位置。
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

        converted_messages = []
        for message in data.get("messages", []):
            if isinstance(message, dict) and message.get("role") == "system":
                converted_messages.append({**message, "role": "user"})
            else:
                converted_messages.append(message)
        data["messages"] = converted_messages
        return data


# 在 LiteLLM 配置中使用：custom_callbacks.proxy_handler_instance
proxy_handler_instance = SystemToMessagesHandler()
