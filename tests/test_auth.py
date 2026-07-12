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
