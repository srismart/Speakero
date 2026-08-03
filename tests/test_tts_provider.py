import asyncio

import pytest

import tts_provider


def test_factory_default_is_smallest(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    tts_provider.reset_for_tests()
    p = tts_provider.get_tts_provider()
    assert p.name == "smallest"


def test_factory_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "bogus")
    tts_provider.reset_for_tests()
    with pytest.raises(ValueError):
        tts_provider.get_tts_provider()
    tts_provider.reset_for_tests()


def test_cache_key_scopes_provider_model_voice(monkeypatch):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    tts_provider.reset_for_tests()
    key = tts_provider.get_tts_provider().cache_key("hi")
    assert key.startswith("smallest|")
    assert "hi" in key


def test_speak_caches(monkeypatch):
    tts_provider.reset_for_tests()

    class FakeProvider:
        name = "fake"
        calls = 0

        def cache_key(self, text):
            return f"fake|{text}"

        async def synthesize(self, text):
            FakeProvider.calls += 1
            return b"WAVDATA"

    monkeypatch.setattr(tts_provider, "get_tts_provider", lambda: FakeProvider())
    assert asyncio.run(tts_provider.speak("hello")) == b"WAVDATA"
    assert asyncio.run(tts_provider.speak("hello")) == b"WAVDATA"
    assert FakeProvider.calls == 1
    tts_provider.reset_for_tests()


def test_smallest_requires_api_key(monkeypatch):
    monkeypatch.delenv("SMALLEST_API_KEY", raising=False)
    with pytest.raises(ValueError):
        asyncio.run(tts_provider.SmallestLightningTTS().synthesize("x"))
