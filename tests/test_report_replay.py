from fastapi.testclient import TestClient
import main


def test_report_includes_replay(monkeypatch):
    async def fake_report(*args, **kwargs):
        return {"summary": "ok"}

    monkeypatch.setattr(main, "generate_report", fake_report)

    sess = main.SessionState()
    sess.start()
    sess.detector.process_words([
        {"word": "we", "start": 0.0, "end": 0.3},
        {"word": "ship", "start": 0.3, "end": 0.7},
        {"word": "fast", "start": 0.7, "end": 1.1},
        {"word": "today", "start": 1.1, "end": 1.6},
    ])
    main.SESSIONS["testsid"] = sess

    client = TestClient(main.fastapi_app)
    res = client.post("/api/report", json={"sid": "testsid", "topic": "x"})

    assert res.status_code == 200
    body = res.json()
    assert "replay" in body
    assert body["replay"]["best"]["text"] == "we ship fast today"
    assert body["replay"]["best"]["start"] == 0.0
    assert body["replay"]["best"]["end"] == 1.6
