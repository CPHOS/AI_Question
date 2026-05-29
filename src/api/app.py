"""
FastAPI 应用工厂与启动入口。

负责：
- 初始化数据库与引导管理员 token；
- 启动 / 关闭后台任务执行器（lifespan）；
- 注册任务与管理路由；
- 暴露健康检查与自动生成的 OpenAPI 文档（``/docs``、``/redoc``、``/openapi.json``）。

启动方式::

    uv run physics-api            # 使用 .env 中的 API_HOST / API_PORT
    uvicorn api.app:app --reload  # 开发模式
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api import db, jobs, store
from api.auth import require_admin  # noqa: F401 - 确保依赖在文档中注册
from api.routes_tasks import router as tasks_router
from api.routes_admin import router as admin_router
from api.schemas import MessageResponse
from config.config import (
    API_HOST, API_PORT, ADMIN_BOOTSTRAP_TOKEN, logger,
)

API_DESCRIPTION = """
CPhOS 物理竞赛题全自动生成系统的后端 API。

## 鉴权
所有 `/api/**` 端点需在 `Authorization: Bearer <token>` 头中携带 token。
token 由管理员通过 `/api/admin/tokens` 签发，明文仅创建时返回一次。

## 异步工作流
1. `POST /api/tasks` 提交任务，立即获得 `task_id`；
2. 轮询 `GET /api/tasks/{task_id}` 直到 `status` 变为 `done` / `error` / `aborted`；
3. 通过 `GET /api/tasks/{task_id}/artifacts` 与下载端点获取产物。
"""


def _bootstrap_admin() -> None:
    """首次启动且库内无管理员时，按引导 token 写入一个管理员。"""
    if not ADMIN_BOOTSTRAP_TOKEN:
        return
    if store.has_admin():
        return
    user = store.create_user(label="bootstrap-admin")
    store.insert_token_hash(
        store.hash_token(ADMIN_BOOTSTRAP_TOKEN),
        user_id=user["user_id"],
        role="admin",
        label="bootstrap",
    )
    logger.info("[api] 已写入引导管理员 user_id=%s", user["user_id"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期：启动时建库 / 引导 admin / 启动执行器，退出时清理。"""
    db.init_db()
    interrupted = store.mark_interrupted_running()
    if interrupted:
        logger.info("[api] 标记 %d 个残留任务为 interrupted", interrupted)
    _bootstrap_admin()
    jobs.start_executor()
    try:
        yield
    finally:
        jobs.shutdown_executor()


def create_app() -> FastAPI:
    """构建并返回 FastAPI 应用实例。"""
    app = FastAPI(
        title="CPhOS 物理竞赛题生成 API",
        description=API_DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=MessageResponse, tags=["system"],
             summary="健康检查")
    def health() -> MessageResponse:
        """无需鉴权的健康检查端点。"""
        return MessageResponse(detail="ok")

    app.include_router(tasks_router)
    app.include_router(admin_router)
    return app


app = create_app()


def run() -> None:
    """``physics-api`` 脚本入口：用 uvicorn 启动服务。"""
    import uvicorn

    uvicorn.run("api.app:app", host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    run()
