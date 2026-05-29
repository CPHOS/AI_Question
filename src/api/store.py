"""
领域仓储层：用户、token、任务元数据的读写。

Token 安全
==========
Token 明文从不入库；仅以 SHA-256 哈希存储。明文由 :func:`create_token`
在创建时一次性返回，调用方（管理员）负责安全分发。校验时对入参明文做同样
哈希后比对。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from api import db


def _now() -> str:
    """返回 UTC ISO8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def hash_token(token: str) -> str:
    """计算 token 明文的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ============ 用户 ============

def create_user(label: str = "") -> dict[str, Any]:
    """创建用户并返回其记录。"""
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    created_at = _now()
    db.execute(
        "INSERT INTO users (user_id, label, created_at) VALUES (?, ?, ?)",
        (user_id, label, created_at),
    )
    return {"user_id": user_id, "label": label, "created_at": created_at}


def get_user(user_id: str) -> Optional[dict[str, Any]]:
    """按 ID 查询用户。"""
    row = db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    """列出全部用户（按创建时间倒序）。"""
    rows = db.query_all("SELECT * FROM users ORDER BY created_at DESC")
    return [dict(r) for r in rows]


# ============ Token ============

def create_token(user_id: str, role: str = "user", label: str = "") -> dict[str, Any]:
    """为用户生成一个新的不透明 token。

    Returns:
        包含 ``token``（一次性明文）、``id``、``user_id``、``role`` 等字段的字典。
    """
    if role not in ("admin", "user"):
        raise ValueError(f"非法角色: {role}")
    plain = secrets.token_urlsafe(32)
    token_id = f"tok_{uuid.uuid4().hex[:12]}"
    created_at = _now()
    db.execute(
        "INSERT INTO tokens (id, token_hash, user_id, role, label, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token_id, hash_token(plain), user_id, role, label, created_at),
    )
    return {
        "id": token_id,
        "token": plain,  # 仅此一次返回明文
        "user_id": user_id,
        "role": role,
        "label": label,
        "created_at": created_at,
    }


def insert_token_hash(
    token_hash: str, user_id: str, role: str = "admin", label: str = ""
) -> str:
    """直接以哈希插入 token（用于引导管理员）。返回 token id。"""
    token_id = f"tok_{uuid.uuid4().hex[:12]}"
    db.execute(
        "INSERT INTO tokens (id, token_hash, user_id, role, label, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token_id, token_hash, user_id, role, label, _now()),
    )
    return token_id


def resolve_token(plain: str) -> Optional[dict[str, Any]]:
    """按明文解析未吊销的 token，返回其元数据（含 user_id / role）。"""
    row = db.query_one(
        "SELECT * FROM tokens WHERE token_hash = ? AND revoked_at IS NULL",
        (hash_token(plain),),
    )
    return dict(row) if row else None


def list_tokens(user_id: Optional[str] = None) -> list[dict[str, Any]]:
    """列出 token 元数据（不含明文与哈希）。"""
    if user_id:
        rows = db.query_all(
            "SELECT id, user_id, role, label, created_at, revoked_at "
            "FROM tokens WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
    else:
        rows = db.query_all(
            "SELECT id, user_id, role, label, created_at, revoked_at "
            "FROM tokens ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


def revoke_token(token_id: str) -> bool:
    """吊销 token；返回是否命中一条未吊销记录。"""
    row = db.query_one(
        "SELECT id FROM tokens WHERE id = ? AND revoked_at IS NULL", (token_id,)
    )
    if not row:
        return False
    db.execute("UPDATE tokens SET revoked_at = ? WHERE id = ?", (_now(), token_id))
    return True


def has_admin() -> bool:
    """库内是否已存在未吊销的管理员 token。"""
    row = db.query_one(
        "SELECT 1 FROM tokens WHERE role = 'admin' AND revoked_at IS NULL LIMIT 1"
    )
    return row is not None


# ============ 任务 ============

def create_task(task_id: str, user_id: str, mode: str, topic: str, total_score: int) -> None:
    """登记一个新任务（初始状态 ``queued``）。"""
    db.execute(
        "INSERT INTO tasks (task_id, user_id, status, mode, topic, total_score, created_at) "
        "VALUES (?, ?, 'queued', ?, ?, ?, ?)",
        (task_id, user_id, mode, topic, total_score, _now()),
    )


def set_task_status(task_id: str, status: str) -> None:
    """更新任务状态。"""
    db.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))


def set_task_phase(task_id: str, phase: str) -> None:
    """更新任务当前阶段（节点级进度）。"""
    db.execute("UPDATE tasks SET phase = ? WHERE task_id = ?", (phase, task_id))


def append_event(
    task_id: str, seq: int, phase: str, status: str, output: dict[str, Any] | None = None
) -> None:
    """追加一条阶段进度事件。

    Args:
        task_id: 任务标识。
        seq: 单调递增的事件序号（同一任务内）。
        phase: 阶段名（:class:`engine.state_machine.Phase` 的 ``name``）。
        status: ``running`` / ``completed``。
        output: 已格式化的阶段产出快照（``completed`` 时提供）。
    """
    db.execute(
        "INSERT INTO task_events (task_id, seq, phase, status, output_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            task_id, seq, phase, status,
            json.dumps(output, ensure_ascii=False) if output is not None else None,
            _now(),
        ),
    )


def list_events(task_id: str) -> list[dict[str, Any]]:
    """按序返回某任务的全部进度事件（自动解析 ``output_json``）。"""
    rows = db.query_all(
        "SELECT seq, phase, status, output_json, created_at "
        "FROM task_events WHERE task_id = ? ORDER BY seq ASC",
        (task_id,),
    )
    events: list[dict[str, Any]] = []
    for r in rows:
        data = dict(r)
        raw = data.pop("output_json")
        data["output"] = json.loads(raw) if raw else None
        events.append(data)
    return events


def finish_task(
    task_id: str, status: str, summary: dict[str, Any] | None = None, error: str = ""
) -> None:
    """标记任务结束并写入摘要与错误信息。"""
    db.execute(
        "UPDATE tasks SET status = ?, finished_at = ?, summary_json = ?, error = ? "
        "WHERE task_id = ?",
        (status, _now(), json.dumps(summary or {}, ensure_ascii=False), error, task_id),
    )


def get_task(task_id: str) -> Optional[dict[str, Any]]:
    """按 ID 查询任务；自动解析 ``summary_json``。"""
    row = db.query_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    if not row:
        return None
    data = dict(row)
    data["summary"] = json.loads(data.pop("summary_json") or "{}")
    return data


def list_tasks(user_id: Optional[str] = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """列出任务（可按用户过滤），按创建时间倒序分页。"""
    if user_id:
        rows = db.query_all(
            "SELECT task_id, user_id, status, mode, topic, total_score, created_at, finished_at, error "
            "FROM tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        )
    else:
        rows = db.query_all(
            "SELECT task_id, user_id, status, mode, topic, total_score, created_at, finished_at, error "
            "FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return [dict(r) for r in rows]


def delete_task(task_id: str) -> None:
    """删除任务元数据记录及其进度事件。"""
    db.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))


def mark_interrupted_running() -> int:
    """把残留的 ``running`` / ``queued`` 任务标记为 ``interrupted``。

    进程重启后内存执行器不恢复，此函数保证历史状态一致。返回受影响行数。
    """
    conn = db.get_connection()
    with db._lock:  # noqa: SLF001 - 复用同一把写锁
        cur = conn.execute(
            "UPDATE tasks SET status = 'interrupted' "
            "WHERE status IN ('running', 'queued')"
        )
        conn.commit()
        return cur.rowcount
