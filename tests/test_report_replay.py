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


def test_report_passes_candidates_and_uses_claude_roughest_pick(monkeypatch):
    seen = {}

    async def fake_report(*args, **kwargs):
        seen["candidates"] = kwargs.get("candidate_windows")
        # Claude picks the rambling-but-filler-free window (index 1)
        return {"summary": "ok", "roughest_window_index": 1}

    monkeypatch.setattr(main, "generate_report", fake_report)

    sess = main.SessionState()
    sess.start()
    sess.detector.process_words([
        {"word": "we", "start": 0.0, "end": 0.3},
        {"word": "ship", "start": 0.3, "end": 0.7},
        {"word": "fast", "start": 0.7, "end": 1.1},
        {"word": "today", "start": 1.1, "end": 1.6},
    ])
    sess.detector.process_words([
        {"word": "hey", "start": 2.0, "end": 2.3},
        {"word": "yeah", "start": 2.3, "end": 2.6},
        {"word": "my", "start": 2.6, "end": 2.8},
        {"word": "product", "start": 2.8, "end": 3.2},
    ])
    main.SESSIONS["testsid2"] = sess

    client = TestClient(main.fastapi_app)
    res = client.post("/api/report", json={"sid": "testsid2", "topic": "x"})

    assert res.status_code == 200
    body = res.json()
    # candidates were passed into the report call
    assert [c["index"] for c in seen["candidates"]] == [0, 1]
    # Claude's pick became the "worst" replay window
    assert body["replay"]["worst"]["text"] == "hey yeah my product"
    assert body["replay"]["worst"]["start"] == 2.0
    # the internal index field is not leaked to the client
    assert "roughest_window_index" not in body
