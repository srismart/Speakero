import os
import asyncio
from smallestai import WavesClient


async def speak(text: str) -> bytes:
    """
    Call smallest.ai Lightning TTS and return raw audio bytes (WAV).
    Runs the synchronous SDK call in a thread executor.
    """
    api_key = os.getenv("SMALLEST_API_KEY")
    if not api_key:
        raise ValueError("SMALLEST_API_KEY environment variable is not set")

    loop = asyncio.get_event_loop()

    def _synthesize() -> bytes:
        client = WavesClient(
            api_key=api_key,
            model="lightning",
            voice_id="emily",
            sample_rate=24000,
        )
        return client.synthesize(text)

    return await loop.run_in_executor(None, _synthesize)
