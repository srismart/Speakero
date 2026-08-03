import time

from fastapi.testclient import TestClient

import main


def test_start_initializes_last_final_at():
    sess = main.SessionState()
    sess.start()
    assert sess.last_final_at is not None
    assert abs(sess.last_final_at - time.time()) < 2


def test_silence_exceeded_logic():
    sess = main.SessionState()
    sess.start()
    now = sess.last_final_at
    assert main._silence_exceeded(sess, now + 10, timeout=180) is False
    assert main._silence_exceeded(sess, now + 181, timeout=180) is True
    sess.stop()
    assert main._silence_exceeded(sess, now + 181, timeout=180) is False


def test_silence_disabled_when_timeout_zero():
    sess = main.SessionState()
    sess.start()
    assert main._silence_exceeded(sess, sess.last_final_at + 9999, timeout=0) is False


def _mk_session(sid):
    sess = main.SessionState()
    sess.room = sid
    main.SESSIONS[sid] = sess
    return sess


def test_start_spawns_and_stop_cancels_silence_task(monkeypatch):
    monkeypatch.setattr(main.limits, "check_and_count", lambda ctx, ip, marker: None)
    sess = _mk_session("s-silence")
    with TestClient(main.fastapi_app) as client:
        res = client.post("/api/start", json={"sid": "s-silence"})
        assert res.status_code == 200
        assert sess.silence_task is not None
        client.post("/api/stop", json={"sid": "s-silence"})
        assert sess.silence_task is None
    main.SESSIONS.pop("s-silence", None)


def test_watchdog_stops_session_and_emits_auto_stopped(monkeypatch):
    monkeypatch.setattr(main.limits, "check_and_count", lambda ctx, ip, marker: None)
    monkeypatch.setattr(main, "SILENCE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(main, "SILENCE_CHECK_INTERVAL_SECONDS", 0.05)
    emitted = []

    async def fake_emit(event, data, room=None):
        emitted.append((data.get("type"), room))

    monkeypatch.setattr(main.sio, "emit", fake_emit)
    sess = _mk_session("s-quiet")
    with TestClient(main.fastapi_app) as client:
        client.post("/api/start", json={"sid": "s-quiet"})
        assert sess.active is True
        time.sleep(0.5)
        assert sess.active is False
    assert ("auto_stopped", "s-quiet") in emitted
    main.SESSIONS.pop("s-quiet", None)


def test_frontend_handles_auto_stopped_event():
    html = open("static/index.html", encoding="utf-8").read()
    assert "case 'auto_stopped'" in html
