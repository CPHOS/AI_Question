"""
管理路由：用户与 token 生命周期、跨用户任务查看。

全部端点要求管理员身份（:func:`api.auth.require_admin`）。Token 明文仅在创建时
返回一次，请通过安全渠道分发。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api import store
from api.auth import Identity, require_admin
from api.schemas import (
    CreateUserRequest, UserInfo, CreateTokenRequest, TokenCreated, TokenInfo,
    TaskListItem, MessageResponse,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============ 用户 ============

@router.post("/users", response_model=UserInfo, status_code=status.HTTP_201_CREATED,
             summary="创建用户")
def create_user(req: CreateUserRequest, _: Identity = Depends(require_admin)) -> UserInfo:
    """创建一个新用户（随后可为其签发 token）。"""
    user = store.create_user(label=req.label)
    return UserInfo(**user)


@router.get("/users", response_model=list[UserInfo], summary="列出用户")
def list_users(_: Identity = Depends(require_admin)) -> list[UserInfo]:
    """列出全部用户。"""
    return [UserInfo(**u) for u in store.list_users()]


@router.get("/users/{user_id}/tasks", response_model=list[TaskListItem],
            summary="查看指定用户的任务历史")
def list_user_tasks(
    user_id: str, limit: int = 50, offset: int = 0, _: Identity = Depends(require_admin),
) -> list[TaskListItem]:
    """查看任意用户的任务历史。"""
    if store.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    rows = store.list_tasks(user_id=user_id, limit=limit, offset=offset)
    return [TaskListItem(**r) for r in rows]


# ============ Token ============

@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED,
             summary="为用户签发 token")
def create_token(req: CreateTokenRequest, _: Identity = Depends(require_admin)) -> TokenCreated:
    """为指定用户签发一个不透明 token，明文仅此一次返回。"""
    if store.get_user(req.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    token = store.create_token(req.user_id, role=req.role, label=req.label)
    return TokenCreated(**token)


@router.get("/tokens", response_model=list[TokenInfo], summary="列出 token 元数据")
def list_tokens(
    user_id: str | None = None, _: Identity = Depends(require_admin),
) -> list[TokenInfo]:
    """列出 token 元数据（不含明文）；可按 user_id 过滤。"""
    return [TokenInfo(**t) for t in store.list_tokens(user_id=user_id)]


@router.delete("/tokens/{token_id}", response_model=MessageResponse, summary="吊销 token")
def revoke_token(token_id: str, _: Identity = Depends(require_admin)) -> MessageResponse:
    """吊销指定 token。"""
    if not store.revoke_token(token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token 不存在或已吊销")
    return MessageResponse(detail=f"token {token_id} 已吊销")


# ============ 全局任务 ============

@router.get("/tasks", response_model=list[TaskListItem], summary="列出全部任务")
def list_all_tasks(
    limit: int = 50, offset: int = 0, _: Identity = Depends(require_admin),
) -> list[TaskListItem]:
    """跨用户列出全部任务（按创建时间倒序）。"""
    rows = store.list_tasks(limit=limit, offset=offset)
    return [TaskListItem(**r) for r in rows]
