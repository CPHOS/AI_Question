"""
LLM 服务商客户端基类。
所有具体实现（OpenRouter、OpenAI 兼容等）继承此基类。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageInfo:
    """Token 用量信息。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class BaseLLMClient(ABC):
    """LLM 服务商客户端抽象基类。"""

    @abstractmethod
    def stream_chat(self, **kwargs) -> tuple[str, UsageInfo]:
        """流式聊天请求，返回 (完整文本, UsageInfo)。
        kwargs 与 OpenAI SDK chat.completions.create() 参数一致。
        """
        ...

    @abstractmethod
    def create(self, **kwargs):
        """非流式请求（用于 Function Calling 等），返回原始响应对象。
        kwargs 与 OpenAI SDK chat.completions.create() 参数一致。
        """
        ...
