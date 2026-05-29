"""api.store 与 api.auth 的鉴权 / 仓储单元测试。"""
import pytest


def test_hash_token_is_stable_and_not_plaintext(api_db):
    from api import store

    h1 = store.hash_token("secret")
    h2 = store.hash_token("secret")
    assert h1 == h2
    assert h1 != "secret"
    assert len(h1) == 64  # sha256 hex


def test_create_and_resolve_token(api_db):
    from api import store

    user = store.create_user(label="u1")
    created = store.create_token(user["user_id"], role="user", label="t1")
    assert created["token"]  # 明文返回
    assert "token" not in {k for k in store.list_tokens()[0]}  # 列表不含明文

    resolved = store.resolve_token(created["token"])
    assert resolved is not None
    assert resolved["user_id"] == user["user_id"]
    assert resolved["role"] == "user"


def test_resolve_revoked_token_returns_none(api_db):
    from api import store

    user = store.create_user()
    created = store.create_token(user["user_id"])
    assert store.revoke_token(created["id"]) is True
    assert store.resolve_token(created["token"]) is None
    # 二次吊销失败
    assert store.revoke_token(created["id"]) is False


def test_has_admin_reflects_admin_tokens(api_db):
    from api import store

    assert store.has_admin() is False
    user = store.create_user()
    store.create_token(user["user_id"], role="admin")
    assert store.has_admin() is True


def test_create_token_rejects_bad_role(api_db):
    from api import store

    user = store.create_user()
    with pytest.raises(ValueError):
        store.create_token(user["user_id"], role="superuser")


def test_task_lifecycle_persistence(api_db):
    from api import store

    user = store.create_user()
    store.create_task("task_x", user["user_id"], "topic_generation", "力学", 40)
    t = store.get_task("task_x")
    assert t["status"] == "queued"

    store.set_task_status("task_x", "running")
    store.finish_task("task_x", "done", summary={"arbiter_decision": "PASS"}, error="")
    t = store.get_task("task_x")
    assert t["status"] == "done"
    assert t["summary"]["arbiter_decision"] == "PASS"

    rows = store.list_tasks(user_id=user["user_id"])
    assert len(rows) == 1


def test_mark_interrupted_running(api_db):
    from api import store

    user = store.create_user()
    store.create_task("t1", user["user_id"], "m", "a", 40)
    store.create_task("t2", user["user_id"], "m", "b", 40)
    store.set_task_status("t2", "running")
    n = store.mark_interrupted_running()
    assert n == 2
    assert store.get_task("t1")["status"] == "interrupted"
    assert store.get_task("t2")["status"] == "interrupted"
