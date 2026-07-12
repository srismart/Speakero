# Auth, Guardrails, and LLM Cost Hardening (Spec A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Supabase auth (magic link + Google), config-driven tier limits with hard session caps and Pro time override, anonymous abuse guards, and the LLM ops hardening set (structured outputs, Haiku nudges, shared clients, TTS cache, memoization, usage logging).

**Architecture:** Frontend supabase-js handles sign-in; FastAPI verifies JWTs locally (HS256, `auth.py`) and stamps sessions with user/tier. `limits.py` reads an `app_config` Supabase table (60s cache, env fallbacks, fail-open) and enforces session-length timers and count caps (anonymous in-memory by IP+marker; signed-in via a `session_starts` table). Auth is additive: with no Supabase env vars the app runs exactly as today, anonymous-only.

**Tech Stack:** Python (FastAPI, PyJWT, httpx, pytest), Supabase (Auth + PostgREST), vanilla JS + supabase-js CDN. `anthropic` 0.84.0 (supports `output_config`).

**Spec:** `docs/superpowers/specs/2026-07-10-auth-guardrails-design.md`

**Async test note:** pytest-asyncio is NOT installed. Async scenarios use `asyncio.run(...)` inside sync tests. Endpoint tests use `fastapi.testclient.TestClient` as a context manager when background tasks must keep running.

---

## File Structure

- Create `auth.py` — JWT verification only.
- Create `limits.py` — config fetch/cache, session-minute resolution, count caps.
- Modify `main.py` — /api/config, identity stamping, sid-user binding, start-time counters + length timer, /api/speak gate + cap, debrief memoization, shared Anthropic client, Haiku nudges, usage logging.
- Modify `report.py` — structured outputs, shared client, drop retry loop and LLM-echoed filler_breakdown.
- Modify `tts.py` — LRU response cache.
- Modify `static/index.html` — auth UI, auth headers, marker, countdown, session_limit, Pro length selector, sid on /api/speak.
- Create `tests/test_auth.py`, `tests/test_limits.py`, `tests/test_guardrail_endpoints.py`; rewrite `tests/test_report_retry.py` as `tests/test_report_structured.py`.
- Create `docs/SETUP-supabase.md`; update `README.md` env table, `requirements.txt`, `.env.example`.

---

### Task 0: Dependencies and env scaffolding

**Files:** Modify `requirements.txt`, `.env.example`

- [ ] **Step 1:** Append `PyJWT` on its own line to `requirements.txt`. Run: `pip install PyJWT` — expect successful install (any 2.x).
- [ ] **Step 2:** Replace `.env.example` content with:

```
SMALLEST_API_KEY=your_smallest_ai_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Supabase (optional: without these the app runs anonymous-only)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_public_key
SUPABASE_SERVICE_KEY=your_service_role_key
SUPABASE_JWT_SECRET=your_legacy_jwt_secret
```

- [ ] **Step 3:** Commit: `git add requirements.txt .env.example && git commit -m "chore: add PyJWT and Supabase env scaffolding"`

---

### Task 1: auth.py — JWT verification

**Files:** Create `auth.py`, Create `tests/test_auth.py`

- [ ] **Step 1: Failing tests** — create `tests/test_auth.py`:

```python
import time

import jwt

import auth

SECRET = "test-secret"


def make_token(sub="user-1", email="a@b.c", tier=None, exp_offset=3600, aud="authenticated", secret=SECRET):
    payload = {"sub": sub, "email": email, "aud": aud, "exp": int(time.time()) + exp_offset}
    if tier is not None:
        payload["app_metadata"] = {"tier": tier}
    return jwt.encode(payload, secret, algorithm="HS256")


def test_valid_token_returns_context(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    ctx = auth.verify_token(f"Bearer {make_token(tier='pro')}")
    assert ctx.user_id == "user-1"
    assert ctx.email == "a@b.c"
    assert ctx.tier == "pro"


def test_tier_defaults_to_free(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    assert auth.verify_token(f"Bearer {make_token()}").tier == "free"


def test_unknown_tier_coerced_to_free(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    assert auth.verify_token(f"Bearer {make_token(tier='admin')}").tier == "free"


def test_expired_forged_missing_are_anonymous(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    assert auth.verify_token(f"Bearer {make_token(exp_offset=-10)}") is None
    assert auth.verify_token(f"Bearer {make_token(secret='wrong')}") is None
    assert auth.verify_token(None) is None
    assert auth.verify_token("Bearer not-a-jwt") is None
    assert auth.verify_token("Basic abc") is None


def test_no_secret_configured_is_anonymous(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    assert auth.verify_token(f"Bearer {make_token()}") is None
```

- [ ] **Step 2:** Run `python -m pytest tests/test_auth.py -q` — expect FAIL (no module `auth`).
- [ ] **Step 3: Implement** — create `auth.py`:

```python
import os
from dataclasses import dataclass

import jwt


@dataclass
class AuthContext:
    user_id: str
    email: str
    tier: str  # "free" | "pro"


def verify_token(authorization: str | None) -> AuthContext | None:
    """Verify a Supabase access token (HS256). Returns None for anything
    invalid or unconfigured: callers treat None as anonymous."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret:
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
    except jwt.InvalidTokenError:
        return None
    tier = (payload.get("app_metadata") or {}).get("tier", "free")
    if tier not in ("free", "pro"):
        tier = "free"
    return AuthContext(user_id=payload.get("sub", ""), email=payload.get("email", ""), tier=tier)
```

- [ ] **Step 4:** Run `python -m pytest tests/test_auth.py -q` — expect 5 passed.
- [ ] **Step 5:** Commit: `git add auth.py tests/test_auth.py && git commit -m "feat: Supabase JWT verification (auth.py)"`

---

### Task 2: limits.py — config, cache, session-minute resolution

**Files:** Create `limits.py`, Create `tests/test_limits.py`

- [ ] **Step 1: Failing tests** — create `tests/test_limits.py`:

```python
import limits


def setup_function(_fn):
    limits.reset_cache_for_tests()


def test_defaults_apply_without_supabase(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    cfg = limits.get_config()
    assert cfg["max_session_minutes.anonymous"] == 5
    assert cfg["sessions_per_month.free"] == 8
    assert cfg["sessions_per_day.free"] is None


def test_env_fallback_overrides_default(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("LIMIT_MAX_SESSION_MINUTES_FREE", "20")
    assert limits.get_config()["max_session_minutes.free"] == 20


def test_remote_config_overrides_env(monkeypatch):
    monkeypatch.setenv("LIMIT_ANON_SESSIONS_PER_DAY", "5")
    monkeypatch.setattr(limits, "_fetch_remote_config", lambda: {"anon_sessions_per_day": "3"})
    assert limits.get_config()["anon_sessions_per_day"] == 3


def test_remote_failure_falls_back(monkeypatch):
    def boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(limits, "_fetch_remote_config", boom)
    assert limits.get_config()["max_session_minutes.pro"] == 30


def test_cache_ttl(monkeypatch):
    calls = {"n": 0}
    def counting():
        calls["n"] += 1
        return {}
    monkeypatch.setattr(limits, "_fetch_remote_config", counting)
    limits.get_config()
    limits.get_config()
    assert calls["n"] == 1  # cached


def test_resolve_session_minutes(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert limits.resolve_session_minutes("anonymous", None) == 5
    assert limits.resolve_session_minutes("free", 60) == 15   # non-pro clamped to tier cap
    assert limits.resolve_session_minutes("pro", None) == 30
    assert limits.resolve_session_minutes("pro", 60) == 60
    assert limits.resolve_session_minutes("pro", 90) == 60    # clamped to override max
    assert limits.resolve_session_minutes("pro", "45") == 45  # string ok
    assert limits.resolve_session_minutes("pro", "junk") == 30
```

- [ ] **Step 2:** Run `python -m pytest tests/test_limits.py -q` — expect FAIL (no module `limits`).
- [ ] **Step 3: Implement** — create `limits.py`:

```python
import os
import time
from datetime import datetime, timezone

import httpx

# All keys are live-editable in the Supabase app_config table; env vars named
# LIMIT_<KEY with . -> _, uppercased> act as fallbacks; these are the defaults.
DEFAULTS: dict[str, int | None] = {
    "max_session_minutes.anonymous": 5,
    "max_session_minutes.free": 15,
    "max_session_minutes.pro": 30,
    "pro_override_max_minutes": 60,
    "anon_sessions_per_day": 2,
    "sessions_per_day.free": None,
    "sessions_per_day.pro": None,
    "sessions_per_month.free": 8,
    "sessions_per_month.pro": None,
    "tts_calls_per_session": 30,
}

_CACHE_TTL_SECONDS = 60
_config_cache: dict | None = None
_config_fetched_at = 0.0

_anon_counts: dict[str, list[float]] = {}


def reset_cache_for_tests():
    global _config_cache, _config_fetched_at
    _config_cache = None
    _config_fetched_at = 0.0
    _anon_counts.clear()


def _parse(value) -> int | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("", "null", "none", "unlimited"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _env_key(key: str) -> str:
    return "LIMIT_" + key.upper().replace(".", "_")


def _supabase_headers():
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _fetch_remote_config() -> dict:
    url = os.getenv("SUPABASE_URL", "")
    if not url or not os.getenv("SUPABASE_SERVICE_KEY", ""):
        return {}
    resp = httpx.get(
        f"{url}/rest/v1/app_config",
        params={"select": "key,value"},
        headers=_supabase_headers(),
        timeout=5,
    )
    resp.raise_for_status()
    return {row["key"]: row["value"] for row in resp.json()}


def get_config() -> dict:
    global _config_cache, _config_fetched_at
    now = time.time()
    if _config_cache is not None and now - _config_fetched_at < _CACHE_TTL_SECONDS:
        return _config_cache
    cfg = dict(DEFAULTS)
    for key in DEFAULTS:
        env = os.getenv(_env_key(key))
        if env is not None:
            cfg[key] = _parse(env)
    try:
        for key, value in _fetch_remote_config().items():
            if key in DEFAULTS:
                cfg[key] = _parse(value)
    except Exception as e:
        print(f"[limits] config fetch failed, using fallbacks: {e}")
    _config_cache = cfg
    _config_fetched_at = now
    return cfg


def resolve_session_minutes(tier: str, requested_minutes=None) -> int:
    cfg = get_config()
    cap = cfg.get(f"max_session_minutes.{tier}") or DEFAULTS[f"max_session_minutes.{tier}"]
    if tier == "pro" and requested_minutes is not None:
        try:
            requested = int(requested_minutes)
        except (TypeError, ValueError):
            return cap
        ceiling = cfg.get("pro_override_max_minutes") or DEFAULTS["pro_override_max_minutes"]
        return max(1, min(requested, ceiling))
    return cap
```

- [ ] **Step 4:** Run `python -m pytest tests/test_limits.py -q` — expect 6 passed.
- [ ] **Step 5:** Commit: `git add limits.py tests/test_limits.py && git commit -m "feat: config-driven limits with cache and env fallbacks"`

---

### Task 3: limits.py — count caps (anonymous in-memory, signed-in via session_starts)

**Files:** Modify `limits.py`, Modify `tests/test_limits.py`

- [ ] **Step 1: Failing tests** — append to `tests/test_limits.py`:

```python
from auth import AuthContext


def test_anonymous_cap_trips_on_ip(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert limits.check_and_count(None, "1.2.3.4", "m1") is None
    assert limits.check_and_count(None, "1.2.3.4", "m2") is None
    denied = limits.check_and_count(None, "1.2.3.4", "m3")
    assert denied is not None and "Sign in" in denied["reason"]


def test_anonymous_cap_trips_on_marker(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert limits.check_and_count(None, "1.1.1.1", "mk") is None
    assert limits.check_and_count(None, "2.2.2.2", "mk") is None
    assert limits.check_and_count(None, "3.3.3.3", "mk") is not None


def test_signed_in_monthly_cap(monkeypatch):
    ctx = AuthContext(user_id="u1", email="a@b.c", tier="free")
    monkeypatch.setattr(limits, "_count_starts", lambda uid, window: 8)
    monkeypatch.setattr(limits, "_record_start", lambda uid, tier: None)
    denied = limits.check_and_count(ctx, "9.9.9.9", "mk9")
    assert denied is not None and "Monthly" in denied["reason"]


def test_signed_in_under_cap_records(monkeypatch):
    ctx = AuthContext(user_id="u1", email="a@b.c", tier="free")
    recorded = {}
    monkeypatch.setattr(limits, "_count_starts", lambda uid, window: 0)
    monkeypatch.setattr(limits, "_record_start", lambda uid, tier: recorded.update(uid=uid, tier=tier))
    assert limits.check_and_count(ctx, "9.9.9.9", "mk9") is None
    assert recorded == {"uid": "u1", "tier": "free"}


def test_signed_in_count_failure_fails_open(monkeypatch):
    ctx = AuthContext(user_id="u1", email="a@b.c", tier="free")
    def boom(uid, window):
        raise RuntimeError("down")
    monkeypatch.setattr(limits, "_count_starts", boom)
    monkeypatch.setattr(limits, "_record_start", lambda uid, tier: None)
    assert limits.check_and_count(ctx, "9.9.9.9", "mk9") is None
```

- [ ] **Step 2:** Run `python -m pytest tests/test_limits.py -q` — new tests FAIL (`check_and_count` missing).
- [ ] **Step 3: Implement** — append to `limits.py`:

```python
_DAY_SECONDS = 86400


def _prune_anon(key: str, now: float) -> list[float]:
    kept = [t for t in _anon_counts.get(key, []) if now - t < _DAY_SECONDS]
    _anon_counts[key] = kept
    return kept


def _month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _day_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _count_starts(user_id: str, window: str) -> int:
    """window: 'day' | 'month'. Returns 0 when Supabase is not configured."""
    url = os.getenv("SUPABASE_URL", "")
    if not url or not os.getenv("SUPABASE_SERVICE_KEY", ""):
        return 0
    since = _day_start_iso() if window == "day" else _month_start_iso()
    resp = httpx.get(
        f"{url}/rest/v1/session_starts",
        params={"select": "id", "user_id": f"eq.{user_id}", "started_at": f"gte.{since}"},
        headers={**_supabase_headers(), "Prefer": "count=exact", "Range": "0-0"},
        timeout=5,
    )
    resp.raise_for_status()
    content_range = resp.headers.get("content-range", "*/0")
    return int(content_range.split("/")[-1])


def _record_start(user_id: str, tier: str) -> None:
    url = os.getenv("SUPABASE_URL", "")
    if not url or not os.getenv("SUPABASE_SERVICE_KEY", ""):
        return
    httpx.post(
        f"{url}/rest/v1/session_starts",
        json={"user_id": user_id, "tier": tier},
        headers={**_supabase_headers(), "Prefer": "return=minimal"},
        timeout=5,
    ).raise_for_status()


def check_and_count(auth_ctx, ip: str, marker: str) -> dict | None:
    """Returns None when the session may start, else {'error','reason'}.
    Counting failures fail OPEN: never block users because the config store
    is down."""
    cfg = get_config()

    if auth_ctx is None:
        limit = cfg.get("anon_sessions_per_day")
        now = time.time()
        keys = [f"ip:{ip}" for _ in [1] if ip] + [f"mk:{marker}" for _ in [1] if marker]
        if limit is not None:
            for key in keys:
                if len(_prune_anon(key, now)) >= limit:
                    return {
                        "error": "limit",
                        "reason": "Daily free sessions used. Sign in to save your sessions and keep practicing.",
                    }
        for key in keys:
            _anon_counts.setdefault(key, []).append(now)
        return None

    day_cap = cfg.get(f"sessions_per_day.{auth_ctx.tier}")
    month_cap = cfg.get(f"sessions_per_month.{auth_ctx.tier}")
    try:
        if day_cap is not None and _count_starts(auth_ctx.user_id, "day") >= day_cap:
            return {"error": "limit", "reason": "Daily session limit reached. Try again tomorrow."}
        if month_cap is not None and _count_starts(auth_ctx.user_id, "month") >= month_cap:
            return {"error": "limit", "reason": "Monthly session limit reached. Upgrade for more sessions."}
    except Exception as e:
        print(f"[limits] count check failed, allowing session: {e}")
    try:
        _record_start(auth_ctx.user_id, auth_ctx.tier)
    except Exception as e:
        print(f"[limits] session_starts record failed: {e}")
    return None
```

- [ ] **Step 4:** Run `python -m pytest tests/test_limits.py -q` — expect 11 passed.
- [ ] **Step 5:** Commit: `git add limits.py tests/test_limits.py && git commit -m "feat: anonymous and signed-in session count caps"`

---

### Task 4: main.py — /api/config, identity stamping, sid-user binding

**Files:** Modify `main.py`, Create `tests/test_guardrail_endpoints.py`

- [ ] **Step 1: Failing tests** — create `tests/test_guardrail_endpoints.py`:

```python
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
```

- [ ] **Step 2:** Run `python -m pytest tests/test_guardrail_endpoints.py -q` — expect FAIL (404 on /api/config; 200 where 401 expected).
- [ ] **Step 3: Implement in `main.py`:**

(a) Add imports near the other local imports: `import limits` and `from auth import verify_token`.

(b) Add fields in `SessionState.__init__` after `self.cleanup_task`:

```python
        self.user_id: str | None = None
        self.tier: str = "anonymous"
        self.limit_task: asyncio.Task | None = None
        self.tts_calls: int = 0
        self.report_cache: dict | None = None
        self.usage: dict = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
```

(c) Add after the `index()` route:

```python
@fastapi_app.get("/api/config")
async def api_config():
    return JSONResponse({
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
    })


def _binding_check(request: Request, sess: SessionState):
    """Returns (auth_ctx, error_response|None). A session with an owner may
    only be driven by that owner; anonymous sessions are open (no identity
    to verify) and adopt the caller's identity at /api/start."""
    ctx = verify_token(request.headers.get("authorization"))
    if sess.user_id is not None and (ctx is None or ctx.user_id != sess.user_id):
        return ctx, JSONResponse({"error": "not your session"}, status_code=401)
    return ctx, None
```

(d) In `api_stop`, `api_report`, and `api_speak`, directly after the `if sess is None: ... 404` guard, add:

```python
    _ctx, err = _binding_check(request, sess)
    if err:
        return err
```

(e) Update socket `connect` to accept and stamp auth (python-socketio passes a third `auth` argument):

```python
@sio.event
async def connect(sid, environ, auth=None):
    sess = SessionState()
    sess.room = sid
    token = (auth or {}).get("token") if isinstance(auth, dict) else None
    ctx = verify_token(f"Bearer {token}") if token else None
    if ctx:
        sess.user_id, sess.tier = ctx.user_id, ctx.tier
    SESSIONS[sid] = sess
    await sio.enter_room(sid, sid)
    print(f"[sio] Client connected: {sid} (user={sess.user_id or 'anon'})")
```

- [ ] **Step 4:** Run `python -m pytest -q` — expect all pass (existing tests use anonymous sessions, which stay open).
- [ ] **Step 5:** Commit: `git add main.py tests/test_guardrail_endpoints.py && git commit -m "feat: /api/config, identity stamping, sid-user binding"`

---

### Task 5: main.py — start-time caps, Pro override, session length timer

**Files:** Modify `main.py`, Modify `tests/test_guardrail_endpoints.py`

- [ ] **Step 1: Failing tests** — append to `tests/test_guardrail_endpoints.py`:

```python
import limits


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
```

- [ ] **Step 2:** Run `python -m pytest tests/test_guardrail_endpoints.py -q` — new tests FAIL.
- [ ] **Step 3: Implement** — replace the body of `api_start` in `main.py` with:

```python
@fastapi_app.post("/api/start")
async def api_start(request: Request):
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    sid = data.get("sid", "")
    mode = data.get("mode", "individual")
    topic = data.get("topic", "")
    sess = SESSIONS.get(sid)
    if sess is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    ctx, err = _binding_check(request, sess)
    if err:
        return err
    if ctx:
        # Authoritative identity stamp: signing in mid-connection upgrades the session
        sess.user_id, sess.tier = ctx.user_id, ctx.tier

    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else ""))
    denied = limits.check_and_count(ctx, ip, data.get("marker", ""))
    if denied:
        return JSONResponse(denied, status_code=429)

    minutes = limits.resolve_session_minutes(sess.tier, data.get("requested_minutes"))

    sess.start()
    sess.mode = mode
    sess.topic = topic
    sess.tts_calls = 0
    sess.report_cache = None
    sess.usage = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
    if sess.coaching_task and not sess.coaching_task.done():
        sess.coaching_task.cancel()
    sess.coaching_task = asyncio.create_task(coaching_loop(sess))

    if sess.limit_task and not sess.limit_task.done():
        sess.limit_task.cancel()

    async def _limit_stop():
        await asyncio.sleep(minutes * 60)
        if not sess.active:
            return
        sess.stop()
        if sess.coaching_task and not sess.coaching_task.done():
            sess.coaching_task.cancel()
        _log_session_usage(sess, "limit")
        await sio.emit("event", {"type": "session_limit", "tier": sess.tier}, room=sess.room)

    sess.limit_task = asyncio.create_task(_limit_stop())
    return JSONResponse({"status": "started", "max_minutes": minutes})
```

Add the usage logger helper above `api_start`:

```python
def _log_session_usage(sess: SessionState, ended_by: str):
    print(json.dumps({
        "event": "session_usage",
        "user_id": sess.user_id,
        "tier": sess.tier,
        "seconds": round(sess.elapsed_seconds(), 1),
        "ended_by": ended_by,
        **sess.usage,
    }))
```

In `api_stop`, after `sess.stop()`, add:

```python
    if sess.limit_task and not sess.limit_task.done():
        sess.limit_task.cancel()
    sess.limit_task = None
    _log_session_usage(sess, "user")
```

In the `disconnect` handler's `_cleanup()`, extend the coaching-task cancel to also cancel `gone.limit_task` the same way:

```python
        if gone and gone.limit_task and not gone.limit_task.done():
            gone.limit_task.cancel()
```

- [ ] **Step 4:** Run `python -m pytest -q` — all pass.
- [ ] **Step 5:** Commit: `git add main.py tests/test_guardrail_endpoints.py && git commit -m "feat: session caps, pro override, length timer with session_limit event"`

---

### Task 6: /api/speak gate + cap, debrief memoization

**Files:** Modify `main.py`, Modify `tests/test_guardrail_endpoints.py`

- [ ] **Step 1: Failing tests** — append to `tests/test_guardrail_endpoints.py`:

```python
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
    sess = _mk_session("s-tts")
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
```

- [ ] **Step 2:** Run — new tests FAIL.
- [ ] **Step 3: Implement in `main.py`:**

(a) Replace `api_speak` body:

```python
@fastapi_app.post("/api/speak")
async def api_speak(request: Request):
    data = await request.json()
    text = data.get("text", "")
    sid = data.get("sid", "")
    if not text:
        return Response(status_code=400)
    sess = SESSIONS.get(sid)
    if sess is None:
        return JSONResponse({"error": "no active session"}, status_code=401)
    _ctx, err = _binding_check(request, sess)
    if err:
        return err
    cap = limits.get_config().get("tts_calls_per_session")
    if cap is not None and sess.tts_calls >= cap:
        return JSONResponse({"error": "tts limit reached for this session"}, status_code=429)
    sess.tts_calls += 1
    try:
        audio_bytes = await speak(text)
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Cache-Control": "no-cache"},
        )
    except Exception as e:
        print(f"[tts] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
```

(b) In `api_report`, after the binding check, add memoization:

```python
    if sess.report_cache is not None:
        return JSONResponse(sess.report_cache)
```

and after `report["replay"] = ...` (before `return`), add:

```python
        sess.report_cache = report
```

(The degraded fallback branch is NOT cached, so a re-click retries Claude.)

- [ ] **Step 4:** Run `python -m pytest -q` — all pass (note: existing frontend still sends /api/speak without sid; fixed in Task 9).
- [ ] **Step 5:** Commit: `git add main.py tests/test_guardrail_endpoints.py && git commit -m "feat: gate /api/speak per session with cap; memoize debrief"`

---

### Task 7: LLM ops — structured outputs, shared clients, Haiku nudges, usage capture

**Files:** Modify `report.py`, Modify `main.py`, Delete `tests/test_report_retry.py`, Create `tests/test_report_structured.py`

- [ ] **Step 1: Failing tests** — create `tests/test_report_structured.py` and `git rm tests/test_report_retry.py`:

```python
import asyncio
from types import SimpleNamespace

import report


class FakeClient:
    def __init__(self, text):
        self.kwargs = None
        self.messages = self
        self._text = text

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(text=self._text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


def test_structured_output_request_and_usage_sink(monkeypatch):
    fake = FakeClient('{"summary": "ok", "roughest_window_index": -1}')
    monkeypatch.setattr(report, "_get_client", lambda: fake)
    usage = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
    result = asyncio.run(report.generate_report("hello world", {}, usage_sink=usage))
    fmt = fake.kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert "filler_breakdown" not in fmt["schema"]["properties"]  # injected from stats, not LLM
    assert result["summary"] == "ok"
    assert result["roughest_window_index"] is None  # -1 normalized
    assert usage == {"input_tokens": 100, "output_tokens": 50, "llm_calls": 1}


def test_roughest_index_normalization(monkeypatch):
    fake = FakeClient('{"summary": "ok", "roughest_window_index": 2}')
    monkeypatch.setattr(report, "_get_client", lambda: fake)
    result = asyncio.run(report.generate_report("hi", {}, candidate_windows=[{"index": 0, "text": "a"}]))
    assert result["roughest_window_index"] == 2
```

- [ ] **Step 2:** Run `python -m pytest tests/test_report_structured.py -q` — FAIL (`_get_client` missing).
- [ ] **Step 3: Rework `report.py`:**

(a) Replace the module header and add the schema + client singleton after the imports:

```python
import os
import json
import anthropic
from typing import Dict, Any

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        _client = anthropic.AsyncAnthropic(api_key=api_key, timeout=30.0)
    return _client


def _str_array():
    return {"type": "array", "items": {"type": "string"}}


# filler_breakdown is intentionally absent: the caller injects the detector's
# real counts (deterministic) instead of having the LLM echo them.
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "roughest_window_index": {"type": "integer"},
        "topic_identified": {"type": "string"},
        "strengths": _str_array(),
        "improvements": _str_array(),
        "content_feedback": _str_array(),
        "summary": {"type": "string"},
        "spoken_feedback": {"type": "string"},
        "example_extract": {"type": "string"},
        "repetition_flags": _str_array(),
        "jargon_flags": _str_array(),
        "sentence_completion_rate": {"type": "string"},
        "highlight_moment": {"type": "string"},
    },
    "required": [
        "roughest_window_index", "topic_identified", "strengths", "improvements",
        "content_feedback", "summary", "spoken_feedback", "example_extract",
        "repetition_flags", "jargon_flags", "sentence_completion_rate", "highlight_moment",
    ],
    "additionalProperties": False,
}
```

(b) Change the signature to add the sink: `async def generate_report(transcript, stats, topic="", mode="individual", highlight_window="", candidate_windows=None, usage_sink=None):` and delete the old `api_key` check + `client = anthropic.AsyncAnthropic(...)` lines (the singleton replaces them).

(c) The roughest-window section is now ALWAYS present (schema requires the key). Replace the `if candidate_windows: ... else: roughest_section = "" / roughest_key = ""` block with:

```python
    if candidate_windows:
        window_lines = "\n".join(f'{c["index"]}: {c["text"]}' for c in candidate_windows)
        roughest_section = (
            "\nCANDIDATE WINDOWS (numbered segments of the talk):\n"
            f"{window_lines}\n"
            "For 'roughest_window_index', pick the index of the window with the ROUGHEST "
            "delivery: most rambling, abandoned thoughts, awkward pauses, or filler. "
            "If none stands out, pick the most filler-heavy one."
        )
    else:
        roughest_section = "\n(No candidate windows available; set 'roughest_window_index' to -1.)"
```

(d) In the prompt template, drop the `{roughest_key}` interpolation and the entire `Return ONLY a valid JSON object ... {{...}}` block, replacing it with a short field guide (the schema enforces shape; the guide steers content):

```python
Field guidance:
- spoken_feedback: the single most important coaching point as 1 natural spoken sentence (max 20 words).
- example_extract: rewrite 1-2 sentences from the transcript showing better delivery; natural and speakable.
- repetition_flags / jargon_flags: up to 3 items each; empty arrays if none.
- sentence_completion_rate: like "Good - most sentences were completed" or "Needs work - several abandoned thoughts".
- highlight_moment: comment on the best delivery window, or empty string if none was provided.
- roughest_window_index: integer index from the candidate list, or -1.
Be specific, actionable, and encouraging. Base your analysis strictly on the data provided.
```

(e) Replace the retry loop / fence stripping / json.loads block with:

```python
    client = _get_client()
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        output_config={"format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if usage_sink is not None:
        usage_sink["input_tokens"] += message.usage.input_tokens
        usage_sink["output_tokens"] += message.usage.output_tokens
        usage_sink["llm_calls"] += 1
    report = json.loads(message.content[0].text)
```

Keep the existing roughest_window_index normalization block at the end unchanged.

- [ ] **Step 4: main.py nudge path.** Add a module-level singleton mirroring report.py:

```python
_anthropic_client: anthropic.AsyncAnthropic | None = None


def _get_anthropic() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=10.0)
    return _anthropic_client
```

In `_get_nudge_from_claude`, replace `client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)` with `client = _get_anthropic()`, change `model="claude-sonnet-4-6"` to `model="claude-haiku-4-5"`, and after the create call add:

```python
    sess.usage["input_tokens"] += message.usage.input_tokens
    sess.usage["output_tokens"] += message.usage.output_tokens
    sess.usage["llm_calls"] += 1
```

In `api_report`, pass the sink: add `usage_sink=sess.usage,` to the `generate_report(...)` call.

- [ ] **Step 5:** Run `python -m pytest -q` — all pass.
- [ ] **Step 6:** Commit: `git add -A && git commit -m "feat: structured debrief output, Haiku nudges, shared clients, usage capture"`

---

### Task 8: tts.py LRU cache

**Files:** Modify `tts.py`, Create test appended to `tests/test_guardrail_endpoints.py`

- [ ] **Step 1: Failing test** — append to `tests/test_guardrail_endpoints.py`:

```python
import tts


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
    import asyncio
    assert asyncio.run(tts.speak("hello")) == b"WAVDATA"
    assert asyncio.run(tts.speak("hello")) == b"WAVDATA"
    assert calls["n"] == 1
```

- [ ] **Step 2:** Run — FAIL (`tts._cache` missing).
- [ ] **Step 3: Implement** — in `tts.py`, add after the constants:

```python
import hashlib
from collections import OrderedDict

_cache: OrderedDict[str, bytes] = OrderedDict()
_CACHE_MAX_ENTRIES = 128
```

At the top of `speak()` (after the api_key check):

```python
    cache_key = hashlib.sha256(f"{TTS_MODEL}|{TTS_VOICE}|{text}".encode()).hexdigest()
    if cache_key in _cache:
        _cache.move_to_end(cache_key)
        return _cache[cache_key]
```

Before `return res.content` at the end:

```python
    _cache[cache_key] = res.content
    if len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)
```

- [ ] **Step 4:** Run `python -m pytest -q` — all pass.
- [ ] **Step 5:** Commit: `git add tts.py tests/test_guardrail_endpoints.py && git commit -m "feat: LRU cache for TTS responses"`

---

### Task 9: Frontend — auth UI, headers, marker, countdown, session_limit, Pro selector

**Files:** Modify `static/index.html`. Verified by `tests/test_frontend_wiring.py` (extends automatically) + manual.

All anchors below exist today; locate each with Grep before editing.

- [ ] **Step 1: Auth state + init.** Directly after the `let sessionPcm = [];` / `let sessionPcmRate = 0;` globals, add:

```javascript
    // ── Auth (Supabase) ────────────────────────────────────────────────────────
    let supabaseClient = null;
    let authToken = null;
    let userTier = 'anonymous';
    const browserMarker = (() => {
      let m = localStorage.getItem('speakero_marker');
      if (!m) { m = crypto.randomUUID(); localStorage.setItem('speakero_marker', m); }
      return m;
    })();

    function authHeaders() {
      return authToken ? { 'Authorization': 'Bearer ' + authToken } : {};
    }

    function applyAuthSession(session) {
      authToken = session ? session.access_token : null;
      userTier = session ? ((session.user.app_metadata || {}).tier || 'free') : 'anonymous';
      const signin = document.getElementById('btnSignIn');
      const chip = document.getElementById('userChip');
      if (session) {
        signin.style.display = 'none';
        chip.style.display = 'flex';
        document.getElementById('userEmail').textContent = session.user.email || '';
      } else {
        signin.style.display = '';
        chip.style.display = 'none';
      }
      document.getElementById('proLengthWrap').style.display = userTier === 'pro' ? '' : 'none';
    }

    async function initAuth() {
      try {
        const cfg = await (await fetch('/api/config')).json();
        if (!cfg.supabase_url || !cfg.supabase_anon_key) return; // anonymous-only mode
        await new Promise((resolve, reject) => {
          const s = document.createElement('script');
          s.src = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2';
          s.onload = resolve;
          s.onerror = reject;
          document.head.appendChild(s);
        });
        supabaseClient = window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key);
        supabaseClient.auth.onAuthStateChange((_event, session) => applyAuthSession(session));
        const { data } = await supabaseClient.auth.getSession();
        applyAuthSession(data.session);
        document.getElementById('authArea').style.display = 'flex';
      } catch (e) { console.warn('[auth] init failed:', e); }
    }
    initAuth();
```

- [ ] **Step 2: Header markup.** Grep for the header's right-side container (the element holding the settings gear / avatar; grep `settings` or `header-right` or the ⚙ char). Insert as its first child:

```html
        <div id="authArea" style="display:none; align-items:center; gap:10px;">
          <button id="btnSignIn" class="btn-hear">Sign in</button>
          <div id="userChip" style="display:none; align-items:center; gap:8px;">
            <span id="userEmail" style="font-size:0.8rem; color:var(--slate-400); font-family:'Inter',sans-serif;"></span>
            <button id="btnSignOut" class="btn-replay">Sign out</button>
          </div>
        </div>
```

- [ ] **Step 3: Auth modal.** Insert before the `<!-- Toast notifications -->` div:

```html
  <!-- Auth modal -->
  <div id="authModal" style="display:none;">
    <div class="auth-card">
      <button id="btnCloseAuth">✕</button>
      <h3>Sign in to Speakero</h3>
      <p id="authPitch">Save your sessions, track progress over time, and unlock more practice.</p>
      <input id="authEmail" type="email" placeholder="you@example.com" />
      <button id="btnMagicLink" class="btn-hear">Email me a magic link</button>
      <div class="auth-divider">or</div>
      <button id="btnGoogle" class="btn-hear">Continue with Google</button>
      <p id="authStatus"></p>
    </div>
  </div>
```

CSS, inserted before the `/* ── TOAST` block:

```css
    /* ── AUTH MODAL ──────────────────────────────────────────────────────────── */
    #authModal {
      position: fixed; inset: 0; z-index: 300;
      background: rgba(2,6,23,0.75);
      display: none; align-items: center; justify-content: center;
    }
    #authModal.open { display: flex; }
    .auth-card {
      background: var(--surface-solid);
      border: 1px solid var(--border-solid);
      border-radius: 16px;
      padding: 28px;
      width: min(92vw, 380px);
      position: relative;
      font-family: 'Inter', sans-serif;
    }
    .auth-card h3 { color: var(--text); margin-bottom: 8px; }
    .auth-card p { color: var(--slate-400); font-size: 0.85rem; margin-bottom: 14px; line-height: 1.5; }
    .auth-card input {
      width: 100%; padding: 10px 12px; margin-bottom: 10px;
      background: var(--bg); color: var(--text);
      border: 1px solid var(--border-solid); border-radius: 8px;
    }
    .auth-card .btn-hear { width: 100%; margin-bottom: 4px; }
    .auth-divider { text-align: center; color: var(--muted); font-size: 0.75rem; margin: 10px 0; }
    #btnCloseAuth {
      position: absolute; top: 10px; right: 12px;
      background: none; border: none; color: var(--muted); cursor: pointer; font-size: 1rem;
    }
    #authStatus { min-height: 1em; color: var(--emerald); }
```

JS handlers, after the `initAuth();` line:

```javascript
    function openAuthModal(pitch) {
      if (!supabaseClient) { showToast('Sign-in is not configured on this server.'); return; }
      if (pitch) document.getElementById('authPitch').textContent = pitch;
      document.getElementById('authModal').classList.add('open');
    }
    document.getElementById('btnSignIn').addEventListener('click', () => openAuthModal());
    document.getElementById('btnCloseAuth').addEventListener('click', () =>
      document.getElementById('authModal').classList.remove('open'));
    document.getElementById('btnMagicLink').addEventListener('click', async () => {
      const email = document.getElementById('authEmail').value.trim();
      if (!email) return;
      const { error } = await supabaseClient.auth.signInWithOtp({ email });
      document.getElementById('authStatus').textContent =
        error ? 'Could not send link: ' + error.message : 'Check your email for the sign-in link.';
    });
    document.getElementById('btnGoogle').addEventListener('click', async () => {
      await supabaseClient.auth.signInWithOAuth({ provider: 'google' });
    });
    document.getElementById('btnSignOut').addEventListener('click', async () => {
      await supabaseClient.auth.signOut();
    });
```

- [ ] **Step 4: Pro length selector.** Grep for `topicInput`; immediately after its wrapping element add:

```html
        <span id="proLengthWrap" style="display:none; margin-left:8px;">
          <select id="proMinutes" title="Session length (Pro)">
            <option value="30" selected>30 min</option>
            <option value="45">45 min</option>
            <option value="60">60 min</option>
          </select>
        </span>
```

- [ ] **Step 5: Wire API calls.** In the `btnStart` handler's `/api/start` fetch: add `...authHeaders()` into a `headers` object alongside Content-Type, and extend the body with `marker: browserMarker, requested_minutes: userTier === 'pro' ? parseInt(document.getElementById('proMinutes').value, 10) : null`. Capture the response: `const startRes = await fetch(...); const startBody = await startRes.json(); if (!startRes.ok) { showToast(startBody.reason || 'Could not start session.'); if (!authToken) openAuthModal(startBody.reason); return; } sessionMaxSeconds = (startBody.max_minutes || 0) * 60;`
  Do the same header spread for the `/api/stop` and `/api/report` fetches. In `playNudgeAudio`, add `sid: socket.id` to the `/api/speak` body and spread `authHeaders()`.
- [ ] **Step 6: Countdown timer.** Add global `let sessionMaxSeconds = 0;` next to `timerSeconds`. In `startTimer()`, change the display line to show remaining time when a limit exists:

```javascript
        const shown = sessionMaxSeconds > 0 ? Math.max(0, sessionMaxSeconds - timerSeconds) : timerSeconds;
        const m = String(Math.floor(shown / 60)).padStart(2, '0');
        const s = String(shown % 60).padStart(2, '0');
        document.getElementById('timer').textContent = `${m}:${s}`;
```

- [ ] **Step 7: session_limit event.** In the `socket.on('event', ...)` switch, add a case:

```javascript
        case 'session_limit': {
          sessionActive = false;
          stopAudioCapture();
          stopTimer();
          setVisualizerActive(false);
          document.getElementById('btnStart').disabled = false;
          document.getElementById('btnStart').classList.add('pulsing');
          document.getElementById('btnStop').disabled = true;
          setStatus('Session limit reached');
          if (!authToken) {
            openAuthModal('Time is up for this free session. Sign in to save your sessions and keep practicing.');
          } else {
            showToast('Session limit reached. Your debrief is ready to generate.');
          }
          break;
        }
```

- [ ] **Step 8: socket auth.** Change the socket init line to:

```javascript
    const socket = io({ transports: ['websocket', 'polling'], auth: (cb) => cb({ token: authToken }) });
```

- [ ] **Step 9: Verify.** Run `python -m pytest tests/test_frontend_wiring.py -q` (JS parses; all referenced ids exist — the wiring test catches typos across all these edits). Then start `uvicorn main:app --port 8080`, `Invoke-WebRequest http://localhost:8080/` expect 200, stop server.
- [ ] **Step 10:** Commit: `git add static/index.html && git commit -m "feat: auth UI, tier-aware start flow, countdown, session_limit handling"`

---

### Task 10: Docs + final verification

**Files:** Create `docs/SETUP-supabase.md`; Modify `README.md`

- [ ] **Step 1:** Create `docs/SETUP-supabase.md`:

```markdown
# Supabase setup (Spec A)

Without these steps the app runs anonymous-only (sign-in hidden). ~15 minutes.

1. Create a project at supabase.com. Copy from Settings -> API:
   - Project URL -> `SUPABASE_URL`
   - anon public key -> `SUPABASE_ANON_KEY`
   - service_role key -> `SUPABASE_SERVICE_KEY` (server only, never in frontend)
   - Settings -> API -> JWT Settings -> JWT Secret -> `SUPABASE_JWT_SECRET`
2. Auth -> Providers: enable Email (magic link on). Enable Google (paste OAuth
   client id/secret from Google Cloud Console; authorized redirect =
   the Supabase callback URL shown on that page).
3. Auth -> URL Configuration: set Site URL to your app URL
   (http://localhost:8080 for dev) so magic links redirect back.
4. SQL editor, run:

   create table app_config (key text primary key, value text);
   insert into app_config (key, value) values
     ('max_session_minutes.anonymous', '5'),
     ('max_session_minutes.free', '15'),
     ('max_session_minutes.pro', '30'),
     ('pro_override_max_minutes', '60'),
     ('anon_sessions_per_day', '2'),
     ('sessions_per_day.free', 'null'),
     ('sessions_per_day.pro', 'null'),
     ('sessions_per_month.free', '8'),
     ('sessions_per_month.pro', 'null'),
     ('tts_calls_per_session', '30');

   create table session_starts (
     id uuid primary key default gen_random_uuid(),
     user_id uuid not null,
     tier text not null,
     started_at timestamptz not null default now()
   );
   create index on session_starts (user_id, started_at);

   alter table app_config enable row level security;
   alter table session_starts enable row level security;
   -- no policies: service_role bypasses RLS; anon/authenticated get nothing.

5. Ops: edit app_config rows in the Table Editor to change limits live
   (60s server cache). Set a value to the text 'null' for unlimited, '0' to
   shut a tier off. Manage users under Authentication -> Users.
   To make a user Pro (until Stripe lands): Authentication -> Users -> select
   user -> edit App Metadata -> {"tier": "pro"}.
```

- [ ] **Step 2:** README env table: add rows for the four SUPABASE_ vars (mark optional, "app runs anonymous-only without them") and a pointer to `docs/SETUP-supabase.md`.
- [ ] **Step 3:** Full verification: `python -m pytest -q` (expect ~40 passed), then smoke-serve (uvicorn + Invoke-WebRequest 200 + stop).
- [ ] **Step 4:** Commit: `git add -A && git commit -m "docs: Supabase setup guide and env reference"`

---

## Self-Review (done during planning)

**Spec coverage:** /api/config + supabase-js + modal (T9), JWT verify (T1), sid binding + identity stamp (T4), anonymous day caps by IP+marker (T3, T5), signed-in daily/monthly caps via session_starts + fail-open (T3), length timer + Pro override + session_limit + countdown (T5, T9), /api/speak gate + cap (T6, T9), memoization (T6), structured outputs + deterministic filler_breakdown + field guide (T7), Haiku + shared clients + timeouts (T7), usage logging (T5, T7), TTS LRU (T8), app_config/session_starts DDL + Studio ops guide (T10). Static-first prompt ordering: satisfied by keeping instructions ahead of transcript (no code change needed; convention noted in spec).
**Type consistency:** `AuthContext(user_id, email, tier)` used by T1/T3/T4; `check_and_count(auth_ctx, ip, marker)` T3/T5; `resolve_session_minutes(tier, requested)` T2/T5; `_binding_check(request, sess) -> (ctx, err)` T4/T5/T6; `usage_sink` dict keys match `sess.usage` T5/T7; `_get_client` (report) vs `_get_anthropic` (main) deliberately distinct.
**Placeholder scan:** clean; every code step has complete code.
**Note:** existing `test_report_replay.py` tests monkeypatch `main.generate_report`, unaffected by T7's signature extension (`usage_sink` kwarg is optional). `filler_breakdown` injection: T7 removes it from the LLM schema; `main.api_report` already receives `stats` — add `report["filler_breakdown"] = stats.get("fillerBreakdown", {})` right before `report["replay"] = ...` (include this in T7 Step 4).
