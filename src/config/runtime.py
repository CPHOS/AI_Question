"""
运行期设置服务（engine-facing facade）。

本模块是引擎 / Agent 侧访问「可持久化设置」的唯一入口，对外暴露：
  - :func:`resolve_model` / :func:`build_client`：把 Agent 角色解析为模型参数
    并构造对应客户端。
  - 一组类型化的应用设置读取函数（max_retry_count 等）。
  - :func:`openrouter_credentials`：账户级额度查询所需凭据。

设计约束
========
- 为避免 ``engine`` / ``agents`` 包反向依赖 ``api`` 包形成 import 环，本模块对
  :mod:`api.settings_store` 与 :mod:`api.db` 一律**函数内懒导入**。
- 首次访问时若数据库尚未初始化，会懒触发 :func:`api.db.init_db`（含播种），
  以支持 CLI / 测试等未显式初始化的场景。
- 读取结果带进程级缓存；任何写入设置的路径都应调用 :func:`invalidate_cache`。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

_lock = threading.RLock()
_resolved_cache: dict[str, "ResolvedModel"] = {}
_app_cache: dict[str, Any] | None = None
_db_ready = False


@dataclass(frozen=True)
class ResolvedModel:
    """某个 Agent 角色解析后的可调用配置快照。"""
    role: str
    provider_kind: str
    api_key: str
    base_url: str
    timeout: int
    max_retries: int
    model: str
    temperature: float
    max_tokens: int
    streaming: bool


def _ensure_db() -> None:
    """懒初始化数据库（幂等）。"""
    global _db_ready
    if _db_ready:
        return
    from api import db
    db.init_db()
    _db_ready = True


def invalidate_cache() -> None:
    """清空解析缓存与数据库就绪标志。修改设置或切换数据库后必须调用。"""
    global _app_cache, _db_ready
    with _lock:
        _resolved_cache.clear()
        _app_cache = None
        _db_ready = False


def resolve_model(role: str) -> ResolvedModel:
    """解析 Agent 角色为 :class:`ResolvedModel`（带缓存）。"""
    with _lock:
        cached = _resolved_cache.get(role)
        if cached is not None:
            return cached
    _ensure_db()
    from api import settings_store

    data = settings_store.resolve(role)
    rm = ResolvedModel(**data)
    with _lock:
        _resolved_cache[role] = rm
    return rm


def build_client(role: str) -> tuple[Any, ResolvedModel]:
    """为 Agent 角色构造客户端并返回 ``(client, resolved)``。

    Agent 用法::

        client, m = build_client("planner")
        text, usage = stream_chat(
            client, model=m.model, temperature=m.temperature,
            max_tokens=m.max_tokens, stream=m.streaming, messages=...,
        )
    """
    from client import build_client_from_settings

    m = resolve_model(role)
    client = build_client_from_settings(
        provider_kind=m.provider_kind, api_key=m.api_key, base_url=m.base_url,
        timeout=m.timeout, max_retries=m.max_retries,
    )
    return client, m


# ---------------------------------------------------------------------------
# 应用设置（类型化读取）
# ---------------------------------------------------------------------------

def _app_settings() -> dict[str, Any]:
    global _app_cache
    with _lock:
        if _app_cache is not None:
            return _app_cache
    _ensure_db()
    from api import settings_store

    data = settings_store.get_app_settings()
    with _lock:
        _app_cache = data
    return data


def max_retry_count() -> int:
    return int(_app_settings().get("max_retry_count", 3))


def source_material_max_chars() -> int:
    return int(_app_settings().get("source_material_max_chars", 60000))


def auto_compile_figures() -> bool:
    return bool(_app_settings().get("auto_compile_figures", True))


def auto_compile_latex() -> bool:
    return bool(_app_settings().get("auto_compile_latex", False))


def latex_compile_timeout() -> int:
    from config import config as cfg

    return int(_app_settings().get("latex_compile_timeout", cfg.SEED_LATEX_COMPILE_TIMEOUT))


def latex_compiler_backend() -> str:
    from config import config as cfg

    return str(_app_settings().get("latex_compiler_backend", cfg.SEED_LATEX_COMPILER_BACKEND))


def latex_service_base_url() -> str:
    from config import config as cfg

    return str(_app_settings().get("latex_service_base_url", cfg.SEED_LATEX_SERVICE_BASE_URL))


def latex_service_api_key() -> str:
    from config import config as cfg

    return str(_app_settings().get("latex_service_api_key", cfg.SEED_LATEX_SERVICE_API_KEY))


def latex_service_poll_interval() -> float:
    from config import config as cfg

    return float(_app_settings().get("latex_service_poll_interval", cfg.SEED_LATEX_SERVICE_POLL_INTERVAL))


def latex_service_max_wait() -> float:
    from config import config as cfg

    return float(_app_settings().get("latex_service_max_wait", cfg.SEED_LATEX_SERVICE_MAX_WAIT))


def sse_poll_interval() -> float:
    return float(_app_settings().get("sse_poll_interval", 1.0))


def sse_max_duration() -> float:
    return float(_app_settings().get("sse_max_duration", 1800.0))


# ---------------------------------------------------------------------------
# 账户级凭据
# ---------------------------------------------------------------------------

def openrouter_credentials() -> tuple[str, int] | None:
    """返回首个 OpenRouter 服务商的 ``(api_key, timeout)``；不存在则 ``None``。"""
    _ensure_db()
    from api import settings_store

    prov = settings_store.first_provider_of_kind("openrouter")
    if prov is None or not prov.get("api_key"):
        return None
    return prov["api_key"], int(prov["timeout"])
