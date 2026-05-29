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
    total_score  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    finished_at  TEXT,
    summary_json TEXT,
    error        TEXT,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks (user_id);

CREATE TABLE IF NOT EXISTS task_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    phase       TEXT NOT NULL,
    status      TEXT NOT NULL,
    output_json TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks (task_id)
);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events (task_id, seq);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """对既有库做幂等增量迁移（新增列等）。

    ``CREATE TABLE IF NOT EXISTS`` 不会给已存在的表补列，因此对历史库需要
    显式 ``ALTER TABLE``。逐列检查后再添加，保证幂等且向后兼容。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "phase" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN phase TEXT NOT NULL DEFAULT ''")


def configure(db_path: Path) -> None:
    """覆盖数据库路径（主要供测试隔离使用），并关闭已有连接。"""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _db_path = db_path


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
    """幂等创建所有表与索引，并执行增量迁移。"""
    conn = get_connection()
    with _lock:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
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
