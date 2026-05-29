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
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["components"]["db"]["status"] == "ok"
    assert body["components"]["worker"]["status"] == "ok"


def test_version_is_public(client):
    body = client.get("/version").json()
    assert body["name"] == "physics-generator"
    assert body["license"] == "AGPL-3.0-or-later"
    assert isinstance(body["version"], str) and body["version"]


def test_missing_token_rejected(client):
    assert client.get("/api/tasks").status_code == 401


def test_invalid_token_rejected(client):
    assert client.get("/api/tasks", headers=_auth("bogus")).status_code == 401


def test_user_cannot_access_admin(client):
    user_token = _make_user_token(client)
    assert client.get("/api/admin/users", headers=_auth(user_token)).status_code == 403


def test_admin_bootstrap_works(client):
    assert client.get("/api/admin/users", headers=_auth(client.admin_token)).status_code == 200


# ============ 统一错误契约 ============

def test_error_body_has_stable_code(client):
    # 401：缺少 token。
    body = client.get("/api/tasks").json()
    assert body == {"code": "unauthorized", "detail": "缺少 Bearer token"}

    # 403：普通用户访问管理端。
    user_token = _make_user_token(client)
    forbidden = client.get("/api/admin/users", headers=_auth(user_token)).json()
    assert forbidden["code"] == "forbidden"

    # 404：任务不存在 → task_not_found。
    missing = client.get("/api/tasks/nope", headers=_auth(user_token))
    assert missing.status_code == 404
    assert missing.json()["code"] == "task_not_found"


def test_request_validation_keeps_default_422_shape(client):
    token = _make_user_token(client)
    # 业务校验（topic 与 source 同时为空）→ {code, detail}。
    biz = client.post("/api/tasks", headers=_auth(token), json={})
    assert biz.status_code == 422
    assert biz.json()["code"] == "unprocessable_entity"
    # 结构校验（total_score 越界）→ 保留 FastAPI 默认 detail 列表。
    bad = client.post(
        "/api/tasks", headers=_auth(token),
        json={"topic": "x", "total_score": 9999},
    )
    assert bad.status_code == 422
    assert isinstance(bad.json()["detail"], list)


def test_admin_users_search_and_order(client):
    admin = client.admin_token
    client.post("/api/admin/users", headers=_auth(admin), json={"label": "alpha"})
    client.post("/api/admin/users", headers=_auth(admin), json={"label": "beta"})

    hit = client.get("/api/admin/users?q=alpha", headers=_auth(admin)).json()
    assert hit["total"] == 1
    assert hit["items"][0]["label"] == "alpha"

    asc = client.get("/api/admin/users?order=ASC", headers=_auth(admin)).json()
    desc = client.get("/api/admin/users?order=DESC", headers=_auth(admin)).json()
    asc_times = [u["created_at"] for u in asc["items"]]
    desc_times = [u["created_at"] for u in desc["items"]]
    assert asc_times == sorted(asc_times)
    assert desc_times == sorted(desc_times, reverse=True)


def test_admin_users_search_escapes_like_wildcards(client):
    """LIKE 通配符（% / _）应按字面量匹配，而非匹配所有记录。"""
    admin = client.admin_token
    client.post("/api/admin/users", headers=_auth(admin), json={"label": "100%done"})
    client.post("/api/admin/users", headers=_auth(admin), json={"label": "plain"})

    # 字面 '%' 仅命中含该字符的标签，不应匹配 "plain"。
    hit = client.get("/api/admin/users?q=%25", headers=_auth(admin)).json()
    labels = [u["label"] for u in hit["items"]]
    assert labels == ["100%done"]


def test_admin_tokens_search(client):
    admin = client.admin_token
    user = client.post("/api/admin/users", headers=_auth(admin), json={"label": "u"}).json()
    client.post(
        "/api/admin/tokens", headers=_auth(admin),
        json={"user_id": user["user_id"], "role": "user", "label": "ci-runner"},
    )
    hit = client.get("/api/admin/tokens?q=ci-runner", headers=_auth(admin)).json()
    assert hit["total"] == 1
    assert hit["items"][0]["label"] == "ci-runner"


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


def test_artifact_disposition_inline_vs_attachment(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    # 默认 attachment（下载）
    dl = client.get(f"/api/tasks/{task_id}/artifacts/final_latex", headers=_auth(token))
    assert dl.status_code == 200
    assert dl.headers["content-disposition"].startswith("attachment")

    # inline（浏览器内联预览）
    inline = client.get(
        f"/api/tasks/{task_id}/artifacts/final_latex?disposition=inline", headers=_auth(token)
    )
    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline")

    # 非法 disposition → 422
    bad = client.get(
        f"/api/tasks/{task_id}/artifacts/final_latex?disposition=bogus", headers=_auth(token)
    )
    assert bad.status_code == 422


def test_result_exposes_compile_status_fields(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    result = client.get(f"/api/tasks/{task_id}/result", headers=_auth(token)).json()
    # 字段始终存在（无 log.json 时为空 / False），便于前端无条件读取
    assert "latex_compile_status" in result
    assert "figure_compile_status" in result
    assert result["final_pdf_available"] is False


def test_compile_endpoint_produces_pdf(client, monkeypatch):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    def _fake_recompile(tid, output_dir):
        (output_dir / f"{tid}_final.pdf").write_bytes(b"%PDF-1.4\n")
        return {
            "latex_compile_status": {"ok": True, "detail": "", "template_dir": "t"},
            "figure_compile_status": {},
        }

    monkeypatch.setattr("api.jobs.recompile_outputs", _fake_recompile)
    resp = client.post(f"/api/tasks/{task_id}/compile", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["latex_compile_status"]["ok"] is True
    assert body["final_pdf_available"] is True

    # 编译产出的 PDF 现在应可作为产物内联预览
    dl = client.get(
        f"/api/tasks/{task_id}/artifacts/final_pdf?disposition=inline", headers=_auth(token)
    )
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.headers["content-disposition"].startswith("inline")


def test_compile_endpoint_404_for_unknown_task(client):
    token = _make_user_token(client)
    assert client.post("/api/tasks/task_nope/compile", headers=_auth(token)).status_code == 404


def test_compile_endpoint_409_when_no_final_latex(client, monkeypatch):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    def _raise(tid, output_dir):
        raise FileNotFoundError(f"任务 {tid} 无 final_latex 产物，无法编译")

    monkeypatch.setattr("api.jobs.recompile_outputs", _raise)
    resp = client.post(f"/api/tasks/{task_id}/compile", headers=_auth(token))
    assert resp.status_code == 409


def test_task_history_lists_own_tasks(client):
    token = _make_user_token(client)
    client.post("/api/tasks", headers=_auth(token), json={"topic": "a"})
    client.post("/api/tasks", headers=_auth(token), json={"topic": "b"})
    page = client.get("/api/tasks", headers=_auth(token)).json()
    assert page["total"] == 2
    assert len(page["items"]) == 2


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


def test_delete_task_discards_compile_lock(client, monkeypatch):
    """删除任务后应同时清理其编译锁，避免注册表无限增长。"""
    from api import jobs

    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    # 触发一次按需编译以惰性创建该任务的编译锁。
    monkeypatch.setattr(
        "api.jobs.recompile_outputs",
        lambda tid, output_dir: {
            "latex_compile_status": {"ok": True, "detail": "", "template_dir": "t"},
            "figure_compile_status": {},
        },
    )
    client.post(f"/api/tasks/{task_id}/compile", headers=_auth(token))
    assert task_id in jobs._compile_locks

    assert client.delete(f"/api/tasks/{task_id}", headers=_auth(token)).status_code == 200
    assert task_id not in jobs._compile_locks


# ============ 多用户隔离 ============

def test_user_cannot_access_others_task(client):
    token_a = _make_user_token(client)
    token_b = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token_a, task_id)

    # B 访问 A 的任务 → 403
    assert client.get(f"/api/tasks/{task_id}", headers=_auth(token_b)).status_code == 403
    # B 的历史不含 A 的任务
    assert client.get("/api/tasks", headers=_auth(token_b)).json()["items"] == []


def test_admin_can_access_any_task(client):
    token_a = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token_a, task_id)

    admin = client.admin_token
    assert client.get(f"/api/tasks/{task_id}", headers=_auth(admin)).status_code == 200
    dl = client.get(f"/api/tasks/{task_id}/artifacts/final_latex", headers=_auth(admin))
    assert dl.status_code == 200
    # 全局任务列表可见
    all_tasks = client.get("/api/admin/tasks", headers=_auth(admin)).json()["items"]
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


# ============ #1 GET /api/me ============

def test_me_returns_user_role_and_label(client):
    admin = client.admin_token
    user = client.post("/api/admin/users", headers=_auth(admin), json={"label": "team-a"}).json()
    tok = client.post(
        "/api/admin/tokens", headers=_auth(admin),
        json={"user_id": user["user_id"], "role": "user", "label": "dev"},
    ).json()

    me = client.get("/api/me", headers=_auth(tok["token"])).json()
    assert me["user_id"] == user["user_id"]
    assert me["role"] == "user"
    assert me["label"] == "team-a"
    assert me["token_id"] == tok["id"]


def test_me_admin_role(client):
    me = client.get("/api/me", headers=_auth(client.admin_token)).json()
    assert me["role"] == "admin"


def test_me_requires_token(client):
    assert client.get("/api/me").status_code == 401


# ============ #2 分页元数据 ============

def test_task_list_pagination_metadata(client):
    token = _make_user_token(client)
    for t in ("a", "b", "c"):
        client.post("/api/tasks", headers=_auth(token), json={"topic": t})
    page = client.get("/api/tasks?limit=2&offset=0", headers=_auth(token)).json()
    assert page["total"] == 3
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert len(page["items"]) == 2


def test_admin_users_tokens_pagination(client):
    admin = client.admin_token
    users = client.get("/api/admin/users", headers=_auth(admin)).json()
    assert "items" in users and "total" in users
    tokens = client.get("/api/admin/tokens", headers=_auth(admin)).json()
    assert "items" in tokens and "total" in tokens


# ============ #3 产物下载 MIME / Content-Disposition ============

def test_download_sets_content_type_and_disposition(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    dl = client.get(f"/api/tasks/{task_id}/artifacts/final_latex", headers=_auth(token))
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/x-tex")
    assert "attachment" in dl.headers.get("content-disposition", "")
    assert task_id in dl.headers["content-disposition"]


# ============ #5 过滤与排序 ============

def test_task_list_status_filter(client):
    token = _make_user_token(client)
    for t in ("a", "b"):
        tid = client.post("/api/tasks", headers=_auth(token), json={"topic": t}).json()["task_id"]
        _wait_done(client, token, tid)
    done = client.get("/api/tasks?status=done", headers=_auth(token)).json()
    assert done["total"] == 2
    none = client.get("/api/tasks?status=error", headers=_auth(token)).json()
    assert none["total"] == 0


def test_task_list_topic_search(client):
    token = _make_user_token(client)
    client.post("/api/tasks", headers=_auth(token), json={"topic": "电磁感应"})
    client.post("/api/tasks", headers=_auth(token), json={"topic": "刚体力学"})
    hit = client.get("/api/tasks?q=电磁", headers=_auth(token)).json()
    assert hit["total"] == 1
    assert hit["items"][0]["topic"] == "电磁感应"


# ============ #7 用户管理 ============

def test_user_detail_update_and_delete(client):
    admin = client.admin_token
    user = client.post("/api/admin/users", headers=_auth(admin), json={"label": "old"}).json()
    uid = user["user_id"]
    token = client.post(
        "/api/admin/tokens", headers=_auth(admin), json={"user_id": uid},
    ).json()["token"]
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    detail = client.get(f"/api/admin/users/{uid}", headers=_auth(admin)).json()
    assert detail["task_count"] == 1
    assert detail["token_count"] == 1

    upd = client.patch(f"/api/admin/users/{uid}", headers=_auth(admin), json={"label": "new"})
    assert upd.status_code == 200
    assert upd.json()["label"] == "new"

    delr = client.delete(f"/api/admin/users/{uid}", headers=_auth(admin))
    assert delr.status_code == 200
    # 用户删除后其 token 失效，任务记录消失
    assert client.get("/api/me", headers=_auth(token)).status_code == 401
    assert client.get(f"/api/admin/users/{uid}", headers=_auth(admin)).status_code == 404


def test_update_missing_user_404(client):
    admin = client.admin_token
    assert client.patch(
        "/api/admin/users/user_nope", headers=_auth(admin), json={"label": "x"}
    ).status_code == 404


# ============ #8 管理端统计 ============

def test_admin_stats(client):
    admin = client.admin_token
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    stats = client.get("/api/admin/stats", headers=_auth(admin)).json()
    assert stats["task_total"] >= 1
    assert stats["task_status_counts"].get("done", 0) >= 1
    assert stats["user_count"] >= 1
    assert stats["active_token_count"] >= 1
    assert stats["token_usage_total"] >= 2  # fake 每任务 total_tokens=2


def test_stats_requires_admin(client):
    token = _make_user_token(client)
    assert client.get("/api/admin/stats", headers=_auth(token)).status_code == 403


# ============ #10 upload total_score 校验 ============

def test_upload_total_score_validation(client):
    token = _make_user_token(client)
    resp = client.post(
        "/api/tasks/upload",
        headers=_auth(token),
        files={"file": ("s.txt", "思路".encode("utf-8"), "text/plain")},
        data={"total_score": "5"},
    )
    assert resp.status_code == 422


# ============ 产物打包下载（zip） ============

def test_artifacts_archive_download(client):
    import io
    import zipfile

    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)

    resp = client.get(f"/api/tasks/{task_id}/artifacts/archive", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert f"{task_id}.zip" in resp.headers.get("content-disposition", "")

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any(n.endswith("_final.tex") for n in names)
    assert zf.read(f"{task_id}_final.tex").decode("utf-8").strip()


def test_artifacts_archive_requires_ownership(client):
    token_a = _make_user_token(client)
    token_b = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token_a, task_id)
    assert client.get(
        f"/api/tasks/{task_id}/artifacts/archive", headers=_auth(token_b)
    ).status_code == 403


# ============ 失败 / 中止任务重跑 ============

def test_retry_clones_failed_task(client):
    from api import store

    token = _make_user_token(client)
    task_id = client.post(
        "/api/tasks", headers=_auth(token),
        json={"topic": "电磁感应", "source_material": "原始素材文本", "total_score": 50},
    ).json()["task_id"]
    _wait_done(client, token, task_id)

    # 原任务持久化了 source_material / difficulty，可用于克隆重跑。
    original = store.get_task(task_id)
    assert original["source_material"] == "原始素材文本"

    # 强制为失败终态后重试。
    store.finish_task(task_id, "error", summary={}, error="boom")
    resp = client.post(f"/api/tasks/{task_id}/retry", headers=_auth(token))
    assert resp.status_code == 202
    new_id = resp.json()["task_id"]
    assert new_id != task_id

    body = _wait_done(client, token, new_id)
    assert body["status"] == "done"
    clone = store.get_task(new_id)
    assert clone["topic"] == "电磁感应"
    assert clone["source_material"] == "原始素材文本"
    assert clone["total_score"] == 50


def test_retry_rejects_done_task(client):
    token = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token, task_id)  # 终态为 done，不可重试
    assert client.post(f"/api/tasks/{task_id}/retry", headers=_auth(token)).status_code == 409


def test_retry_requires_ownership(client):
    from api import store

    token_a = _make_user_token(client)
    token_b = _make_user_token(client)
    task_id = client.post("/api/tasks", headers=_auth(token_a), json={"topic": "x"}).json()["task_id"]
    _wait_done(client, token_a, task_id)
    store.finish_task(task_id, "error", summary={}, error="boom")
    assert client.post(f"/api/tasks/{task_id}/retry", headers=_auth(token_b)).status_code == 403

