"""LLM 设置仓储层与管理路由测试。

覆盖：种子默认值、服务商 / 模型配置 / Agent 绑定 / 应用设置的 CRUD、解析、
脱敏、引用完整性约束，以及管理端 REST 端点与鉴权。
"""
import pytest


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ============ 仓储层（直接调用 settings_store） ============

def test_seed_defaults_populates_providers_and_bindings(api_db):
    from api import settings_store

    # init_db 已触发 seed_defaults：应有 1 个默认服务商、4 个模型配置、8 个绑定。
    assert settings_store.count_providers() == 1
    assert settings_store.count_model_configs() == 4
    bindings = settings_store.list_bindings()
    assert {b["role"] for b in bindings} == set(settings_store.AGENT_ROLES)
    assert all(b["model_config_id"] is not None for b in bindings)


def test_seed_defaults_is_idempotent(api_db):
    from api import settings_store

    settings_store.seed_defaults()
    settings_store.seed_defaults()
    assert settings_store.count_providers() == 1
    assert settings_store.count_model_configs() == 4


def test_provider_public_masks_api_key(api_db):
    from api import settings_store

    row = settings_store.create_provider(
        name="p2", kind="openai_compatible", api_key="sk-secret-1234", base_url="http://x",
    )
    pub = settings_store.provider_public(row)
    assert "api_key" not in pub
    assert pub["api_key_set"] is True
    assert pub["api_key_masked"] == "****1234"


def test_delete_provider_blocked_when_referenced(api_db):
    from api import settings_store

    prov = settings_store.create_provider(name="p3", kind="openrouter", api_key="k")
    settings_store.create_model_config(name="m-ref", provider_id=prov["id"], model="x")
    with pytest.raises(ValueError):
        settings_store.delete_provider(prov["id"])


def test_resolve_role_returns_flat_config(api_db):
    from api import settings_store

    data = settings_store.resolve("planner")
    assert data["role"] == "planner"
    assert "provider_kind" in data and "model" in data and "temperature" in data


def test_resolve_unknown_role_raises(api_db):
    from api import settings_store

    with pytest.raises(ValueError):
        settings_store.resolve("nope")


def test_app_settings_typed_roundtrip(api_db):
    from api import settings_store

    settings_store.set_app_settings({"max_retry_count": 7, "auto_compile_latex": True})
    out = settings_store.get_app_settings()
    assert out["max_retry_count"] == 7
    assert out["auto_compile_latex"] is True


def test_set_unknown_app_setting_raises(api_db):
    from api import settings_store

    with pytest.raises(ValueError):
        settings_store.set_app_settings({"bogus": 1})


# ============ runtime facade ============

def test_runtime_resolve_and_invalidate(api_db):
    from api import settings_store
    from config import runtime

    runtime.invalidate_cache()
    assert runtime.max_retry_count() >= 0
    settings_store.set_app_settings({"max_retry_count": 9})
    runtime.invalidate_cache()
    assert runtime.max_retry_count() == 9


# ============ 管理路由 ============

def test_llm_settings_require_admin(client):
    user_admin = client.admin_token
    user = client.post("/api/admin/users", headers=_auth(user_admin), json={"label": "u"}).json()
    tok = client.post(
        "/api/admin/tokens", headers=_auth(user_admin),
        json={"user_id": user["user_id"], "role": "user"},
    ).json()["token"]
    assert client.get("/api/admin/llm/providers", headers=_auth(tok)).status_code == 403


def test_provider_crud_endpoints(client):
    admin = client.admin_token
    # create
    resp = client.post(
        "/api/admin/llm/providers", headers=_auth(admin),
        json={"name": "extra", "kind": "openai_compatible",
              "api_key": "sk-abcd1234", "base_url": "http://h"},
    )
    assert resp.status_code == 201
    pid = resp.json()["id"]
    assert resp.json()["api_key_masked"] == "****1234"
    assert "api_key" not in resp.json()
    # duplicate name
    assert client.post(
        "/api/admin/llm/providers", headers=_auth(admin),
        json={"name": "extra", "kind": "openrouter"},
    ).status_code == 409
    # patch
    patched = client.patch(
        f"/api/admin/llm/providers/{pid}", headers=_auth(admin), json={"base_url": "http://new"},
    )
    assert patched.status_code == 200 and patched.json()["base_url"] == "http://new"
    # delete
    assert client.delete(f"/api/admin/llm/providers/{pid}", headers=_auth(admin)).status_code == 200
    assert client.get(f"/api/admin/llm/providers/{pid}", headers=_auth(admin)).status_code == 404


def test_model_and_binding_endpoints(client):
    admin = client.admin_token
    prov = client.get("/api/admin/llm/providers", headers=_auth(admin)).json()["items"][0]
    # create model config
    resp = client.post(
        "/api/admin/llm/models", headers=_auth(admin),
        json={"name": "custom", "provider_id": prov["id"], "model": "foo/bar",
              "temperature": 0.5, "max_tokens": 2048, "streaming": True},
    )
    assert resp.status_code == 201
    mid = resp.json()["id"]
    assert resp.json()["streaming"] is True
    # bind planner to it
    bound = client.put(
        "/api/admin/llm/agents/planner", headers=_auth(admin),
        json={"model_config_id": mid},
    )
    assert bound.status_code == 200 and bound.json()["model_config_id"] == mid
    # unknown role
    assert client.put(
        "/api/admin/llm/agents/nope", headers=_auth(admin),
        json={"model_config_id": mid},
    ).status_code == 400
    # deleting referenced model config is blocked
    assert client.delete(f"/api/admin/llm/models/{mid}", headers=_auth(admin)).status_code == 409


def test_app_settings_endpoints(client):
    admin = client.admin_token
    got = client.get("/api/admin/llm/settings", headers=_auth(admin))
    assert got.status_code == 200
    patched = client.patch(
        "/api/admin/llm/settings", headers=_auth(admin),
        json={"max_retry_count": 5, "auto_compile_latex": True},
    )
    assert patched.status_code == 200
    assert patched.json()["max_retry_count"] == 5
    assert patched.json()["auto_compile_latex"] is True


def test_provider_kinds_endpoint(client):
    from api import settings_store

    admin = client.admin_token
    resp = client.get("/api/admin/llm/provider-kinds", headers=_auth(admin))
    assert resp.status_code == 200
    kinds = resp.json()["kinds"]
    # 与注册中心一致：至少含默认两种，且为有序去重列表。
    assert "openrouter" in kinds and "openai_compatible" in kinds
    assert kinds == sorted(kinds)


def test_provider_kinds_requires_admin(client):
    user = client.post("/api/admin/users", headers=_auth(client.admin_token),
                       json={"label": "u"}).json()
    tok = client.post(
        "/api/admin/tokens", headers=_auth(client.admin_token),
        json={"user_id": user["user_id"], "role": "user"},
    ).json()["token"]
    assert client.get("/api/admin/llm/provider-kinds", headers=_auth(tok)).status_code == 403


def test_llm_options_endpoint(client):
    from api import settings_store

    admin = client.admin_token
    resp = client.get("/api/admin/llm/options", headers=_auth(admin))
    assert resp.status_code == 200
    body = resp.json()
    assert "openrouter" in body["provider_kinds"]
    assert body["agent_roles"] == list(settings_store.AGENT_ROLES)
    keys = {s["key"] for s in body["app_settings"]}
    assert keys == set(settings_store.APP_SETTING_TYPES)
    # 约束元信息正确暴露。
    by_key = {s["key"]: s for s in body["app_settings"]}
    assert by_key["max_retry_count"]["type"] == "int"
    assert by_key["max_retry_count"]["min"] == 0
    assert by_key["sse_poll_interval"]["exclusiveMin"] == 0
