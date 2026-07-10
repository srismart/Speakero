import time

import jwt
from fastapi.testclient import TestClient

import main

SECRET = "test-secret"


def make_token(sub="user-1", tier="free"):
    payload = {
        "sub": sub, "email": "a@b.c", "aud": "authenticated",
        "exp": int(time.time()) + 3600, "app_metadata": {"tier": tier},
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _mk_session(sid, user_id=None, tier="anonymous"):
    sess = main.SessionState()
    sess.room = sid
    sess.user_id = user_id
    sess.tier = tier
    main.SESSIONS[sid] = sess
    return sess


def test_api_config_returns_public_keys(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    client = TestClient(main.fastapi_app)
    body = client.get("/api/config").json()
    assert body == {"supabase_url": "https://x.supabase.co", "supabase_anon_key": "anon-key"}


def test_stop_binding_rejects_other_users_session(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    _mk_session("s-owned", user_id="owner", tier="free")
    client = TestClient(main.fastapi_app)
    res = client.post("/api/stop", json={"sid": "s-owned"},
                      headers={"Authorization": f"Bearer {make_token(sub='intruder')}"})
    assert res.status_code == 401
    res = client.post("/api/stop", json={"sid": "s-owned"})  # anonymous caller
    assert res.status_code == 401
    main.SESSIONS.pop("s-owned", None)


def test_stop_binding_allows_owner_and_anon_sessions(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    _mk_session("s-owned2", user_id="owner", tier="free")
    _mk_session("s-anon")
    client = TestClient(main.fastapi_app)
    assert client.post("/api/stop", json={"sid": "s-owned2"},
                       headers={"Authorization": f"Bearer {make_token(sub='owner')}"}).status_code == 200
    assert client.post("/api/stop", json={"sid": "s-anon"}).status_code == 200
    main.SESSIONS.pop("s-owned2", None)
    main.SESSIONS.pop("s-anon", None)
