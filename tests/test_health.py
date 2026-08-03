from fastapi.testclient import TestClient

import main


def test_healthz_returns_ok():
    client = TestClient(main.fastapi_app)
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert isinstance(body["sessions"], int)


def test_parse_allowed_origins():
    assert main._parse_allowed_origins("*") == "*"
    assert main._parse_allowed_origins("") == "*"
    assert main._parse_allowed_origins("https://a.com") == ["https://a.com"]
    assert main._parse_allowed_origins("https://a.com, https://b.com") == [
        "https://a.com", "https://b.com",
    ]
