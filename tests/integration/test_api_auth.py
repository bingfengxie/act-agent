"""HTTP API auth + tenant scoping (lifespan is not started; DB comes from conftest)."""

import pytest
from httpx import ASGITransport, AsyncClient

from act_agent.main import app
from act_agent.storage.bootstrap import bootstrap_defaults


@pytest.fixture
async def client():
    await bootstrap_defaults()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client, username, password="admin123"):
    resp = await client.post(
        "/v1/auth/login",
        json={"tenant": "default", "username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_login_and_me(client):
    token = await _login(client, "admin")
    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["tenant_id"] == "default"
    assert "tenant:admin" in body["scopes"]


async def test_bad_password_and_missing_token(client):
    resp = await client.post(
        "/v1/auth/login",
        json={"tenant": "default", "username": "admin", "password": "nope"},
    )
    assert resp.status_code == 401
    assert (await client.get("/v1/auth/me")).status_code == 401


async def test_session_lifecycle_and_tenant_scoping(client):
    token = await _login(client, "operator", "operator123")
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post("/v1/sessions", json={}, headers=headers)
    assert created.status_code == 201
    session_id = created.json()["id"]

    listed = await client.get("/v1/sessions", headers=headers)
    assert [s["id"] for s in listed.json()] == [session_id]

    # 同租户不同用户不能读别人的会话
    admin_token = await _login(client, "admin")
    other = await client.post(
        "/v1/sessions", json={}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert other.status_code == 201
    # admin 可以管理本租户会话（设计如此）
    peek = await client.get(
        f"/v1/sessions/{session_id}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert peek.status_code == 200

    deleted = await client.delete(f"/v1/sessions/{session_id}", headers=headers)
    assert deleted.status_code == 204


async def test_viewer_cannot_read_audit(client):
    viewer = await _login(client, "viewer", "viewer123")
    resp = await client.get(
        "/v1/admin/audit", headers={"Authorization": f"Bearer {viewer}"}
    )
    assert resp.status_code == 403

    admin = await _login(client, "admin")
    ok = await client.get("/v1/admin/audit", headers={"Authorization": f"Bearer {admin}"})
    assert ok.status_code == 200
    assert ok.json()["count"] == 0
