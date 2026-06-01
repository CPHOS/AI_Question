"""
SQLite 持久化层。

提供进程级单例连接（``check_same_thread=False`` + 全局锁），用于在多线程的
FastAPI 后端中安全访问用户、token 与任务元数据。建表通过 :func:`init_db`
幂等执行。本层只负责连接与 schema；具体的领域读写见 :mod:`api.store`。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from config.config import DB_PATH, logger

# 同一进程共享一个连接 + 一把锁，序列化写操作，避免 SQLite 多线程写冲突。
_conn: sqlite3.Connection | None = None
_lock = threading.RLock()
_db_path: Path = DB_PATH


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    id         TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
);
CREATE INDEX IF NOT EXISTS idx_tokens_hash ON tokens (token_hash);

CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    status       TEXT NOT NULL,
    phase        TEXT NOT NULL DEFAULT '',
    mode         TEXT NOT NULL DEFAULT '',
    topic        TEXT NOT NULL DEFAULT '',
    difficulty   TEXT NOT NULL DEFAULT '',
    source_material TEXT NOT NULL DEFAULT '',
    total_score  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    finished_at  TEXT,
    summary_json TEXT,
    error        TEXT,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks (user_id);

CREATE TABLE IF NOT EXISTS task_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    phase         TEXT NOT NULL,
    status        TEXT NOT NULL,
    occurrence_id TEXT NOT NULL DEFAULT '',
    round         INTEGER NOT NULL DEFAULT 1,
    output_json   TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id)
);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events (task_id, seq);

-- ===== LLM 服务商凭据（机密入库，API 读取时掩码） =====
CREATE TABLE IF NOT EXISTS llm_providers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    api_key     TEXT NOT NULL DEFAULT '',
    base_url    TEXT NOT NULL DEFAULT '',
    proxy       TEXT NOT NULL DEFAULT '',
    timeout     INTEGER NOT NULL DEFAULT 600,
    max_retries INTEGER NOT NULL DEFAULT 3,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- ===== 模型配置（「模型配置」实体，引用某个服务商凭据） =====
CREATE TABLE IF NOT EXISTS model_configs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    provider_id TEXT NOT NULL,
    model       TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.0,
    max_tokens  INTEGER NOT NULL DEFAULT 4096,
    streaming   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (provider_id) REFERENCES llm_providers (id)
);

-- ===== Agent 配置（每个 Agent 角色绑定到一个模型配置） =====
CREATE TABLE IF NOT EXISTS agent_bindings (
    role            TEXT PRIMARY KEY,
    model_config_id TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (model_config_id) REFERENCES model_configs (id)
);

-- ===== 运行期应用设置（流程开关 / 数值阈值） =====
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """对既有库做幂等增量迁移（新增列等）。

    ``CREATE TABLE IF NOT EXISTS`` 不会给已存在的表补列，因此对历史库需要
    显式 ``ALTER TABLE``。逐列检查后再添加，保证幂等且向后兼容。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "phase" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN phase TEXT NOT NULL DEFAULT ''")
    if "difficulty" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN difficulty TEXT NOT NULL DEFAULT ''")
    if "source_material" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN source_material TEXT NOT NULL DEFAULT ''")

    event_cols = {row["name"] for row in conn.execute("PRAGMA table_info(task_events)").fetchall()}
    if "occurrence_id" not in event_cols:
        conn.execute("ALTER TABLE task_events ADD COLUMN occurrence_id TEXT NOT NULL DEFAULT ''")
    if "round" not in event_cols:
        conn.execute("ALTER TABLE task_events ADD COLUMN round INTEGER NOT NULL DEFAULT 1")

    prov_cols = {row["name"] for row in conn.execute("PRAGMA table_info(llm_providers)").fetchall()}
    if "proxy" not in prov_cols:
        conn.execute("ALTER TABLE llm_providers ADD COLUMN proxy TEXT NOT NULL DEFAULT ''")


def configure(db_path: Path) -> None:
    """覆盖数据库路径（主要供测试隔离使用），并关闭已有连接。"""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _db_path = db_path
    # 切换数据库后，运行期设置缓存与就绪标志必须失效，避免读到旧库的设置。
    from config import runtime
    runtime.invalidate_cache()


def get_connection() -> sqlite3.Connection:
    """返回进程级单例连接（按需创建并建表）。"""
    global _conn
    with _lock:
        if _conn is None:
            _db_path.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(_db_path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL;")
            _conn.execute("PRAGMA foreign_keys=ON;")
        return _conn


def init_db() -> None:
    """幂等创建所有表与索引，执行增量迁移，并播种默认 LLM / 应用设置。"""
    conn = get_connection()
    with _lock:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
    # 首次初始化时，从 .env 种子默认值播种模型 / 服务商 / Agent 绑定 / 应用设置。
    from api import settings_store
    settings_store.seed_defaults()
    logger.info("[db] 数据库已就绪: %s", _db_path)


def execute(sql: str, params: tuple = ()) -> None:
    """执行写语句并提交。"""
    conn = get_connection()
    with _lock:
        conn.execute(sql, params)
        conn.commit()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    """查询单行。"""
    conn = get_connection()
    with _lock:
        cur = conn.execute(sql, params)
        return cur.fetchone()


def query_all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """查询多行。"""
    conn = get_connection()
    with _lock:
        cur = conn.execute(sql, params)
        return cur.fetchall()
