import feedback
import main
from fastapi.testclient import TestClient


def test_record_feedback_noop_without_supabase(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    # Must not raise and must report it did not persist.
    assert feedback.record_feedback(None, "sid", "up", "great", "topic") is False


def test_record_feedback_posts_when_configured(monkeypatch):
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

    monkeypatch.setattr(feedback.httpx, "post", fake_post)
    assert feedback.record_feedback("u1", "sid1", "down", "meh", "climate") is True
    assert captured["url"].endswith("/rest/v1/session_feedback")
    assert captured["json"]["rating"] == "down"
    assert captured["json"]["user_id"] == "u1"


def test_record_feedback_fail_open_on_error(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")

    def boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(feedback.httpx, "post", boom)
    # Fail-open: never raises, reports not-persisted.
    assert feedback.record_feedback("u1", "sid1", "up", "", "") is False


def _mk_session(sid, user_id=None):
    sess = main.SessionState()
    sess.room = sid
    sess.user_id = user_id
    main.SESSIONS[sid] = sess
    return sess


def test_feedback_endpoint_unknown_sid():
    client = TestClient(main.fastapi_app)
    res = client.post("/api/feedback", json={"sid": "nope", "rating": "up"})
    assert res.status_code == 404


def test_feedback_endpoint_invalid_rating():
    _mk_session("s-fb-bad")
    client = TestClient(main.fastapi_app)
    res = client.post("/api/feedback", json={"sid": "s-fb-bad", "rating": "sideways"})
    assert res.status_code == 400
    main.SESSIONS.pop("s-fb-bad", None)


def test_frontend_feedback_widget_wired():
    html = open("static/index.html", encoding="utf-8").read()
    for dom_id in ("feedbackUp", "feedbackDown", "feedbackText", "feedbackSend"):
        assert f'id="{dom_id}"' in html
    assert "/api/feedback" in html
    assert "resetFeedback()" in html


def test_feedback_endpoint_ok(monkeypatch):
    calls = {"n": 0}

    def fake_record(user_id, sid, rating, comment, topic):
        calls["n"] += 1
        return False

    monkeypatch.setattr(main.feedback, "record_feedback", fake_record)
    _mk_session("s-fb-ok")
    client = TestClient(main.fastapi_app)
    res = client.post("/api/feedback", json={"sid": "s-fb-ok", "rating": "up", "comment": "nice"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert calls["n"] == 1
    main.SESSIONS.pop("s-fb-ok", None)
