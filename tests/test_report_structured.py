import asyncio
from types import SimpleNamespace

import report


class FakeClient:
    def __init__(self, text):
        self.kwargs = None
        self.messages = self
        self._text = text

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(text=self._text)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )


def test_structured_output_request_and_usage_sink(monkeypatch):
    fake = FakeClient('{"summary": "ok", "roughest_window_index": -1}')
    monkeypatch.setattr(report, "_get_client", lambda: fake)
    usage = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
    result = asyncio.run(report.generate_report("hello world", {}, usage_sink=usage))
    fmt = fake.kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert "filler_breakdown" not in fmt["schema"]["properties"]  # injected from stats, not LLM
    assert result["summary"] == "ok"
    assert result["roughest_window_index"] is None  # -1 normalized
    assert usage == {"input_tokens": 100, "output_tokens": 50, "llm_calls": 1}


def test_schema_includes_content_score_fields():
    props = report.REPORT_SCHEMA["properties"]
    assert props["content_score"]["type"] == "integer"
    assert props["verdict"]["type"] == "string"
    drivers = props["content_drivers"]
    assert drivers["type"] == "array"
    item = drivers["items"]
    assert item["properties"]["label"]["type"] == "string"
    assert item["properties"]["positive"]["type"] == "boolean"
    # direction-only: no numeric delta field on LLM-judged drivers
    assert "delta" not in item["properties"]
    for field in ("content_score", "verdict", "content_drivers"):
        assert field in report.REPORT_SCHEMA["required"]


def test_schema_includes_roughest_moment_note():
    props = report.REPORT_SCHEMA["properties"]
    assert props["roughest_moment_note"]["type"] == "string"
    assert "roughest_moment_note" in report.REPORT_SCHEMA["required"]


def test_content_score_clamped(monkeypatch):
    fake = FakeClient('{"summary": "ok", "roughest_window_index": -1, "content_score": 140}')
    monkeypatch.setattr(report, "_get_client", lambda: fake)
    result = asyncio.run(report.generate_report("hi", {}))
    assert result["content_score"] == 100


def test_roughest_index_normalization(monkeypatch):
    fake = FakeClient('{"summary": "ok", "roughest_window_index": 2}')
    monkeypatch.setattr(report, "_get_client", lambda: fake)
    result = asyncio.run(report.generate_report("hi", {}, candidate_windows=[{"index": 0, "text": "a"}]))
    assert result["roughest_window_index"] == 2
