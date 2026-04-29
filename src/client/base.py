"""
LLM 服务商客户端基类与服务商注册中心。

设计目标（针对 PR #5 review wu / Boxin-Byron 的扩展性意见）：
  1. `BaseLLMClient` 收敛为一个稳定的抽象基类（最小接口 + 可扩展配置）。
  2. 提供一套**注册中心**（registry）与 `@register_provider("name")` 装饰器，
     新增服务商时只需新建一个继承 `BaseLLMClient` 的类并加装饰器，
     `client/__init__.py` 中的工厂函数无需任何改动。
  3. 每个 Provider 自己负责从环境变量构造（`from_config()` classmethod），
     避免在工厂里堆 `if/elif` 链条。

参考: CPHOS/AI_Reviewer 中的 client / provider 抽象层。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterable, TypeVar


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UsageInfo:
    """Token 用量信息。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class BaseLLMClient(ABC):
    """LLM 服务商客户端抽象基类。

    子类必须实现：
      - `stream_chat(**kwargs) -> tuple[str, UsageInfo]`
      - `create(**kwargs) -> Any`（返回原始 SDK 响应，供 Function Calling 使用）
      - `from_config()` classmethod：从全局配置 / 环境变量构造一个实例

    `from_config()` 必须在缺失必备配置（API key / base URL 等）时抛出
    `ValueError` 并给出可执行的修复指引（例如指出应在 `.env` 中设置哪个变量）。
    """

    # 子类可覆盖：用于日志 / 错误信息中的友好名称。默认取类名。
    provider_name: str = ""

    @abstractmethod
    def stream_chat(self, **kwargs) -> tuple[str, "UsageInfo"]:
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

    @classmethod
    @abstractmethod
    def from_config(cls) -> "BaseLLMClient":
        """从全局配置 / 环境变量构造一个实例。

        典型实现：
            @classmethod
            def from_config(cls):
                from config.config import LLM_API_KEY, LLM_BASE_URL, MODEL_TIMEOUT
                if not LLM_API_KEY:
                    raise ValueError("环境变量缺失: LLM_API_KEY ...")
                return cls(api_key=LLM_API_KEY, base_url=LLM_BASE_URL,
                           timeout=MODEL_TIMEOUT)
        """
        ...


# ---------------------------------------------------------------------------
# 服务商注册中心
# ---------------------------------------------------------------------------

_T = TypeVar("_T", bound=BaseLLMClient)

# name(str) -> client class
_PROVIDER_REGISTRY: dict[str, type[BaseLLMClient]] = {}


def register_provider(name: str) -> Callable[[type[_T]], type[_T]]:
    """类装饰器：将一个 BaseLLMClient 子类注册到指定名称下。

    用法：
        @register_provider("openrouter")
        class OpenRouterClient(BaseLLMClient):
            ...

    重复注册同名 provider 会抛 `ValueError`，避免 silent shadowing。
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"provider 名称必须是非空字符串，收到: {name!r}")

    def decorator(cls: type[_T]) -> type[_T]:
        if not issubclass(cls, BaseLLMClient):
            raise TypeError(
                f"@register_provider 只能装饰 BaseLLMClient 子类，"
                f"但 {cls.__name__} 不是。"
            )
        existing = _PROVIDER_REGISTRY.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"provider '{name}' 已注册为 {existing.__name__}，"
                f"不能再注册为 {cls.__name__}。"
            )
        _PROVIDER_REGISTRY[name] = cls
        if not cls.provider_name:
            cls.provider_name = name
        return cls

    return decorator


def get_provider_class(name: str) -> type[BaseLLMClient]:
    """根据名称查找已注册的 provider 类。"""
    if name not in _PROVIDER_REGISTRY:
        supported = ", ".join(sorted(_PROVIDER_REGISTRY.keys())) or "（空）"
        raise ValueError(
            f"不支持的 LLM 提供商: '{name}'。\n"
            f"  已注册: {supported}\n"
            f"  修复: 检查 .env 中的 LLM_PROVIDER，或为新服务商添加"
            f" @register_provider('{name}') 类。"
        )
    return _PROVIDER_REGISTRY[name]


def supported_providers() -> Iterable[str]:
    """返回所有已注册 provider 名称（按字典序）。"""
    return sorted(_PROVIDER_REGISTRY.keys())
