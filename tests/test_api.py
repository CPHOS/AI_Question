"""FastAPI 端点集成测试（鉴权、任务工作流、管理、隔离）。

使用 conftest 中的 ``client`` 夹具：隔离 DB / 产物目录、引导管理员 token、
并以假执行替换真实生成流程。
"""
import time


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _wait_done(client, token: str, task_id: str, timeout: float = 5.0) -> dict:
    """轮询任务直到进入终态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/tasks/{task_id}", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("done", "error", "aborted", "interrupted"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"任务未在 {timeout}s 内完成")


def _make_user_token(client, role: str = "user") -> str:
    """用管理员 token 创建用户并签发 token，返回明文。"""
    admin = client.admin_token
    user = client.post("/api/admin/users", headers=_auth(admin), json={"label": "t"}).json()
    tok = client.post(
        "/api/admin/tokens", headers=_auth(admin),
        json={"user_id": user["user_id"], "role": role},
    ).json()
    return tok["token"]


# ============ 系统 / 鉴权 ============

def test_health_is_public(client):
    assert client.get("/health").json() == {"detail": "ok"}


def test_missing_token_rejected(client):
    assert client.get("/api/tasks").status_code == 401


def test_invalid_token_rejected(client):
    assert client.get("/api/tasks", headers=_auth("bogus")).status_code == 401


def test_user_cannot_access_admin(client):
    user_token = _make_user_token(client)
    assert client.get("/api/admin/users", headers=_auth(user_token)).status_code == 403


def test_admin_bootstrap_works(client):
    assert client.get("/api/admin/users", headers=_auth(client.admin_token)).status_code == 200


# ============ 任务工作流 ============

def test_submit_poll_and_download(client):
    token = _make_user_token(client)
    resp = client.post(
        "/api/tasks", headers=_auth(token),
        json={"topic": "刚体力学", "total_score": 40},
    )
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]

    body = _wait_done(client, token, task_id)
    assert body["status"] == "done"
    assert body["summary"]["arbiter_decision"] == "PASS"

    arts = client.get(f"/api/tasks/{task_id}/artifacts", headers=_auth(token)).json()
    names = {a["name"] for a in arts}
    assert "final_latex" in names

    dl = client.get(f"/api/tasks/{task_id}/artifacts/final_latex", headers=_auth(token))
    assert dl.status_code == 200
    assert "cphos" in dl.text

    result = client.get(f"/api/tasks/{task_id}/result", headers=_auth(token)).json()
    assert "cphos" in result["final_latex"]


def test_submit_requires_topic_or_source(client):
    token = _make_user_token(client)
    resp = client.post("/api/tasks", headers=_auth(token), json={"topic": "", "source_material": ""})
    assert resp.status_code == 422


def test_task_history_lists_own_tasks(client):
    token = _make_user_token(client)
    client.post("/api/tasks", headers=_auth(token), json={"topic": "a"})
    client.post("/api/tasks", headers=_auth(token), json={"topic": "b"})
    rows = client.get("/api/tasks", headers=_auth(token)).json()
    assert len(rows) == 2


def test_upload_source_material(client):
    token = _make_user_token(client)
    resp = client.post(
        "/api/tasks/upload",
        headers=_auth(token),
        files={"file": ("sketch.txt", "一个关于电磁感应的思路".encode("utf-8"), "text/plain")},
        data={"mode": "idea_expansion", "total_score": "40"},
    )
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]
    body = _wait_done(client, token, task_id)
    assert body["status"] == "done"
    assert body["mode"] == "idea_expansion"


def test_delete_task_removes_artifacts(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    assert client.delete(f"/api/tasks/{task_id}", headers=_auth(token)).status_code == 200
    assert client.get(f"/api/tasks/{task_id}", headers=_auth(token)).status_code == 404


# ============ 多用户隔离 ============

def test_user_cannot_access_others_task(client):
    token_a = _make_user_token(client)
    token_b = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token_a, task_id)

    # B 访问 A 的任务 → 403
    assert client.get(f"/api/tasks/{task_id}", headers=_auth(token_b)).status_code == 403
    # B 的历史不含 A 的任务
    assert client.get("/api/tasks", headers=_auth(token_b)).json() == []


def test_admin_can_access_any_task(client):
    token_a = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token_a, task_id)

    admin = client.admin_token
    assert client.get(f"/api/tasks/{task_id}", headers=_auth(admin)).status_code == 200
    dl = client.get(f"/api/tasks/{task_id}/artifacts/final_latex", headers=_auth(admin))
    assert dl.status_code == 200
    # 全局任务列表可见
    all_tasks = client.get("/api/admin/tasks", headers=_auth(admin)).json()
    assert any(t["task_id"] == task_id for t in all_tasks)


# ============ 管理：token 生命周期 ============

def test_revoked_token_cannot_be_used(client):
    admin = client.admin_token
    user = client.post("/api/admin/users", headers=_auth(admin), json={}).json()
    tok = client.post(
        "/api/admin/tokens", headers=_auth(admin),
        json={"user_id": user["user_id"]},
    ).json()

    assert client.get("/api/tasks", headers=_auth(tok["token"])).status_code == 200
    assert client.delete(f"/api/admin/tokens/{tok['id']}", headers=_auth(admin)).status_code == 200
    assert client.get("/api/tasks", headers=_auth(tok["token"])).status_code == 401


def test_create_token_for_missing_user_404(client):
    admin = client.admin_token
    resp = client.post(
        "/api/admin/tokens", headers=_auth(admin),
        json={"user_id": "user_nonexistent"},
    )
    assert resp.status_code == 404


def test_openapi_schema_available(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "CPhOS 物理竞赛题生成 API"
    assert "/api/tasks" in spec["paths"]


# ============ 节点级进度 ============

def test_progress_timeline_after_completion(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    prog = client.get(f"/api/tasks/{task_id}/progress", headers=_auth(token)).json()
    assert prog["task_id"] == task_id
    assert prog["status"] == "done"
    assert prog["phase"] == "DONE"

    events = prog["events"]
    assert len(events) > 0
    # seq 单调递增
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    # 含格式化产出与中文标签
    completed = [e for e in events if e["status"] == "completed"]
    assert completed
    review = next((e for e in completed if e["phase"] == "REVIEWING"), None)
    assert review is not None
    assert review["phase_label"] == "多维审核"
    assert review["output"]["kind"] == "review"
    assert len(review["output"]["reviews"]) == 4


def test_status_includes_phase(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    body = _wait_done(client, token, task_id)
    assert body["phase"] == "DONE"


def test_progress_respects_ownership(client):
    token_a = _make_user_token(client)
    token_b = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token_a, task_id)
    assert client.get(f"/api/tasks/{task_id}/progress", headers=_auth(token_b)).status_code == 403


def test_delete_removes_progress_events(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)
    assert client.delete(f"/api/tasks/{task_id}", headers=_auth(token)).status_code == 200
    # 任务已不存在 → 进度查询 404
    assert client.get(f"/api/tasks/{task_id}/progress", headers=_auth(token)).status_code == 404
