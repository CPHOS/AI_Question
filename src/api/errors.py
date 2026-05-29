"""
统一错误契约：错误码、:class:`ApiError` 异常与全局异常处理器。

所有显式抛出的业务错误都会被规整为 ``{"code": ..., "detail": ...}`` 响应体：

- :class:`ApiError` 携带稳定的机器可读 ``code``；
- 普通 ``HTTPException`` 按状态码映射到一个默认 ``code``（见 :data:`_STATUS_CODE_MAP`），
  也可通过 ``detail={"code": ..., "detail": ...}`` 显式指定；
- FastAPI 的请求体校验错误（422）**保留**框架默认的结构化 ``{"detail": [...]}`` 形态，
  以免丢失字段级定位信息。

路由层通过 :data:`ERROR_RESPONSES` 在受保护 router 上统一声明 4xx 响应文档。
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.schemas import ErrorResponse

# ---- 稳定错误码常量（前端可据此做分支处理） ---------------------------------

CODE_BAD_REQUEST = "bad_request"
CODE_UNAUTHORIZED = "unauthorized"
CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_CONFLICT = "conflict"
CODE_UNPROCESSABLE = "unprocessable_entity"
CODE_INTERNAL = "internal_error"
CODE_ERROR = "error"

# 业务语义错误码（任务编译 / 取消 / 重试等场景）。
CODE_TASK_NOT_FOUND = "task_not_found"
CODE_TASK_NOT_TERMINAL = "task_not_terminal"
CODE_TASK_ALREADY_TERMINAL = "task_already_terminal"
CODE_TASK_NOT_RETRYABLE = "task_not_retryable"
CODE_FINAL_LATEX_MISSING = "final_latex_missing"
CODE_COMPILE_IN_PROGRESS = "compile_in_progress"

# 状态码 → 默认错误码（未显式携带 code 的 HTTPException 使用）。
_STATUS_CODE_MAP: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: CODE_BAD_REQUEST,
    status.HTTP_401_UNAUTHORIZED: CODE_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN: CODE_FORBIDDEN,
    status.HTTP_404_NOT_FOUND: CODE_NOT_FOUND,
    status.HTTP_409_CONFLICT: CODE_CONFLICT,
    status.HTTP_422_UNPROCESSABLE_ENTITY: CODE_UNPROCESSABLE,
}


class ApiError(StarletteHTTPException):
    """携带稳定 ``code`` 的 HTTP 异常。

    用法::

        raise ApiError(status.HTTP_409_CONFLICT, CODE_TASK_NOT_TERMINAL, "任务尚未结束")
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def _code_for(status_code: int) -> str:
    """把状态码映射到默认错误码（未显式映射时按状态码区间回退）。"""
    mapped = _STATUS_CODE_MAP.get(status_code)
    if mapped:
        return mapped
    if status_code >= 500:
        return CODE_INTERNAL
    return CODE_ERROR


def _resolve(exc: StarletteHTTPException) -> tuple[str, str]:
    """从异常推导出 ``(code, detail)``。"""
    code = getattr(exc, "code", None)
    detail: Any = exc.detail
    if code is None and isinstance(detail, dict):
        # 兼容 detail={"code": ..., "detail": ...} 的就地声明方式。
        code = detail.get("code")
        detail = detail.get("detail", "")
    if code is None:
        code = _code_for(exc.status_code)
    if not isinstance(detail, str):
        detail = str(detail)
    return code, detail


async def _http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """把 HTTPException / ApiError 规整为 ``{code, detail}`` 响应体。"""
    code, detail = _resolve(exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": code, "detail": detail},
        headers=getattr(exc, "headers", None) or None,
    )


# 受保护端点统一声明的 4xx 错误响应（供 OpenAPI 文档化）。
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse, "description": "缺少或无效的 Bearer token。",
    },
    status.HTTP_403_FORBIDDEN: {
        "model": ErrorResponse, "description": "无权访问该资源（如非管理员 / 非任务归属者）。",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse, "description": "资源不存在。",
    },
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse, "description": "与当前资源状态冲突（如任务非终态 / 编译进行中）。",
    },
}


def install_error_handlers(app: FastAPI) -> None:
    """在应用上注册统一错误处理器。

    仅接管显式 ``HTTPException`` / :class:`ApiError`；请求体校验错误
    （``RequestValidationError``）不注册，沿用 FastAPI 默认处理器以保留字段级信息。
    """
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
