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
