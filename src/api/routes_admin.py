"""
管理路由：用户与 token 生命周期、跨用户任务查看。

全部端点要求管理员身份（:func:`api.auth.require_admin`）。Token 明文仅在创建时
返回一次，请通过安全渠道分发。
"""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api import jobs, store
from app.outputs import ASSETS_DIR_SUFFIX
from api.auth import Identity, require_admin
from api.schemas import (
    CreateUserRequest, UpdateUserRequest, UserInfo, UserDetail,
    CreateTokenRequest, TokenCreated, TokenInfo,
    TaskListItem, MessageResponse, Page, AdminStats,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _parse_statuses(status_param: str | None) -> list[str] | None:
    """把逗号分隔的 status 查询参数解析为列表。"""
    if not status_param:
        return None
    values = [s.strip() for s in status_param.split(",") if s.strip()]
    return values or None


# ============ 用户 ============

@router.post("/users", response_model=UserInfo, status_code=status.HTTP_201_CREATED,
             summary="创建用户")
def create_user(req: CreateUserRequest, _: Identity = Depends(require_admin)) -> UserInfo:
    """创建一个新用户（随后可为其签发 token）。"""
    user = store.create_user(label=req.label)
    return UserInfo(**user)


@router.get("/users", response_model=Page[UserInfo], summary="列出用户")
def list_users(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, description="按 user_id / label 模糊搜索。"),
    order: str = Query("DESC", description="按创建时间排序：ASC / DESC。"),
    _: Identity = Depends(require_admin),
) -> Page[UserInfo]:
    """分页列出用户，支持模糊搜索与排序。"""
    rows = store.list_users(limit=limit, offset=offset, q=q, order=order)
    total = store.count_users(q=q)
    return Page(items=[UserInfo(**u) for u in rows], total=total, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=UserDetail, summary="查看单用户详情")
def get_user(user_id: str, _: Identity = Depends(require_admin)) -> UserDetail:
    """返回用户详情（含未吊销 token 数与任务数）。"""
    detail = store.get_user_detail(user_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return UserDetail(**detail)


@router.patch("/users/{user_id}", response_model=UserInfo, summary="更新用户备注名")
def update_user(
    user_id: str, req: UpdateUserRequest, _: Identity = Depends(require_admin),
) -> UserInfo:
    """更新指定用户的 label。"""
    if not store.update_user_label(user_id, req.label):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return UserInfo(**store.get_user(user_id))


@router.delete("/users/{user_id}", response_model=MessageResponse, summary="删除用户")
def delete_user(user_id: str, _: Identity = Depends(require_admin)) -> MessageResponse:
    """删除用户并级联吊销 / 删除其 token、任务记录与磁盘产物。"""
    if store.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    base = jobs.user_output_dir(user_id)
    task_ids = store.delete_user_cascade(user_id)
    # 清理磁盘产物（DB 记录已在级联中删除）。
    if base.exists():
        for task_id in task_ids:
            for item in jobs.list_artifacts(user_id, task_id):
                p = base / item["filename"]
                if p.exists() and p.is_file():
                    p.unlink()
            assets = base / f"{task_id}{ASSETS_DIR_SUFFIX}"
            if assets.is_dir():
                shutil.rmtree(assets, ignore_errors=True)
    # 清理各任务的编译锁，避免注册表无限增长。
    for task_id in task_ids:
        jobs.discard_compile_lock(task_id)
    return MessageResponse(detail=f"用户 {user_id} 已删除（级联 {len(task_ids)} 个任务）")


@router.get("/users/{user_id}/tasks", response_model=Page[TaskListItem],
            summary="查看指定用户的任务历史")
def list_user_tasks(
    user_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: Identity = Depends(require_admin),
) -> Page[TaskListItem]:
    """查看任意用户的任务历史。"""
    if store.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    rows = store.list_tasks(user_id=user_id, limit=limit, offset=offset)
    total = store.count_tasks(user_id=user_id)
    return Page(items=[TaskListItem(**r) for r in rows], total=total, limit=limit, offset=offset)


# ============ Token ============

@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED,
             summary="为用户签发 token")
def create_token(req: CreateTokenRequest, _: Identity = Depends(require_admin)) -> TokenCreated:
    """为指定用户签发一个不透明 token，明文仅此一次返回。"""
    if store.get_user(req.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    token = store.create_token(req.user_id, role=req.role, label=req.label)
    return TokenCreated(**token)


@router.get("/tokens", response_model=Page[TokenInfo], summary="列出 token 元数据")
def list_tokens(
    user_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, description="按 token id / label 模糊搜索。"),
    order: str = Query("DESC", description="按创建时间排序：ASC / DESC。"),
    _: Identity = Depends(require_admin),
) -> Page[TokenInfo]:
    """分页列出 token 元数据（不含明文）；可按 user_id 过滤、模糊搜索与排序。"""
    rows = store.list_tokens(user_id=user_id, limit=limit, offset=offset, q=q, order=order)
    total = store.count_tokens(user_id=user_id, q=q)
    return Page(items=[TokenInfo(**t) for t in rows], total=total, limit=limit, offset=offset)


@router.delete("/tokens/{token_id}", response_model=MessageResponse, summary="吊销 token")
def revoke_token(token_id: str, _: Identity = Depends(require_admin)) -> MessageResponse:
    """吊销指定 token。"""
    if not store.revoke_token(token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token 不存在或已吊销")
    return MessageResponse(detail=f"token {token_id} 已吊销")


# ============ 全局任务 ============

@router.get("/tasks", response_model=Page[TaskListItem], summary="列出全部任务")
def list_all_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="按状态过滤，可逗号分隔。"),
    mode: str | None = Query(None, description="按命题模式过滤。"),
    q: str | None = Query(None, description="按 topic 模糊搜索。"),
    order: str = Query("DESC", description="按创建时间排序：ASC / DESC。"),
    _: Identity = Depends(require_admin),
) -> Page[TaskListItem]:
    """跨用户列出全部任务，支持状态 / 模式 / 主题过滤与排序。"""
    statuses = _parse_statuses(status)
    rows = store.list_tasks(
        limit=limit, offset=offset, statuses=statuses, mode=mode, q=q, order=order,
    )
    total = store.count_tasks(statuses=statuses, mode=mode, q=q)
    return Page(items=[TaskListItem(**r) for r in rows], total=total, limit=limit, offset=offset)


# ============ 统计概览 ============

@router.get("/stats", response_model=AdminStats, summary="管理端统计概览")
def get_stats(_: Identity = Depends(require_admin)) -> AdminStats:
    """返回任务 / 用户 / token 计数与累计 token 用量、API 费用，供仪表盘使用。"""
    return AdminStats(**store.admin_stats())
