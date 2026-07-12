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


def test_start_denied_when_cap_tripped(monkeypatch):
    monkeypatch.setattr(main.limits, "check_and_count",
                        lambda ctx, ip, marker: {"error": "limit", "reason": "Daily free sessions used."})
    _mk_session("s-capped")
    client = TestClient(main.fastapi_app)
    res = client.post("/api/start", json={"sid": "s-capped"})
    assert res.status_code == 429
    assert "Daily" in res.json()["reason"]
    main.SESSIONS.pop("s-capped", None)


def test_start_returns_resolved_minutes_and_stamps_identity(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setattr(main.limits, "check_and_count", lambda ctx, ip, marker: None)
    monkeypatch.setattr(main.limits, "resolve_session_minutes", lambda tier, req: 42)
    sess = _mk_session("s-start")
    with TestClient(main.fastapi_app) as client:
        res = client.post("/api/start", json={"sid": "s-start"},
                          headers={"Authorization": f"Bearer {make_token(sub='u9', tier='pro')}"})
        assert res.status_code == 200
        assert res.json()["max_minutes"] == 42
        assert sess.user_id == "u9" and sess.tier == "pro"
        client.post("/api/stop", json={"sid": "s-start"},
                    headers={"Authorization": f"Bearer {make_token(sub='u9')}"})
    main.SESSIONS.pop("s-start", None)


def test_session_limit_timer_stops_session(monkeypatch):
    monkeypatch.setattr(main.limits, "check_and_count", lambda ctx, ip, marker: None)
    monkeypatch.setattr(main.limits, "resolve_session_minutes", lambda tier, req: 0.001)  # ~60ms
    emitted = []

    async def fake_emit(event, data, room=None):
        emitted.append((data.get("type"), room))

    monkeypatch.setattr(main.sio, "emit", fake_emit)
    sess = _mk_session("s-timer")
    with TestClient(main.fastapi_app) as client:
        client.post("/api/start", json={"sid": "s-timer"})
        assert sess.active is True
        time.sleep(0.4)
        assert sess.active is False
    assert ("session_limit", "s-timer") in emitted
    main.SESSIONS.pop("s-timer", None)


import limits  # noqa: E402


def test_speak_requires_session_and_caps(monkeypatch):
    async def fake_speak(text):
        return b"RIFFfake"

    monkeypatch.setattr(main, "speak", fake_speak)
    monkeypatch.setattr(main.limits, "get_config",
                        lambda: {**limits.DEFAULTS, "tts_calls_per_session": 2})
    client = TestClient(main.fastapi_app)
    # no sid -> 401
    assert client.post("/api/speak", json={"text": "hi"}).status_code == 401
    # unknown sid -> 401
    assert client.post("/api/speak", json={"text": "hi", "sid": "nope"}).status_code == 401
    _mk_session("s-tts")
    assert client.post("/api/speak", json={"text": "hi", "sid": "s-tts"}).status_code == 200
    assert client.post("/api/speak", json={"text": "hi", "sid": "s-tts"}).status_code == 200
    assert client.post("/api/speak", json={"text": "hi", "sid": "s-tts"}).status_code == 429
    main.SESSIONS.pop("s-tts", None)


def test_report_memoized(monkeypatch):
    calls = {"n": 0}

    async def fake_report(*args, **kwargs):
        calls["n"] += 1
        return {"summary": "ok"}

    monkeypatch.setattr(main, "generate_report", fake_report)
    sess = _mk_session("s-memo")
    sess.start()
    client = TestClient(main.fastapi_app)
    first = client.post("/api/report", json={"sid": "s-memo"}).json()
    second = client.post("/api/report", json={"sid": "s-memo"}).json()
    assert calls["n"] == 1
    assert first == second
    main.SESSIONS.pop("s-memo", None)


import asyncio  # noqa: E402

import tts  # noqa: E402


def test_tts_cache_hits(monkeypatch):
    tts._cache.clear()
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        content = b"WAVDATA"

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            calls["n"] += 1
            return FakeResp()

    monkeypatch.setattr(tts.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("SMALLEST_API_KEY", "k")
    assert asyncio.run(tts.speak("hello")) == b"WAVDATA"
    assert asyncio.run(tts.speak("hello")) == b"WAVDATA"
    assert calls["n"] == 1
