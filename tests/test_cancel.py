"""任务取消（协作式中断）与 SSE 进度推送测试。"""
import time

import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_user_token(client, role: str = "user") -> str:
    admin = client.admin_token
    user = client.post("/api/admin/users", headers=_auth(admin), json={"label": "t"}).json()
    tok = client.post(
        "/api/admin/tokens", headers=_auth(admin),
        json={"user_id": user["user_id"], "role": role},
    ).json()
    return tok["token"]


def _wait_status(client, token, task_id, wanted, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/tasks/{task_id}", headers=_auth(token)).json()["status"]
        if st in wanted:
            return st
        time.sleep(0.02)
    raise AssertionError(f"任务未在 {timeout}s 内进入 {wanted}")


# ============ 状态机层：协作式取消 ============

def test_state_machine_raises_on_cancel():
    from engine.state_machine import build_graph, TaskCancelled

    sm = build_graph()
    with pytest.raises(TaskCancelled):
        sm.run({}, should_cancel=lambda: True)
    assert sm.phase.name == "ABORTED"


def test_runner_marks_cancelled(monkeypatch, api_db, tmp_path):
    from app import runner

    monkeypatch.setattr("app.runner.budget_snapshot", lambda: {})
    monkeypatch.setattr("app.runner.budget_delta_usd", lambda a, b: 0.0)
    result = runner.execute_task({}, "task_c", tmp_path, should_cancel=lambda: True)
    assert result.cancelled is True
    assert result.error_msg == ""


# ============ API 层：取消 ============

def test_cancel_running_task(client, monkeypatch):
    from app.runner import RunResult

    def blocking(initial_state, task_id, output_dir, write=True, on_phase=None, should_cancel=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        for _ in range(2000):
            if should_cancel and should_cancel():
                return RunResult(task_id=task_id, final_state=dict(initial_state), cancelled=True)
            time.sleep(0.01)
        return RunResult(task_id=task_id, final_state=dict(initial_state))

    monkeypatch.setattr("api.jobs.execute_task", blocking)

    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_status(client, token, task_id, {"running"})

    resp = client.post(f"/api/tasks/{task_id}/cancel", headers=_auth(token))
    assert resp.status_code == 202
    assert resp.json()["status"] == "aborting"

    assert _wait_status(client, token, task_id, {"aborted"}) == "aborted"


def test_cancel_terminal_task_conflict(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_status(client, token, task_id, {"done", "error", "aborted"})
    resp = client.post(f"/api/tasks/{task_id}/cancel", headers=_auth(token))
    assert resp.status_code == 409


def test_cancel_respects_ownership(client):
    token_a = _make_user_token(client)
    token_b = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    assert client.post(f"/api/tasks/{task_id}/cancel", headers=_auth(token_b)).status_code in (403, 409)


# ============ SSE ============

def test_sse_events_stream(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_status(client, token, task_id, {"done"})

    collected = ""
    with client.stream("GET", f"/api/tasks/{task_id}/events", headers=_auth(token)) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        # 任务已终态：生成器回放全部阶段事件 + 一条终态 status 后即关闭流。
        for line in r.iter_lines():
            collected += line + "\n"
    assert "event: phase" in collected
    assert "event: status" in collected
    assert '"status": "done"' in collected


def test_sse_respects_ownership(client):
    token_a = _make_user_token(client)
    token_b = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    _wait_status(client, token_a, task_id, {"done"})
    assert client.get(f"/api/tasks/{task_id}/events", headers=_auth(token_b)).status_code == 403
