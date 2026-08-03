import os

import httpx

# Session persistence: one row per completed debrief for signed-in users.
# This is the write-path only (the history UI and progress chart read it in a
# later slice). Same Supabase-optional, fail-open pattern as limits.py and
# feedback.py: anonymous sessions and unconfigured/failed writes are no-ops,
# never blocking the debrief.


def _supabase_headers():
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def build_summary(topic, mode, stats, total_words, duration_seconds,
                  delivery, content_score, verdict) -> dict:
    """Shape a persisted session row from the values available at debrief time.
    Kept pure and separate from the network call so it is easy to test."""
    delivery_score = (delivery or {}).get("score") if isinstance(delivery, dict) else None
    return {
        "topic": (topic or "")[:500] or None,
        "mode": mode or "individual",
        "filler_count": int(stats.get("fillerCount", 0) or 0),
        "pause_count": int(stats.get("pauseCount", 0) or 0),
        "wpm": int(stats.get("wpm", 0) or 0),
        "total_words": int(total_words or 0),
        "duration_seconds": int(round(duration_seconds or 0)),
        "delivery_score": delivery_score,
        "content_score": content_score,
        "verdict": (verdict or "")[:500] or None,
    }


def record_session(user_id, summary: dict) -> bool:
    """Persist one completed session. Returns True if written, False if skipped
    (anonymous or unconfigured) or the write failed (fail-open). Never raises."""
    if not user_id:
        return False
    url = os.getenv("SUPABASE_URL", "")
    if not url or not os.getenv("SUPABASE_SERVICE_KEY", ""):
        return False
    try:
        httpx.post(
            f"{url}/rest/v1/sessions",
            json={"user_id": user_id, **summary},
            headers={**_supabase_headers(), "Prefer": "return=minimal"},
            timeout=5,
        ).raise_for_status()
        return True
    except Exception as e:
        print(f"[persistence] record failed (fail-open): {e}")
        return False
