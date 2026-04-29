"""
OpenRouter 客户端实现。
通过 OpenRouter 统一网关调用各大模型（Gemini / GPT / DeepSeek 等）。
"""
from __future__ import annotations

from client.base import register_provider
from client.openai_compat import OpenAICompatibleClient


@register_provider("openrouter")
class OpenRouterClient(OpenAICompatibleClient):
    """OpenRouter 统一网关客户端。"""

    provider_name = "openrouter"

    _BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, timeout: int = 600):
        super().__init__(
            api_key=api_key,
            base_url=self._BASE_URL,
            timeout=timeout,
            default_headers={
                "HTTP-Referer": "https://github.com/cphos/AI_Question",
                "X-Title": "CPhO Physics Generator",
            },
        )

    @classmethod
    def from_config(cls) -> "OpenRouterClient":
        """从 .env 配置构造（OPENROUTER_API_KEY）。"""
        from config.config import OPENROUTER_API_KEY, MODEL_TIMEOUT

        if not OPENROUTER_API_KEY:
            raise ValueError(
                "使用 openrouter 提供商但 OPENROUTER_API_KEY 未设置。\n"
                "  修复方法: 在 .env 中设置 OPENROUTER_API_KEY=sk-or-..."
            )
        return cls(api_key=OPENROUTER_API_KEY, timeout=MODEL_TIMEOUT)
