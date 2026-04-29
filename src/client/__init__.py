"""
LLM 客户端工厂。
基于注册中心 (`client.base.register_provider`) 实现，新增服务商无需改动本文件。

支持的提供商（默认注册）:
  - openrouter: 通过 OpenRouter 统一网关调用
  - openai_compatible: 通用 OpenAI 兼容 API（DeepSeek、本地部署等）

新增 Provider 示例:
    from client.base import BaseLLMClient, register_provider

    @register_provider("my_provider")
    class MyProviderClient(BaseLLMClient):
        @classmethod
        def from_config(cls):
            ...
        def stream_chat(self, **kw): ...
        def create(self, **kw): ...

    # 在 .env 设置 LLM_PROVIDER=my_provider 后，get_client() 会自动选中。
"""
from client.base import (
    BaseLLMClient,
    UsageInfo,
    register_provider,
    get_provider_class,
    supported_providers,
)

# 触发默认 Provider 自注册（@register_provider 在 import 时生效）。
# 注意：openrouter 模块会自动 import openai_compat 作为父类，所以引入顺序无影响。
import client.openai_compat  # noqa: F401  -- side-effect: register openai_compatible
import client.openrouter     # noqa: F401  -- side-effect: register openrouter


__all__ = [
    "get_client",
    "stream_chat",
    "UsageInfo",
    "BaseLLMClient",
    "register_provider",
    "supported_providers",
]


def get_client() -> BaseLLMClient:
    """根据 LLM_PROVIDER 配置创建客户端实例。

    通过注册中心查找并委托给 provider 自己的 `from_config()`。
    """
    from config.config import LLM_PROVIDER

    cls = get_provider_class(LLM_PROVIDER)
    return cls.from_config()


def stream_chat(client: BaseLLMClient, **kwargs) -> tuple[str, UsageInfo]:
    """兼容性包装：将独立函数调用代理到客户端实例方法。"""
    return client.stream_chat(**kwargs)
