"""
鉴权依赖：基于不透明 Bearer token 的用户 / 管理员身份解析。

客户端在 ``Authorization: Bearer <token>`` 头中携带 token。:func:`require_user`
解析任意有效身份；:func:`require_admin` 仅放行管理员。解析结果以
:class:`Identity` 注入路由处理函数。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api import store

_bearer = HTTPBearer(auto_error=False, description="不透明 token，形如 `Bearer <token>`")


@dataclass
class Identity:
    """已鉴权的调用者身份。"""

    user_id: str
    role: str
    token_id: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Identity:
    """解析并校验 token，返回调用者身份；失败抛 401。"""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    record = store.resolve_token(credentials.credentials)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 无效或已吊销",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Identity(user_id=record["user_id"], role=record["role"], token_id=record["id"])


def require_admin(identity: Identity = Depends(require_user)) -> Identity:
    """要求管理员身份；非管理员抛 403。"""
    if not identity.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return identity
