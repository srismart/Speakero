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
