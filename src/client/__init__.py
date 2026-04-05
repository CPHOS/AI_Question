"""
OpenRouter 客户端封装层。
通过 OpenRouter 统一网关调用各大模型（Gemini / GPT / DeepSeek 等）。
"""
from dataclasses import dataclass
from openai import OpenAI
from config.settings import OPENROUTER_API_KEY, MODEL_TIMEOUT


_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class UsageInfo:
    """Token 用量信息。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def get_client() -> OpenAI:
    """获取 OpenRouter 客户端实例（所有模型共用）。"""
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=_OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/cphos/AI_Question",
            "X-Title": "CPhO Physics Generator",
        },
        timeout=MODEL_TIMEOUT,
        max_retries=3,
    )


def stream_chat(client: OpenAI, **kwargs) -> tuple[str, UsageInfo]:
    """
    封装流式聊天请求，返回 (完整文本, UsageInfo)。
    自动设置 stream=True 和 include_usage。
    kwargs 直接传给 client.chat.completions.create()。
    """
    kwargs["stream"] = True
    kwargs["stream_options"] = {"include_usage": True}
    response = client.chat.completions.create(**kwargs)

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
