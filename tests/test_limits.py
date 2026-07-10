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
