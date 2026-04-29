"""
通用 OpenAI 兼容 API 客户端。
适用于任何兼容 OpenAI API 的服务商（DeepSeek、本地部署、Azure 等）。
OpenRouter 客户端也继承自此类。
"""
from __future__ import annotations

from openai import OpenAI

from client.base import BaseLLMClient, UsageInfo, register_provider


@register_provider("openai_compatible")
class OpenAICompatibleClient(BaseLLMClient):
    """通用 OpenAI 兼容 API 客户端。"""

    provider_name = "openai_compatible"

    def __init__(self, api_key: str, base_url: str,
                 timeout: int = 600, **client_kwargs):
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=3,
            **client_kwargs,
        )

    @classmethod
    def from_config(cls) -> "OpenAICompatibleClient":
        """从 .env 配置构造（LLM_API_KEY + LLM_BASE_URL）。"""
        from config.config import LLM_API_KEY, LLM_BASE_URL, MODEL_TIMEOUT

        if not LLM_API_KEY or not LLM_BASE_URL:
            raise ValueError(
                "使用 openai_compatible 提供商但 LLM_API_KEY 或 LLM_BASE_URL 未设置。\n"
                "  修复方法: 在 .env 中设置 LLM_API_KEY 和 LLM_BASE_URL"
            )
        return cls(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            timeout=MODEL_TIMEOUT,
        )

    def stream_chat(self, **kwargs) -> tuple[str, UsageInfo]:
        """流式聊天请求，返回 (完整文本, UsageInfo)。"""
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        response = self._client.chat.completions.create(**kwargs)

        content = ""
        usage = None
        for chunk in response:
            if chunk.usage:
                usage = chunk.usage
            if chunk.choices and chunk.choices[0].delta.content:
                content += chunk.choices[0].delta.content

        info = UsageInfo(
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        )
        return content, info

    def create(self, **kwargs):
        """非流式请求，返回原始响应对象。"""
        return self._client.chat.completions.create(**kwargs)
