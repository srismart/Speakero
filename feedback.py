import os

import httpx

# Debrief feedback: one row per thumbs up/down, with an optional comment.
# Follows the same Supabase-optional, fail-open pattern as limits.py: when
# Supabase is not configured (or the write fails) we never block or error the
# user; feedback collection is best-effort telemetry, not a critical path.


def _supabase_headers():
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def record_feedback(user_id, sid: str, rating: str, comment: str, topic: str) -> bool:
    """Persist one feedback row. Returns True if written, False if skipped or
    the write failed (fail-open). Never raises."""
    url = os.getenv("SUPABASE_URL", "")
    if not url or not os.getenv("SUPABASE_SERVICE_KEY", ""):
        return False
    try:
        httpx.post(
            f"{url}/rest/v1/session_feedback",
            json={
                "user_id": user_id,
                "sid": sid,
                "rating": rating,
                "comment": (comment or "")[:2000] or None,
                "topic": (topic or "")[:500] or None,
            },
            headers={**_supabase_headers(), "Prefer": "return=minimal"},
            timeout=5,
        ).raise_for_status()
        return True
    except Exception as e:
        print(f"[feedback] record failed (fail-open): {e}")
        return False
