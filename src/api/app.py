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
from fastapi.middleware.cors import CORSMiddleware

from api import db, jobs, store
from api.auth import require_admin  # noqa: F401 - 确保依赖在文档中注册
from api.errors import ERROR_RESPONSES, install_error_handlers
from api.routes_tasks import router as tasks_router, me_router
from api.routes_admin import router as admin_router
from api.routes_settings import router as settings_router
from api.schemas import ComponentHealth, HealthStatus, VersionInfo
from config.config import (
    API_HOST, API_PORT, ADMIN_BOOTSTRAP_TOKEN, CORS_ALLOW_ORIGINS, logger,
    APP_NAME, APP_VERSION, APP_LICENSE,
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
        version=APP_VERSION,
        license_info={
            "name": APP_LICENSE,
            "url": "https://www.gnu.org/licenses/agpl-3.0.html",
        },
        lifespan=lifespan,
    )

    install_error_handlers(app)

    # 跨域：仅在显式配置来源时启用（同源反代部署无需 CORS）。
    if CORS_ALLOW_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=CORS_ALLOW_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            expose_headers=["Content-Disposition"],
        )
        logger.info("[api] 已启用 CORS，allow_origins=%s", CORS_ALLOW_ORIGINS)

    @app.get("/health", response_model=HealthStatus, tags=["system"],
             summary="健康检查")
    def health() -> HealthStatus:
        """无需鉴权的健康检查端点，返回整体与组件级（db / worker）健康信息。"""
        components: dict[str, ComponentHealth] = {}

        # 数据库：执行一次轻量查询验证可读。
        try:
            db.query_one("SELECT 1 AS n")
            components["db"] = ComponentHealth(status="ok")
        except Exception as exc:  # noqa: BLE001 - 健康检查需吞掉异常并降级上报
            components["db"] = ComponentHealth(status="error", detail=str(exc))

        # 后台任务执行器：检查线程池是否处于运行态。
        if jobs.executor_running():
            components["worker"] = ComponentHealth(status="ok")
        else:
            components["worker"] = ComponentHealth(
                status="error", detail="任务执行器未运行"
            )

        overall = "ok" if all(c.status == "ok" for c in components.values()) else "degraded"
        return HealthStatus(status=overall, components=components)

    @app.get("/version", response_model=VersionInfo, tags=["system"],
             summary="版本信息")
    def version() -> VersionInfo:
        """无需鉴权的版本与许可证信息端点。"""
        return VersionInfo(name=APP_NAME, version=APP_VERSION, license=APP_LICENSE)

    app.include_router(me_router, responses=ERROR_RESPONSES)
    app.include_router(tasks_router, responses=ERROR_RESPONSES)
    app.include_router(admin_router, responses=ERROR_RESPONSES)
    app.include_router(settings_router, responses=ERROR_RESPONSES)
    return app


app = create_app()


def run() -> None:
    """``physics-api`` 脚本入口：用 uvicorn 启动服务。"""
    import uvicorn

    uvicorn.run("api.app:app", host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    run()
