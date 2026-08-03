# STT/TTS Provider Abstraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all smallest.ai specifics out of main.py behind `stt_provider.py` and `tts_provider.py` so STT/TTS vendors can be swapped via env var without touching app code (CLAUDE.md priority #1; prerequisite for Cartesia voice cloning).

**Architecture:** Thin Protocol-based providers. STT providers supply `ws_url(sample_rate)`, `ws_headers()`, and `parse(raw) -> STTEvent | None` (normalized `{words, text, is_final}`); main.py keeps the websocket bridging and detector logic but consumes normalized events. TTS providers supply `synthesize(text) -> bytes` and a `cache_key(text)`; the LRU cache and `speak()` entrypoint live provider-agnostic in tts_provider.py. Factories read `STT_PROVIDER` / `TTS_PROVIDER` env (defaults: pulse / smallest). tts.py is deleted; the one test importing it moves to tts_provider.

**Tech Stack:** Python Protocols, httpx, existing pytest suite.

**Branch:** `feat/provider-abstraction` off origin/main. One PR.

---

### Task 1: tts_provider.py

**Files:**
- Create: `tts_provider.py`
- Delete: `tts.py`
- Modify: `main.py:17` (import), `tests/test_guardrail_endpoints.py:146-177` (tts import block)
- Test: `tests/test_tts_provider.py` (create)

- [ ] **Step 1: Write failing tests** (`tests/test_tts_provider.py`)

```python
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
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_tts_provider.py -q` → ModuleNotFoundError

- [ ] **Step 3: Implement `tts_provider.py`** (logic moved from tts.py; REST-direct rationale comment preserved)

```python
import hashlib
import os
from collections import OrderedDict
from typing import Protocol

import httpx


class TTSProvider(Protocol):
    name: str

    def cache_key(self, text: str) -> str: ...

    async def synthesize(self, text: str) -> bytes: ...


class SmallestLightningTTS:
    """smallest.ai Lightning via REST. The bundled smallestai SDK (4.3.8) only
    knows retired models (lightning / lightning-large / lightning-v2) and sets
    no request timeout, so a retired model hangs forever. We call the current
    REST endpoint directly with a timeout instead."""

    name = "smallest"
    API_BASE = "https://api.smallest.ai/waves/v1"

    def __init__(self):
        self.model = os.getenv("SMALLEST_TTS_MODEL", "lightning-v3.1")
        self.voice = os.getenv("SMALLEST_TTS_VOICE", "avery")
        self.timeout = float(os.getenv("SMALLEST_TTS_TIMEOUT", "20"))

    def cache_key(self, text: str) -> str:
        return f"{self.name}|{self.model}|{self.voice}|{text}"

    async def synthesize(self, text: str) -> bytes:
        api_key = os.getenv("SMALLEST_API_KEY")
        if not api_key:
            raise ValueError("SMALLEST_API_KEY environment variable is not set")
        url = f"{self.API_BASE}/{self.model}/get_speech"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "text": text,
            "voice_id": self.voice,
            "sample_rate": 24000,
            "language": "en",
            "output_format": "wav",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(url, json=payload, headers=headers)
        if res.status_code != 200:
            raise RuntimeError(f"TTS failed: HTTP {res.status_code} {res.text[:200]}")
        return res.content


_PROVIDERS = {"smallest": SmallestLightningTTS}

_provider: TTSProvider | None = None

# Small in-memory LRU: repeated phrases (canned nudges, replayed feedback)
# cost zero TTS and play instantly.
_cache: OrderedDict[str, bytes] = OrderedDict()
_CACHE_MAX_ENTRIES = 128


def reset_for_tests():
    global _provider
    _provider = None
    _cache.clear()


def get_tts_provider() -> TTSProvider:
    global _provider
    if _provider is None:
        name = os.getenv("TTS_PROVIDER", "smallest")
        cls = _PROVIDERS.get(name)
        if cls is None:
            raise ValueError(f"Unknown TTS_PROVIDER '{name}'; options: {sorted(_PROVIDERS)}")
        _provider = cls()
    return _provider


async def speak(text: str) -> bytes:
    """Synthesize speech via the configured provider, with LRU caching.

    Raises on non-200 or timeout so the caller fails fast instead of hanging.
    """
    provider = get_tts_provider()
    key = hashlib.sha256(provider.cache_key(text).encode()).hexdigest()
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    audio = await provider.synthesize(text)
    _cache[key] = audio
    if len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)
    return audio
```

- [ ] **Step 4: Rewire.** `main.py:17` → `from tts_provider import speak`. Delete `tts.py` (`git rm tts.py`). Update `tests/test_guardrail_endpoints.py` tts block: `import tts_provider` instead of `import tts`; `tts_provider.reset_for_tests()` instead of `tts._cache.clear()`; monkeypatch `tts_provider.httpx`; call `tts_provider.speak`.

- [ ] **Step 5: Run full suite** — `python -m pytest -q` → all pass

- [ ] **Step 6: Commit** — `git add -A && git commit -m "refactor: tts_provider abstraction, smallest.ai behind TTS_PROVIDER env"`

### Task 2: stt_provider.py

**Files:**
- Create: `stt_provider.py`
- Modify: `main.py` (remove PULSE_WS_URL, audio_ws, `_bridge_audio`, rename `_handle_pulse_message` → `_handle_stt_event`)
- Test: `tests/test_stt_provider.py` (create)

- [ ] **Step 1: Write failing tests** (`tests/test_stt_provider.py`)

```python
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
```

- [ ] **Step 2: Run to verify fail** → ModuleNotFoundError

- [ ] **Step 3: Implement `stt_provider.py`**

```python
import json
import os
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class STTEvent:
    """Normalized streaming-STT message. Providers emit words with timestamps
    when they have them; text-only messages carry just `text`."""

    words: list = field(default_factory=list)  # [{word, start, end}, ...]
    text: str = ""
    is_final: bool = False


class STTProvider(Protocol):
    name: str

    def ws_url(self, sample_rate: str) -> str: ...

    def ws_headers(self) -> dict: ...

    def parse(self, raw_msg: str | bytes) -> STTEvent | None: ...


class PulseSTT:
    name = "pulse"
    # NOT /v1/pulse/stream: the older path returns 404.
    URL = "wss://api.smallest.ai/waves/v1/pulse/get_text"

    def ws_url(self, sample_rate: str) -> str:
        return (
            f"{self.URL}"
            f"?sample_rate={sample_rate}"
            f"&encoding=linear16"
            f"&language=en"
            f"&word_timestamps=true"
        )

    def ws_headers(self) -> dict:
        return {"Authorization": f"Bearer {os.getenv('SMALLEST_API_KEY', '')}"}

    def parse(self, raw_msg: str | bytes) -> STTEvent | None:
        try:
            if isinstance(raw_msg, bytes):
                raw_msg = raw_msg.decode("utf-8")
            data = json.loads(raw_msg)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return STTEvent(
            words=data.get("words", []) or [],
            text=data.get("transcript", data.get("text", "")) or "",
            is_final=bool(data.get("is_final", data.get("isFinal", False))),
        )


_PROVIDERS = {"pulse": PulseSTT}

_provider: STTProvider | None = None


def reset_for_tests():
    global _provider
    _provider = None


def get_stt_provider() -> STTProvider:
    global _provider
    if _provider is None:
        name = os.getenv("STT_PROVIDER", "pulse")
        cls = _PROVIDERS.get(name)
        if cls is None:
            raise ValueError(f"Unknown STT_PROVIDER '{name}'; options: {sorted(_PROVIDERS)}")
        _provider = cls()
    return _provider
```

- [ ] **Step 4: Rewire main.py.**
  - Import `from stt_provider import STTEvent, get_stt_provider`; drop `PULSE_WS_URL`.
  - `audio_ws`: `provider = get_stt_provider()`; connect to `provider.ws_url(sample_rate)` with `additional_headers=provider.ws_headers()`; pass `provider` into `_bridge_audio`; log line uses `provider.name`.
  - `_bridge_audio(browser_ws, stt_ws, sess, sid, provider)`: pulse→frontend forwarder becomes `event = provider.parse(raw_msg)`; `if event is None: print parse warning; continue`; `await _handle_stt_event(event, sess, sid)`.
  - Rename `_handle_pulse_message` → `_handle_stt_event(event: STTEvent, sess, sid)`; body drops its own json parsing and reads `event.words` / `event.text` / `event.is_final`; the two-branch structure (words path, text path) is unchanged.
- [ ] **Step 5: Run full suite** — `python -m pytest -q` → all pass
- [ ] **Step 6: Commit** — `git add -A && git commit -m "refactor: stt_provider abstraction, Pulse behind STT_PROVIDER env"`

### Task 3: Docs + PR

- [ ] **Step 1:** `.env.example`: add `STT_PROVIDER=pulse` and `TTS_PROVIDER=smallest` with a comment (`# Voice provider selection; current options: pulse / smallest`).
- [ ] **Step 2:** CLAUDE.md: mark priority 1 done — change `1. stt_provider.py + tts_provider.py abstraction layer` to `1. (done 2026-08-03) stt_provider.py + tts_provider.py abstraction layer`. HANDOFF.md: same change in Next Steps; add both new files to the File Map.
- [ ] **Step 3:** Commit docs; push; `gh pr create` (title "Provider abstraction: stt_provider + tts_provider"); wait CI; code-review pass; fix findings; squash-merge.

## Self-review
- Behavior preserved: parse fallbacks (`transcript`/`text`, `is_final`/`isFinal`), REST TTS payload, LRU semantics, offline-mode fallback untouched.
- test_guardrail_endpoints tts test must be updated in Task 1 Step 4 or the suite breaks mid-PR.
- Cache key now includes provider name so a future provider swap cannot serve stale audio.
