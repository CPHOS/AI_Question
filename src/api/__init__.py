"""CPhOS 物理竞赛题生成系统的后端 API 包。

模块组成：

- :mod:`api.db` — SQLite 连接与 schema。
- :mod:`api.store` — 用户 / token / 任务仓储。
- :mod:`api.auth` — Bearer token 鉴权依赖。
- :mod:`api.jobs` — 后台任务执行器与产物枚举。
- :mod:`api.schemas` — 请求 / 响应 pydantic 模型。
- :mod:`api.routes_tasks` / :mod:`api.routes_admin` — 路由。
- :mod:`api.app` — FastAPI 应用工厂与启动入口。
"""
