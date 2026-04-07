"""
LLM 客户端工厂。
根据 LLM_PROVIDER 配置自动选择对应实现。

支持的提供商:
  - openrouter: 通过 OpenRouter 统一网关调用
  - openai_compatible: 通用 OpenAI 兼容 API（DeepSeek、本地部署等）
"""
from client.base import BaseLLMClient, UsageInfo

__all__ = ["get_client", "stream_chat", "UsageInfo", "BaseLLMClient"]


def get_client() -> BaseLLMClient:
    """根据 LLM_PROVIDER 配置创建客户端实例。"""
    from config.settings import LLM_PROVIDER, MODEL_TIMEOUT

    if LLM_PROVIDER == "openrouter":
        from config.settings import OPENROUTER_API_KEY
        if not OPENROUTER_API_KEY:
            raise ValueError(
                "使用 openrouter 提供商但 OPENROUTER_API_KEY 未设置。\n"
                "  修复方法: 在 .env 中设置 OPENROUTER_API_KEY=sk-or-..."
            )
        from client.openrouter import OpenRouterClient
        return OpenRouterClient(api_key=OPENROUTER_API_KEY, timeout=MODEL_TIMEOUT)

    elif LLM_PROVIDER == "openai_compatible":
        from config.settings import LLM_API_KEY, LLM_BASE_URL
        if not LLM_API_KEY or not LLM_BASE_URL:
            raise ValueError(
                "使用 openai_compatible 提供商但 LLM_API_KEY 或 LLM_BASE_URL 未设置。\n"
                "  修复方法: 在 .env 中设置 LLM_API_KEY 和 LLM_BASE_URL"
            )
        from client.openai_compat import OpenAICompatibleClient
        return OpenAICompatibleClient(
            api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=MODEL_TIMEOUT,
        )

    else:
        raise ValueError(
            f"不支持的 LLM 提供商: '{LLM_PROVIDER}'。\n"
            f"  支持的值: openrouter, openai_compatible"
        )


def stream_chat(client: BaseLLMClient, **kwargs) -> tuple[str, UsageInfo]:
    """兼容性包装：将独立函数调用代理到客户端实例方法。"""
    return client.stream_chat(**kwargs)
