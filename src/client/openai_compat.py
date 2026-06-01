"""
通用 OpenAI 兼容 API 客户端。
适用于任何兼容 OpenAI API 的服务商（DeepSeek、本地部署、Azure 等）。
OpenRouter 客户端也继承自此类。
"""
from __future__ import annotations

from typing import Optional

import httpx
from openai import OpenAI

from client.base import BaseLLMClient, UsageInfo, register_provider


@register_provider("openai_compatible")
class OpenAICompatibleClient(BaseLLMClient):
    """通用 OpenAI 兼容 API 客户端。"""

    provider_name = "openai_compatible"

    def __init__(self, api_key: str, base_url: str,
                 timeout: int = 600, max_retries: int = 3,
                 proxy: Optional[str] = None, **client_kwargs):
        http_client: Optional[httpx.Client] = None
        if proxy:
            http_client = httpx.Client(proxy=proxy)
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
            **client_kwargs,
        )

    @classmethod
    def from_config(cls) -> "OpenAICompatibleClient":
        """从 .env 配置构造（LLM_API_KEY + LLM_BASE_URL）。"""
        from config.config import (
            SEED_LLM_API_KEY, SEED_LLM_BASE_URL, SEED_MODEL_TIMEOUT, SEED_LLM_MAX_RETRIES,
        )

        if not SEED_LLM_API_KEY or not SEED_LLM_BASE_URL:
            raise ValueError(
                "使用 openai_compatible 提供商但 LLM_API_KEY 或 LLM_BASE_URL 未设置。\n"
                "  修复方法: 在 .env 中设置 LLM_API_KEY 和 LLM_BASE_URL"
            )
        return cls(
            api_key=SEED_LLM_API_KEY,
            base_url=SEED_LLM_BASE_URL,
            timeout=SEED_MODEL_TIMEOUT,
            max_retries=SEED_LLM_MAX_RETRIES,
        )

    @classmethod
    def from_settings(cls, *, api_key: str, base_url: str,
                      timeout: int = 600, max_retries: int = 3,
                      proxy: Optional[str] = None) -> "OpenAICompatibleClient":
        """从已解析的设置记录构造（供 :mod:`config.runtime` 使用）。"""
        if not api_key or not base_url:
            raise ValueError(
                "openai_compatible 服务商缺少 api_key 或 base_url。\n"
                "  修复: 通过管理员 API 配置该服务商的凭据。"
            )
        return cls(api_key=api_key, base_url=base_url,
                   timeout=timeout, max_retries=max_retries, proxy=proxy)

    def stream_chat(self, **kwargs) -> tuple[str, UsageInfo]:
        """流式聊天请求，返回 (完整文本, UsageInfo)。

        是否流式由显式的 ``stream`` kwarg 决定（来自已解析的模型配置）；
        缺省为非流式。
        """
        if not kwargs.pop("stream", False):
            kwargs.pop("stream", None)
            kwargs.pop("stream_options", None)
            response = self._client.chat.completions.create(**kwargs)
            usage = response.usage
            message = response.choices[0].message
            content = message.content or ""
            info = UsageInfo(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            )
            return content, info

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
