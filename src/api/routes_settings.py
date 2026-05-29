"""
LLM 设置管理路由：服务商凭据、模型配置、Agent 绑定与运行期应用设置。

全部端点要求管理员身份（:func:`api.auth.require_admin`）。这些记录是
**可持久化变量**（见 :mod:`api.settings_store`），而非环境变量；任何写操作后都会
调用 :func:`config.runtime.invalidate_cache` 使运行期缓存失效，确保下一次任务取到
最新配置。

安全：服务商 ``api_key`` 入库存储，但响应一律脱敏（见
:func:`api.settings_store.provider_public`）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api import settings_store
from api.auth import Identity, require_admin
from api.schemas import (
    ProviderCreate, ProviderUpdate, ProviderInfo,
    ModelConfigCreate, ModelConfigUpdate, ModelConfigInfo,
    AgentBindingInfo, AgentBindingUpdate,
    AppSettingsUpdate, AppSettingsInfo,
    MessageResponse, Page,
    ProviderKindsResponse, AppSettingSpec, LLMOptions,
)
from client import supported_providers
from config import runtime

router = APIRouter(prefix="/api/admin/llm", tags=["llm-settings"])


def _invalidate() -> None:
    """写操作后使运行期设置缓存失效。"""
    runtime.invalidate_cache()


# ============ 元数据（驱动管理端表单的单一事实来源） ============

@router.get("/provider-kinds", response_model=ProviderKindsResponse,
            summary="列出已注册的服务商类型")
def list_provider_kinds(_: Identity = Depends(require_admin)) -> ProviderKindsResponse:
    """返回服务商注册中心已注册的 kind 列表，供「新建服务商」表单的下拉框使用，
    新增 provider 后前端零改动。"""
    return ProviderKindsResponse(kinds=list(supported_providers()))


@router.get("/options", response_model=LLMOptions, summary="LLM / 应用设置聚合元数据")
def get_llm_options(_: Identity = Depends(require_admin)) -> LLMOptions:
    """聚合返回服务商类型、Agent 角色与应用设置项元信息，使管理端表单完全由
    后端描述驱动（新增设置项 / 角色 / provider 无需改前端）。"""
    return LLMOptions(
        provider_kinds=list(supported_providers()),
        agent_roles=list(settings_store.AGENT_ROLES),
        app_settings=[AppSettingSpec(**s) for s in settings_store.app_setting_specs()],
    )


# ============ 服务商凭据 ============

@router.post("/providers", response_model=ProviderInfo,
             status_code=status.HTTP_201_CREATED, summary="创建 LLM 服务商凭据")
def create_provider(req: ProviderCreate, _: Identity = Depends(require_admin)) -> ProviderInfo:
    """创建服务商凭据；``name`` 必须唯一。"""
    if settings_store.get_provider_by_name(req.name) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="服务商名称已存在")
    row = settings_store.create_provider(
        name=req.name, kind=req.kind, api_key=req.api_key, base_url=req.base_url,
        timeout=req.timeout, max_retries=req.max_retries,
    )
    _invalidate()
    return ProviderInfo(**settings_store.provider_public(row))


@router.get("/providers", response_model=Page[ProviderInfo], summary="列出服务商")
def list_providers(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: Identity = Depends(require_admin),
) -> Page[ProviderInfo]:
    """分页列出服务商（api_key 脱敏）。"""
    rows = settings_store.list_providers(limit=limit, offset=offset)
    total = settings_store.count_providers()
    items = [ProviderInfo(**settings_store.provider_public(r)) for r in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/providers/{provider_id}", response_model=ProviderInfo, summary="查看服务商")
def get_provider(provider_id: str, _: Identity = Depends(require_admin)) -> ProviderInfo:
    row = settings_store.get_provider(provider_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务商不存在")
    return ProviderInfo(**settings_store.provider_public(row))


@router.patch("/providers/{provider_id}", response_model=ProviderInfo, summary="更新服务商")
def update_provider(
    provider_id: str, req: ProviderUpdate, _: Identity = Depends(require_admin),
) -> ProviderInfo:
    """更新服务商；仅传入的字段会被修改（``api_key`` 留空表示保持不变）。"""
    if settings_store.get_provider(provider_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务商不存在")
    settings_store.update_provider(provider_id, **req.model_dump(exclude_none=True))
    _invalidate()
    return ProviderInfo(**settings_store.provider_public(settings_store.get_provider(provider_id)))


@router.delete("/providers/{provider_id}", response_model=MessageResponse, summary="删除服务商")
def delete_provider(provider_id: str, _: Identity = Depends(require_admin)) -> MessageResponse:
    """删除服务商；若仍被模型配置引用则拒绝。"""
    try:
        ok = settings_store.delete_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务商不存在")
    _invalidate()
    return MessageResponse(detail=f"服务商 {provider_id} 已删除")


# ============ 模型配置 ============

@router.post("/models", response_model=ModelConfigInfo,
             status_code=status.HTTP_201_CREATED, summary="创建模型配置")
def create_model_config(
    req: ModelConfigCreate, _: Identity = Depends(require_admin),
) -> ModelConfigInfo:
    """创建模型配置；``name`` 唯一，``provider_id`` 必须存在。"""
    if settings_store.get_model_config_by_name(req.name) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="模型配置名称已存在")
    try:
        row = settings_store.create_model_config(
            name=req.name, provider_id=req.provider_id, model=req.model,
            temperature=req.temperature, max_tokens=req.max_tokens, streaming=req.streaming,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _invalidate()
    return ModelConfigInfo(**row)


@router.get("/models", response_model=Page[ModelConfigInfo], summary="列出模型配置")
def list_model_configs(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: Identity = Depends(require_admin),
) -> Page[ModelConfigInfo]:
    rows = settings_store.list_model_configs(limit=limit, offset=offset)
    total = settings_store.count_model_configs()
    return Page(items=[ModelConfigInfo(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/models/{model_config_id}", response_model=ModelConfigInfo, summary="查看模型配置")
def get_model_config(model_config_id: str, _: Identity = Depends(require_admin)) -> ModelConfigInfo:
    row = settings_store.get_model_config(model_config_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")
    return ModelConfigInfo(**row)


@router.patch("/models/{model_config_id}", response_model=ModelConfigInfo, summary="更新模型配置")
def update_model_config(
    model_config_id: str, req: ModelConfigUpdate, _: Identity = Depends(require_admin),
) -> ModelConfigInfo:
    if settings_store.get_model_config(model_config_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")
    try:
        settings_store.update_model_config(model_config_id, **req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _invalidate()
    return ModelConfigInfo(**settings_store.get_model_config(model_config_id))


@router.delete("/models/{model_config_id}", response_model=MessageResponse, summary="删除模型配置")
def delete_model_config(model_config_id: str, _: Identity = Depends(require_admin)) -> MessageResponse:
    """删除模型配置；若仍被 Agent 绑定引用则拒绝。"""
    try:
        ok = settings_store.delete_model_config(model_config_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")
    _invalidate()
    return MessageResponse(detail=f"模型配置 {model_config_id} 已删除")


# ============ Agent 绑定 ============

@router.get("/agents", response_model=list[AgentBindingInfo], summary="列出 Agent 绑定")
def list_bindings(_: Identity = Depends(require_admin)) -> list[AgentBindingInfo]:
    """列出全部权威 Agent 角色及其当前绑定的模型配置。"""
    return [AgentBindingInfo(**b) for b in settings_store.list_bindings()]


@router.put("/agents/{role}", response_model=AgentBindingInfo, summary="绑定 Agent 到模型配置")
def set_binding(
    role: str, req: AgentBindingUpdate, _: Identity = Depends(require_admin),
) -> AgentBindingInfo:
    """把指定 Agent 角色绑定到某个模型配置。"""
    try:
        b = settings_store.set_binding(role, req.model_config_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    _invalidate()
    return AgentBindingInfo(**b)


# ============ 运行期应用设置 ============

@router.get("/settings", response_model=AppSettingsInfo, summary="查看运行期应用设置")
def get_app_settings(_: Identity = Depends(require_admin)) -> AppSettingsInfo:
    """返回当前运行期应用设置（重试次数、自动编译开关、SSE 参数等）。"""
    return AppSettingsInfo(**settings_store.get_app_settings())


@router.patch("/settings", response_model=AppSettingsInfo, summary="更新运行期应用设置")
def update_app_settings(
    req: AppSettingsUpdate, _: Identity = Depends(require_admin),
) -> AppSettingsInfo:
    """更新运行期应用设置；仅传入的字段会被修改。"""
    values = req.model_dump(exclude_none=True)
    if values:
        try:
            settings_store.set_app_settings(values)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        _invalidate()
    return AppSettingsInfo(**settings_store.get_app_settings())
