"""
OpenRouter 客户端实现。
通过 OpenRouter 统一网关调用各大模型（Gemini / GPT / DeepSeek 等）。
"""
from client.openai_compat import OpenAICompatibleClient


class OpenRouterClient(OpenAICompatibleClient):
    """OpenRouter 统一网关客户端。"""

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
