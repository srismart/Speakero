import hashlib
import os
from collections import OrderedDict

import httpx

# The bundled smallestai SDK (4.3.8) only knows retired models
# (lightning / lightning-large / lightning-v2) and sets no request timeout, so a
# retired model hangs forever. We call the current REST endpoint directly with a
# timeout instead. Model/voice are env-overridable so we can swap without code changes.
TTS_API_BASE = "https://api.smallest.ai/waves/v1"
TTS_MODEL = os.getenv("SMALLEST_TTS_MODEL", "lightning-v3.1")
TTS_VOICE = os.getenv("SMALLEST_TTS_VOICE", "avery")
TTS_TIMEOUT_SECONDS = float(os.getenv("SMALLEST_TTS_TIMEOUT", "20"))

# Small in-memory LRU: repeated phrases (canned nudges, replayed feedback)
# cost zero TTS and play instantly.
_cache: OrderedDict[str, bytes] = OrderedDict()
_CACHE_MAX_ENTRIES = 128


async def speak(text: str) -> bytes:
    """
    Call smallest.ai Lightning TTS and return raw audio bytes (WAV).

    Raises on non-200 or timeout so the caller fails fast instead of hanging.
    """
    api_key = os.getenv("SMALLEST_API_KEY")
    if not api_key:
        raise ValueError("SMALLEST_API_KEY environment variable is not set")

    cache_key = hashlib.sha256(f"{TTS_MODEL}|{TTS_VOICE}|{text}".encode()).hexdigest()
    if cache_key in _cache:
        _cache.move_to_end(cache_key)
        return _cache[cache_key]

    url = f"{TTS_API_BASE}/{TTS_MODEL}/get_speech"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "text": text,
        "voice_id": TTS_VOICE,
        "sample_rate": 24000,
        "language": "en",
        "output_format": "wav",
    }

    async with httpx.AsyncClient(timeout=TTS_TIMEOUT_SECONDS) as client:
        res = await client.post(url, json=payload, headers=headers)

    if res.status_code != 200:
        raise RuntimeError(f"TTS failed: HTTP {res.status_code} {res.text[:200]}")
    _cache[cache_key] = res.content
    if len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)
    return res.content
