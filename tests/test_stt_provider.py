import json

import pytest

import stt_provider


def test_factory_default_is_pulse(monkeypatch):
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    stt_provider.reset_for_tests()
    assert stt_provider.get_stt_provider().name == "pulse"


def test_factory_unknown_raises(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "bogus")
    stt_provider.reset_for_tests()
    with pytest.raises(ValueError):
        stt_provider.get_stt_provider()
    stt_provider.reset_for_tests()


def test_pulse_url_and_headers(monkeypatch):
    monkeypatch.setenv("SMALLEST_API_KEY", "sk-test")
    p = stt_provider.PulseSTT()
    url = p.ws_url("48000")
    assert url.startswith("wss://api.smallest.ai/waves/v1/pulse/get_text?")
    assert "sample_rate=48000" in url and "word_timestamps=true" in url
    assert p.ws_headers() == {"Authorization": "Bearer sk-test"}


def test_pulse_parse_words_format():
    p = stt_provider.PulseSTT()
    ev = p.parse(json.dumps({
        "words": [{"word": "hi", "start": 0.1, "end": 0.3}],
        "is_final": True,
    }))
    assert ev.words == [{"word": "hi", "start": 0.1, "end": 0.3}]
    assert ev.is_final is True


def test_pulse_parse_text_format_camelcase():
    p = stt_provider.PulseSTT()
    ev = p.parse(json.dumps({"transcript": "hello there", "isFinal": True}).encode())
    assert ev.words == []
    assert ev.text == "hello there"
    assert ev.is_final is True


def test_pulse_parse_garbage_returns_none():
    p = stt_provider.PulseSTT()
    assert p.parse(b"\xff\xfe") is None
    assert p.parse("not json") is None
