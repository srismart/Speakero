import main
import persistence
from fastapi.testclient import TestClient


def test_report_persists_once_not_on_cache(monkeypatch):
    calls = {"report": 0, "persist": 0}

    async def fake_report(*a, **k):
        calls["report"] += 1
        return {"summary": "ok", "delivery": {"score": 80}, "content_score": 70, "verdict": "Good."}

    def fake_record(user_id, summary):
        calls["persist"] += 1
        return True

    monkeypatch.setattr(main, "generate_report", fake_report)
    monkeypatch.setattr(main.persistence, "record_session", fake_record)

    # Anonymous session (user_id=None) keeps the endpoint binding open so the
    # test needs no JWT; record_session is still invoked once and the fake
    # counts it. record_session's own anon no-op is covered by a unit test above.
    sess = main.SessionState()
    sess.room = "s-persist"
    sess.start()
    main.SESSIONS["s-persist"] = sess

    client = TestClient(main.fastapi_app)
    client.post("/api/report", json={"sid": "s-persist"})
    client.post("/api/report", json={"sid": "s-persist"})  # cached, must not re-persist
    assert calls["report"] == 1
    assert calls["persist"] == 1
    main.SESSIONS.pop("s-persist", None)


def test_record_session_noop_without_supabase(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert persistence.record_session("u1", {"topic": "x"}) is False


def test_record_session_noop_for_anonymous(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    # No user_id: anonymous sessions are not persisted (matches "sign in to save").
    assert persistence.record_session(None, {"topic": "x"}) is False


def test_record_session_posts_when_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr(persistence.httpx, "post", fake_post)
    summary = {"topic": "climate", "mode": "pitch", "delivery_score": 82, "wpm": 130}
    assert persistence.record_session("u9", summary) is True
    assert captured["url"].endswith("/rest/v1/sessions")
    assert captured["json"]["user_id"] == "u9"
    assert captured["json"]["delivery_score"] == 82
    assert captured["json"]["topic"] == "climate"


def test_record_session_fail_open_on_error(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")

    def boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(persistence.httpx, "post", boom)
    assert persistence.record_session("u1", {"topic": "x"}) is False


def test_build_summary_shapes_row():
    row = persistence.build_summary(
        topic="climate", mode="pitch",
        stats={"fillerCount": 3, "pauseCount": 1, "wpm": 130},
        total_words=210, duration_seconds=95.4,
        delivery={"score": 82}, content_score=74, verdict="Solid and focused.",
    )
    assert row["topic"] == "climate"
    assert row["filler_count"] == 3
    assert row["total_words"] == 210
    assert row["duration_seconds"] == 95  # rounded int
    assert row["delivery_score"] == 82
    assert row["content_score"] == 74
    assert row["verdict"] == "Solid and focused."


def test_build_summary_tolerates_missing_scores():
    row = persistence.build_summary(
        topic="", mode="individual", stats={}, total_words=0,
        duration_seconds=0, delivery=None, content_score=None, verdict="",
    )
    assert row["delivery_score"] is None
    assert row["content_score"] is None
    assert row["filler_count"] == 0
