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


# 模糊搜索使用 ``LIKE ... ESCAPE`` 配套，需与各 ``*_filter_clause`` 子句中的
# ``ESCAPE`` 字符保持一致。
_LIKE_ESCAPE = "\\"


def _like_term(q: str) -> str:
    """把用户输入转义为 LIKE 模式并用 ``%`` 包裹。

    转义 LIKE 通配符 ``%`` / ``_`` 及转义字符本身，使其按字面量匹配，
    避免用户输入中的通配符产生非预期的全表匹配。需配合 SQL 中的
    ``ESCAPE '\\'`` 子句使用。
    """
    escaped = (
        q.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


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


def _user_filter_clause(q: Optional[str]) -> tuple[str, list[Any]]:
    """构造用户模糊搜索的 WHERE 子句与参数（匹配 user_id / label，全部参数化）。"""
    if not q:
        return "", []
    like = _like_term(q)
    return (
        " WHERE (user_id LIKE ? ESCAPE '\\' OR label LIKE ? ESCAPE '\\')",
        [like, like],
    )


def list_users(
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    order: str = "DESC",
) -> list[dict[str, Any]]:
    """列出用户（可按 user_id / label 模糊搜索），按创建时间排序并分页。"""
    direction = "ASC" if str(order).upper() == "ASC" else "DESC"
    where, params = _user_filter_clause(q)
    rows = db.query_all(
        f"SELECT * FROM users{where} "
        f"ORDER BY created_at {direction} LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    )
    return [dict(r) for r in rows]


def count_users(q: Optional[str] = None) -> int:
    """统计用户总数（可按 user_id / label 模糊搜索）。"""
    where, params = _user_filter_clause(q)
    row = db.query_one(f"SELECT COUNT(*) AS n FROM users{where}", tuple(params))
    return int(row["n"]) if row else 0


def update_user_label(user_id: str, label: str) -> bool:
    """更新用户 label；返回是否命中一条记录。"""
    if get_user(user_id) is None:
        return False
    db.execute("UPDATE users SET label = ? WHERE user_id = ?", (label, user_id))
    return True


def get_user_detail(user_id: str) -> Optional[dict[str, Any]]:
    """返回用户详情（含未吊销 token 数与任务数统计）。"""
    user = get_user(user_id)
    if user is None:
        return None
    tok = db.query_one(
        "SELECT COUNT(*) AS n FROM tokens WHERE user_id = ? AND revoked_at IS NULL",
        (user_id,),
    )
    tsk = db.query_one(
        "SELECT COUNT(*) AS n FROM tasks WHERE user_id = ?", (user_id,)
    )
    user["token_count"] = int(tok["n"]) if tok else 0
    user["task_count"] = int(tsk["n"]) if tsk else 0
    return user


def delete_user_cascade(user_id: str) -> list[str]:
    """删除用户及其 token、任务记录与进度事件。

    返回被删除任务的 ``task_id`` 列表，供路由层据此清理磁盘产物。
    """
    rows = db.query_all("SELECT task_id FROM tasks WHERE user_id = ?", (user_id,))
    task_ids = [r["task_id"] for r in rows]
    conn = db.get_connection()
    with db._lock:  # noqa: SLF001 - 复用同一把写锁，保证级联原子性
        for tid in task_ids:
            conn.execute("DELETE FROM task_events WHERE task_id = ?", (tid,))
        conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
    return task_ids


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


def _token_filter_clause(
    user_id: Optional[str], q: Optional[str]
) -> tuple[str, list[Any]]:
    """构造 token 过滤的 WHERE 子句与参数（user_id 精确 + id/label 模糊，全参数化）。"""
    clauses: list[str] = []
    params: list[Any] = []
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if q:
        like = _like_term(q)
        clauses.append("(id LIKE ? ESCAPE '\\' OR label LIKE ? ESCAPE '\\')")
        params.extend([like, like])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def resolve_token(plain: str) -> Optional[dict[str, Any]]:
    """按明文解析未吊销的 token，返回其元数据（含 user_id / role）。"""
    row = db.query_one(
        "SELECT * FROM tokens WHERE token_hash = ? AND revoked_at IS NULL",
        (hash_token(plain),),
    )
    return dict(row) if row else None


def list_tokens(
    user_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    order: str = "DESC",
) -> list[dict[str, Any]]:
    """列出 token 元数据（不含明文与哈希）。

    可按 ``user_id`` 过滤、按 ``q`` 模糊搜索（匹配 token id / label），并按创建时间
    排序分页。
    """
    direction = "ASC" if str(order).upper() == "ASC" else "DESC"
    where, params = _token_filter_clause(user_id, q)
    rows = db.query_all(
        "SELECT id, user_id, role, label, created_at, revoked_at "
        f"FROM tokens{where} ORDER BY created_at {direction} LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    )
    return [dict(r) for r in rows]


def count_tokens(user_id: Optional[str] = None, q: Optional[str] = None) -> int:
    """统计 token 总数（可按 user_id 过滤、按 q 模糊搜索）。"""
    where, params = _token_filter_clause(user_id, q)
    row = db.query_one(f"SELECT COUNT(*) AS n FROM tokens{where}", tuple(params))
    return int(row["n"]) if row else 0


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

def create_task(
    task_id: str, user_id: str, mode: str, topic: str, total_score: int,
    *, difficulty: str = "", source_material: str = "",
) -> None:
    """登记一个新任务（初始状态 ``queued``）。

    ``difficulty`` 与 ``source_material`` 一并持久化，使失败 / 中止的任务可被
    ``POST /api/tasks/{id}/retry`` 以原输入克隆重跑。
    """
    db.execute(
        "INSERT INTO tasks "
        "(task_id, user_id, status, mode, topic, difficulty, source_material, total_score, created_at) "
        "VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
        (task_id, user_id, mode, topic, difficulty, source_material, total_score, _now()),
    )


def set_task_status(task_id: str, status: str) -> None:
    """更新任务状态。"""
    db.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))


def set_task_phase(task_id: str, phase: str) -> None:
    """更新任务当前阶段（节点级进度）。"""
    db.execute("UPDATE tasks SET phase = ? WHERE task_id = ?", (phase, task_id))


def append_event(
    task_id: str, seq: int, phase: str, status: str,
    output: dict[str, Any] | None = None,
    *, occurrence_id: str = "", round_: int = 1,
) -> None:
    """追加一条阶段进度事件。

    Args:
        task_id: 任务标识。
        seq: 单调递增的事件序号（同一任务内）。
        phase: 阶段名（:class:`engine.state_machine.Phase` 的 ``name``）。
        status: ``running`` / ``completed``。
        output: 已格式化的阶段产出快照（``completed`` 时提供）。
        occurrence_id: 同一阶段同一次执行的稳定标识（``running``/``completed`` 共享），
            供前端无序配对，形如 ``"REVIEWING#2"``。
        round_: 重试轮次（从 1 起），便于前端按轮分组。
    """
    db.execute(
        "INSERT INTO task_events "
        "(task_id, seq, phase, status, occurrence_id, round, output_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id, seq, phase, status, occurrence_id, round_,
            json.dumps(output, ensure_ascii=False) if output is not None else None,
            _now(),
        ),
    )


def list_events(task_id: str) -> list[dict[str, Any]]:
    """按序返回某任务的全部进度事件（自动解析 ``output_json``）。"""
    rows = db.query_all(
        "SELECT seq, phase, status, occurrence_id, round, output_json, created_at "
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


_TASK_LIST_COLS = (
    "task_id, user_id, status, mode, topic, total_score, created_at, finished_at, error"
)


def _task_filter_clause(
    user_id: Optional[str],
    statuses: Optional[list[str]],
    mode: Optional[str],
    q: Optional[str],
) -> tuple[str, list[Any]]:
    """构造任务过滤的 WHERE 子句与参数（全部参数化，防注入）。"""
    clauses: list[str] = []
    params: list[Any] = []
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    if q:
        clauses.append("topic LIKE ? ESCAPE '\\'")
        params.append(_like_term(q))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_tasks(
    user_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    statuses: Optional[list[str]] = None,
    mode: Optional[str] = None,
    q: Optional[str] = None,
    order: str = "DESC",
) -> list[dict[str, Any]]:
    """列出任务（可按用户 / 状态 / 模式 / 主题过滤），按创建时间排序分页。"""
    direction = "ASC" if str(order).upper() == "ASC" else "DESC"
    where, params = _task_filter_clause(user_id, statuses, mode, q)
    rows = db.query_all(
        f"SELECT {_TASK_LIST_COLS} FROM tasks{where} "
        f"ORDER BY created_at {direction} LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    )
    return [dict(r) for r in rows]


def count_tasks(
    user_id: Optional[str] = None,
    statuses: Optional[list[str]] = None,
    mode: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    """统计满足过滤条件的任务总数。"""
    where, params = _task_filter_clause(user_id, statuses, mode, q)
    row = db.query_one(f"SELECT COUNT(*) AS n FROM tasks{where}", tuple(params))
    return int(row["n"]) if row else 0


def admin_stats() -> dict[str, Any]:
    """聚合管理端概览统计：任务 / 用户 / token / 用量与费用。"""
    status_rows = db.query_all(
        "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
    )
    status_counts = {r["status"]: int(r["n"]) for r in status_rows}
    task_total = sum(status_counts.values())

    active_tokens_row = db.query_one(
        "SELECT COUNT(*) AS n FROM tokens WHERE revoked_at IS NULL"
    )
    active_token_count = int(active_tokens_row["n"]) if active_tokens_row else 0

    # token 用量与费用存于各任务的 summary_json，需遍历汇总。
    total_tokens = 0
    total_cost = 0.0
    summ_rows = db.query_all(
        "SELECT summary_json FROM tasks WHERE summary_json IS NOT NULL"
    )
    for r in summ_rows:
        try:
            summary = json.loads(r["summary_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        usage = summary.get("token_usage") or {}
        if isinstance(usage, dict):
            total_tokens += int(usage.get("total_tokens", 0) or 0)
        cost = summary.get("api_cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)

    return {
        "task_total": task_total,
        "task_status_counts": status_counts,
        "user_count": count_users(),
        "active_token_count": active_token_count,
        "token_usage_total": total_tokens,
        "api_cost_usd_total": round(total_cost, 6),
    }


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
            "WHERE status IN ('running', 'queued', 'aborting')"
        )
        conn.commit()
        return cur.rowcount
