"""
LLM 设置仓储层：服务商凭据、模型配置、Agent 绑定与运行期应用设置。

设计要点
========
- **模型配置（model_configs）** 与 **Agent 配置（agent_bindings）** 解耦：
  一个「模型配置」描述「用哪个服务商凭据 + 哪个模型 + 何种采样参数」；每个
  Agent 角色（planner / arbiter / formatter ...）再各自**绑定**到某个模型配置，
  因此不同 Agent 可以选用不同的模型甚至不同的服务商。
- 这些记录是**可持久化变量**而非环境变量；仅通过管理员 API 修改（见
  :mod:`api.routes_settings`）。:mod:`config.config` 中的 ``SEED_*`` 常量只在
  数据库首次初始化时作为种子默认值写入。
- 服务商 ``api_key`` 入库存储；对外（API 响应）一律掩码，见 :func:`provider_public`。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from api import db

# Agent 角色的权威清单。新增需要独立选型的 LLM 节点时在此登记。
AGENT_ROLES: tuple[str, ...] = (
    "planner",
    "problem_generator",
    "solution_generator",
    "reviewer_math",
    "reviewer_physics",
    "reviewer_quality",
    "arbiter",
    "formatter",
)

# 运行期应用设置的合法键与其值类型（用于校验与类型化读取）。
APP_SETTING_TYPES: dict[str, type] = {
    "max_retry_count": int,
    "source_material_max_chars": int,
    "auto_compile_figures": bool,
    "auto_compile_latex": bool,
    "latex_compile_timeout": int,
    "latex_compiler_backend": str,
    "latex_service_base_url": str,
    "latex_service_poll_interval": float,
    "latex_service_max_wait": float,
    "sse_poll_interval": float,
    "sse_max_duration": float,
}

# 各应用设置项的数值约束（管理端表单渲染的单一事实来源；与 schemas.AppSettingsUpdate
# 的校验保持一致）。键使用 OpenAPI 约定的 ``min`` / ``exclusiveMin``。
APP_SETTING_CONSTRAINTS: dict[str, dict[str, float]] = {
    "max_retry_count": {"min": 0},
    "source_material_max_chars": {"min": 0},
    "latex_compile_timeout": {"exclusiveMin": 0},
    "latex_service_poll_interval": {"exclusiveMin": 0},
    "latex_service_max_wait": {"exclusiveMin": 0},
    "sse_poll_interval": {"exclusiveMin": 0},
    "sse_max_duration": {"exclusiveMin": 0},
}

# Python 类型 → 元数据中暴露的类型名。
_TYPE_NAMES: dict[type, str] = {int: "int", float: "float", bool: "bool", str: "str"}


def app_setting_specs() -> list[dict[str, Any]]:
    """返回全部应用设置项的元信息（key / type / 约束），供管理端表单渲染。"""
    specs: list[dict[str, Any]] = []
    for key, typ in APP_SETTING_TYPES.items():
        spec: dict[str, Any] = {"key": key, "type": _TYPE_NAMES.get(typ, "str")}
        spec.update(APP_SETTING_CONSTRAINTS.get(key, {}))
        specs.append(spec)
    return specs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool_to_str(v: bool) -> str:
    return "true" if v else "false"


def _coerce(key: str, raw: str) -> Any:
    """把 app_settings 中的字符串值转为其声明类型。"""
    typ = APP_SETTING_TYPES.get(key, str)
    if typ is bool:
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if typ is int:
        return int(raw)
    if typ is float:
        return float(raw)
    return raw


# ============ 服务商凭据 ============

def create_provider(
    *, name: str, kind: str, api_key: str = "", base_url: str = "",
    timeout: int = 600, max_retries: int = 3,
) -> dict[str, Any]:
    pid = f"prov_{uuid.uuid4().hex[:12]}"
    ts = _now()
    db.execute(
        "INSERT INTO llm_providers "
        "(id, name, kind, api_key, base_url, timeout, max_retries, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, name, kind, api_key, base_url, timeout, max_retries, ts, ts),
    )
    return get_provider(pid)  # type: ignore[return-value]


def get_provider(provider_id: str) -> Optional[dict[str, Any]]:
    row = db.query_one("SELECT * FROM llm_providers WHERE id = ?", (provider_id,))
    return dict(row) if row else None


def get_provider_by_name(name: str) -> Optional[dict[str, Any]]:
    row = db.query_one("SELECT * FROM llm_providers WHERE name = ?", (name,))
    return dict(row) if row else None


def list_providers(*, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    rows = db.query_all(
        "SELECT * FROM llm_providers ORDER BY created_at ASC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [dict(r) for r in rows]


def count_providers() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM llm_providers")
    return int(row["n"]) if row else 0


def update_provider(provider_id: str, **fields: Any) -> bool:
    allowed = {"name", "kind", "api_key", "base_url", "timeout", "max_retries"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return get_provider(provider_id) is not None
    cols = ", ".join(f"{k} = ?" for k in sets) + ", updated_at = ?"
    params = (*sets.values(), _now(), provider_id)
    db.execute(f"UPDATE llm_providers SET {cols} WHERE id = ?", params)
    return get_provider(provider_id) is not None


def delete_provider(provider_id: str) -> bool:
    if get_provider(provider_id) is None:
        return False
    used = db.query_one(
        "SELECT COUNT(*) AS n FROM model_configs WHERE provider_id = ?", (provider_id,)
    )
    if used and int(used["n"]) > 0:
        raise ValueError("该服务商仍被模型配置引用，无法删除")
    db.execute("DELETE FROM llm_providers WHERE id = ?", (provider_id,))
    return True


def provider_public(row: dict[str, Any]) -> dict[str, Any]:
    """脱敏服务商记录：api_key 仅保留末 4 位提示是否已配置。"""
    key = row.get("api_key") or ""
    masked = f"****{key[-4:]}" if len(key) >= 4 else ("****" if key else "")
    out = {k: v for k, v in row.items() if k != "api_key"}
    out["api_key_set"] = bool(key)
    out["api_key_masked"] = masked
    return out


# ============ 模型配置 ============

def create_model_config(
    *, name: str, provider_id: str, model: str = "",
    temperature: float = 0.0, max_tokens: int = 4096, streaming: bool = False,
) -> dict[str, Any]:
    if get_provider(provider_id) is None:
        raise ValueError("provider_id 不存在")
    mid = f"model_{uuid.uuid4().hex[:12]}"
    ts = _now()
    db.execute(
        "INSERT INTO model_configs "
        "(id, name, provider_id, model, temperature, max_tokens, streaming, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, name, provider_id, model, temperature, max_tokens, int(streaming), ts, ts),
    )
    return get_model_config(mid)  # type: ignore[return-value]


def get_model_config(model_config_id: str) -> Optional[dict[str, Any]]:
    row = db.query_one("SELECT * FROM model_configs WHERE id = ?", (model_config_id,))
    if not row:
        return None
    out = dict(row)
    out["streaming"] = bool(out["streaming"])
    return out


def get_model_config_by_name(name: str) -> Optional[dict[str, Any]]:
    row = db.query_one("SELECT * FROM model_configs WHERE name = ?", (name,))
    if not row:
        return None
    out = dict(row)
    out["streaming"] = bool(out["streaming"])
    return out


def list_model_configs(*, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    rows = db.query_all(
        "SELECT * FROM model_configs ORDER BY created_at ASC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["streaming"] = bool(d["streaming"])
        out.append(d)
    return out


def count_model_configs() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM model_configs")
    return int(row["n"]) if row else 0


def update_model_config(model_config_id: str, **fields: Any) -> bool:
    allowed = {"name", "provider_id", "model", "temperature", "max_tokens", "streaming"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "provider_id" in sets and get_provider(sets["provider_id"]) is None:
        raise ValueError("provider_id 不存在")
    if "streaming" in sets:
        sets["streaming"] = int(bool(sets["streaming"]))
    if not sets:
        return get_model_config(model_config_id) is not None
    cols = ", ".join(f"{k} = ?" for k in sets) + ", updated_at = ?"
    params = (*sets.values(), _now(), model_config_id)
    db.execute(f"UPDATE model_configs SET {cols} WHERE id = ?", params)
    return get_model_config(model_config_id) is not None


def delete_model_config(model_config_id: str) -> bool:
    if get_model_config(model_config_id) is None:
        return False
    used = db.query_one(
        "SELECT COUNT(*) AS n FROM agent_bindings WHERE model_config_id = ?",
        (model_config_id,),
    )
    if used and int(used["n"]) > 0:
        raise ValueError("该模型配置仍被 Agent 绑定引用，无法删除")
    db.execute("DELETE FROM model_configs WHERE id = ?", (model_config_id,))
    return True


# ============ Agent 绑定 ============

def get_binding(role: str) -> Optional[dict[str, Any]]:
    row = db.query_one("SELECT * FROM agent_bindings WHERE role = ?", (role,))
    return dict(row) if row else None


def list_bindings() -> list[dict[str, Any]]:
    """返回全部权威角色的绑定（未绑定者 model_config_id 为 None）。"""
    rows = {r["role"]: dict(r) for r in db.query_all("SELECT * FROM agent_bindings")}
    out = []
    for role in AGENT_ROLES:
        b = rows.get(role)
        out.append({
            "role": role,
            "model_config_id": b["model_config_id"] if b else None,
            "updated_at": b["updated_at"] if b else None,
        })
    return out


def set_binding(role: str, model_config_id: str) -> dict[str, Any]:
    if role not in AGENT_ROLES:
        raise ValueError(f"未知 Agent 角色: {role}（合法: {', '.join(AGENT_ROLES)}）")
    if get_model_config(model_config_id) is None:
        raise ValueError("model_config_id 不存在")
    ts = _now()
    db.execute(
        "INSERT INTO agent_bindings (role, model_config_id, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(role) DO UPDATE SET model_config_id = excluded.model_config_id, "
        "updated_at = excluded.updated_at",
        (role, model_config_id, ts),
    )
    return get_binding(role)  # type: ignore[return-value]


# ============ 应用设置 ============

def get_app_settings() -> dict[str, Any]:
    """返回全部应用设置（已按声明类型转换）。"""
    rows = db.query_all("SELECT key, value FROM app_settings")
    return {r["key"]: _coerce(r["key"], r["value"]) for r in rows}


def get_app_setting(key: str, default: Any = None) -> Any:
    row = db.query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
    if row is None:
        return default
    return _coerce(key, row["value"])


def set_app_settings(values: dict[str, Any]) -> dict[str, Any]:
    ts = _now()
    for key, val in values.items():
        if key not in APP_SETTING_TYPES:
            raise ValueError(f"未知应用设置项: {key}")
        stored = _bool_to_str(bool(val)) if APP_SETTING_TYPES[key] is bool else str(val)
        db.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, stored, ts),
        )
    return get_app_settings()


# ============ 解析（供运行期使用） ============

def resolve(role: str) -> dict[str, Any]:
    """把某 Agent 角色解析为可直接构造客户端 + 调用模型的扁平配置。

    缺失绑定 / 模型 / 服务商时抛 :class:`ValueError` 并给出可执行指引。
    """
    binding = get_binding(role)
    if binding is None:
        raise ValueError(
            f"Agent '{role}' 未绑定模型配置。\n"
            f"  修复: 通过管理员 API PUT /api/admin/llm/agents/{role} 绑定一个模型配置。"
        )
    mc = get_model_config(binding["model_config_id"])
    if mc is None:
        raise ValueError(f"Agent '{role}' 绑定的模型配置已不存在，请重新绑定。")
    prov = get_provider(mc["provider_id"])
    if prov is None:
        raise ValueError(f"模型配置 '{mc['name']}' 引用的服务商已不存在。")
    return {
        "role": role,
        "provider_kind": prov["kind"],
        "api_key": prov["api_key"],
        "base_url": prov["base_url"],
        "timeout": int(prov["timeout"]),
        "max_retries": int(prov["max_retries"]),
        "model": mc["model"],
        "temperature": float(mc["temperature"]),
        "max_tokens": int(mc["max_tokens"]),
        "streaming": bool(mc["streaming"]),
    }


def first_provider_of_kind(kind: str) -> Optional[dict[str, Any]]:
    """返回首个指定 kind 的服务商（用于额度查询等账户级操作）。"""
    row = db.query_one(
        "SELECT * FROM llm_providers WHERE kind = ? ORDER BY created_at ASC LIMIT 1",
        (kind,),
    )
    return dict(row) if row else None


# ============ 种子默认值 ============

def seed_defaults() -> None:
    """首次初始化时从 .env 种子默认值播种设置；已有数据则跳过（幂等）。"""
    from config import config as cfg

    if count_providers() == 0:
        kind = cfg.SEED_LLM_PROVIDER
        api_key = cfg.SEED_OPENROUTER_API_KEY if kind == "openrouter" else cfg.SEED_LLM_API_KEY
        base_url = "" if kind == "openrouter" else cfg.SEED_LLM_BASE_URL
        prov = create_provider(
            name="default", kind=kind, api_key=api_key, base_url=base_url,
            timeout=cfg.SEED_MODEL_TIMEOUT, max_retries=cfg.SEED_LLM_MAX_RETRIES,
        )
        pid = prov["id"]

        big = create_model_config(
            name="big-default", provider_id=pid, model=cfg.SEED_BIG_MODEL_NAME,
            temperature=cfg.SEED_BIG_MODEL_TEMPERATURE,
            max_tokens=cfg.SEED_BIG_MODEL_MAX_TOKENS, streaming=cfg.SEED_LLM_STREAMING,
        )
        review = create_model_config(
            name="big-review", provider_id=pid, model=cfg.SEED_BIG_MODEL_NAME,
            temperature=cfg.SEED_REVIEW_TEMPERATURE,
            max_tokens=cfg.SEED_BIG_MODEL_MAX_TOKENS, streaming=cfg.SEED_LLM_STREAMING,
        )
        arbiter = create_model_config(
            name="big-arbiter", provider_id=pid, model=cfg.SEED_BIG_MODEL_NAME,
            temperature=cfg.SEED_REVIEW_TEMPERATURE,
            max_tokens=cfg.SEED_ARBITER_MAX_TOKENS, streaming=False,
        )
        small = create_model_config(
            name="small-default", provider_id=pid, model=cfg.SEED_SMALL_MODEL_NAME,
            temperature=cfg.SEED_SMALL_MODEL_TEMPERATURE,
            max_tokens=cfg.SEED_SMALL_MODEL_MAX_TOKENS, streaming=cfg.SEED_LLM_STREAMING,
        )

        defaults = {
            "planner": big["id"],
            "problem_generator": big["id"],
            "solution_generator": big["id"],
            "reviewer_math": review["id"],
            "reviewer_physics": review["id"],
            "reviewer_quality": review["id"],
            "arbiter": arbiter["id"],
            "formatter": small["id"],
        }
        for role, mc_id in defaults.items():
            set_binding(role, mc_id)

    existing = set(get_app_settings())
    seed_app = {
        "max_retry_count": cfg.SEED_MAX_RETRY_COUNT,
        "source_material_max_chars": cfg.SEED_SOURCE_MATERIAL_MAX_CHARS,
        "auto_compile_figures": cfg.SEED_AUTO_COMPILE_FIGURES,
        "auto_compile_latex": cfg.SEED_AUTO_COMPILE_LATEX,
        "latex_compile_timeout": cfg.SEED_LATEX_COMPILE_TIMEOUT,
        "latex_compiler_backend": cfg.SEED_LATEX_COMPILER_BACKEND,
        "latex_service_base_url": cfg.SEED_LATEX_SERVICE_BASE_URL,
        "latex_service_poll_interval": cfg.SEED_LATEX_SERVICE_POLL_INTERVAL,
        "latex_service_max_wait": cfg.SEED_LATEX_SERVICE_MAX_WAIT,
        "sse_poll_interval": cfg.SEED_SSE_POLL_INTERVAL,
        "sse_max_duration": cfg.SEED_SSE_MAX_DURATION,
    }
    missing = {k: v for k, v in seed_app.items() if k not in existing}
    if missing:
        set_app_settings(missing)
