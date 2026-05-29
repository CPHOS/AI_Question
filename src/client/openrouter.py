"""
OpenRouter 客户端实现。
通过 OpenRouter 统一网关调用各大模型（Gemini / GPT / DeepSeek 等）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from client.base import register_provider
from client.openai_compat import OpenAICompatibleClient


@register_provider("openrouter")
class OpenRouterClient(OpenAICompatibleClient):
    """OpenRouter 统一网关客户端。"""

    provider_name = "openrouter"

    _BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, timeout: int = 600, max_retries: int = 3):
        super().__init__(
            api_key=api_key,
            base_url=self._BASE_URL,
            timeout=timeout,
            max_retries=max_retries,
            default_headers={
                "HTTP-Referer": "https://github.com/cphos/AI_Question",
                "X-Title": "CPhO Physics Generator",
            },
        )

    @classmethod
    def from_config(cls) -> "OpenRouterClient":
        """从 .env 配置构造（OPENROUTER_API_KEY）。"""
        from config.config import SEED_OPENROUTER_API_KEY, SEED_MODEL_TIMEOUT, SEED_LLM_MAX_RETRIES

        if not SEED_OPENROUTER_API_KEY:
            raise ValueError(
                "使用 openrouter 提供商但 OPENROUTER_API_KEY 未设置。\n"
                "  修复方法: 在 .env 中设置 OPENROUTER_API_KEY=<OPENROUTER_API_KEY>"
            )
        return cls(
            api_key=SEED_OPENROUTER_API_KEY,
            timeout=SEED_MODEL_TIMEOUT,
            max_retries=SEED_LLM_MAX_RETRIES,
        )

    @classmethod
    def from_settings(cls, *, api_key: str, base_url: str = "",
                      timeout: int = 600, max_retries: int = 3) -> "OpenRouterClient":
        """从已解析的设置记录构造（``base_url`` 对 OpenRouter 固定，忽略入参）。"""
        if not api_key:
            raise ValueError(
                "openrouter 服务商缺少 api_key。\n"
                "  修复: 通过管理员 API 配置该服务商的 API Key。"
            )
        return cls(api_key=api_key, timeout=timeout, max_retries=max_retries)


def query_openrouter_credits(api_key: str, timeout: int = 30) -> dict:
    """查询 OpenRouter 账户额度快照。

    返回字段尽量保持原始接口信息，并额外补 `balance`。接口异常时抛出
    `RuntimeError`，由调用方决定是否降级为报告中的告警。
    """
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY 未设置，无法查询 OpenRouter 额度")

    req = urllib.request.Request(
        f"{OpenRouterClient._BASE_URL}/credits",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenRouter 额度查询失败: HTTP {exc.code}: {body}") from exc
    except Exception as exc:  # pragma: no cover - 网络环境差异较大
        raise RuntimeError(f"OpenRouter 额度查询失败: {type(exc).__name__}: {exc}") from exc

    data = payload.get("data", payload)
    total_credits = data.get("total_credits")
    total_usage = data.get("total_usage")
    if isinstance(total_credits, (int, float)) and isinstance(total_usage, (int, float)):
        data = dict(data)
        data["balance"] = total_credits - total_usage
    return data
