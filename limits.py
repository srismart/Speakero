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
