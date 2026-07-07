import asyncio
import json
from types import SimpleNamespace

import pytest

import report


def make_fake_anthropic(responses):
    """Fake AsyncAnthropic whose messages.create returns each response in turn."""
    calls = {"n": 0}

    class FakeMessages:
        async def create(self, **kwargs):
            calls["n"] += 1
            text = responses[min(calls["n"] - 1, len(responses) - 1)]
            return SimpleNamespace(content=[SimpleNamespace(text=text)])

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    return FakeClient, calls


def test_retries_once_on_bad_json_then_succeeds(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    FakeClient, calls = make_fake_anthropic(["this is not json", '{"summary": "ok"}'])
    monkeypatch.setattr(report.anthropic, "AsyncAnthropic", FakeClient)

    result = asyncio.run(report.generate_report("hello world", {}))

    assert result["summary"] == "ok"
    assert calls["n"] == 2


def test_raises_after_two_bad_responses(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    FakeClient, calls = make_fake_anthropic(["bad", "still bad"])
    monkeypatch.setattr(report.anthropic, "AsyncAnthropic", FakeClient)

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(report.generate_report("hello world", {}))
    assert calls["n"] == 2
