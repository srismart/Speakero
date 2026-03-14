import os
import asyncio
import time
import httpx
import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MAIN_SERVER_URL = os.getenv("MAIN_SERVER_URL", "http://localhost:8000")
CHUNK_INTERVAL_SECONDS = 30

app = FastAPI(title="Speakero Background Agent")

_buffer_lock = asyncio.Lock()
_transcript_buffer: list[str] = []
_session_active = False
_session_mode: str = "individual"
_session_topic: str = ""


@app.post("/chunk")
async def receive_chunk(request: Request):
    global _session_active
    data = await request.json()
    text = data.get("text", "").strip()
    if text:
        async with _buffer_lock:
            _transcript_buffer.append(text)
            _session_active = True
    return JSONResponse({"status": "ok"})


@app.post("/start")
async def start_session(request: Request):
    global _session_active, _session_mode, _session_topic
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    async with _buffer_lock:
        _transcript_buffer.clear()
        _session_active = True
    _session_mode = data.get("mode", "individual")
    _session_topic = data.get("topic", "")
    return JSONResponse({"status": "started"})


@app.post("/stop")
async def stop_session():
    global _session_active
    _session_active = False
    return JSONResponse({"status": "stopped"})


async def _get_nudge_from_claude(transcript_snippet: str) -> str:
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    # Build mode-specific coaching focus
    if _session_mode == "panel":
        mode_instruction = (
            "This is a PANEL interview or Q&A. Focus on: conciseness (flag if the answer is too long), "
            "direct responsiveness to questions, and avoiding rambling. "
            "If the answer seems too long or loses focus, nudge the speaker to be more concise."
        )
    elif _session_mode == "pitch":
        mode_instruction = (
            "This is a PITCH presentation. Focus on: persuasiveness, hook quality, "
            "and whether there is a clear call-to-action. "
            "Nudge the speaker toward stronger value propositions or a clearer ask."
        )
    else:
        # individual
        mode_instruction = (
            "This is an individual talk or presentation. Focus on: narrative flow, "
            "smooth transitions between points, and building toward a clear conclusion."
        )

    # Topic drift detection
    topic_instruction = ""
    if _session_topic:
        topic_instruction = (
            f"\n\nThe speaker's stated topic is: \"{_session_topic}\". "
            "If the snippet seems to drift off-topic or lose relevance to this topic, "
            "make the nudge about getting back on track with the topic."
        )

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=64,
        messages=[
            {
                "role": "user",
                "content": (
                    f"You are a speaking coach. {mode_instruction}{topic_instruction}\n\n"
                    "Based on this transcript snippet, write ONE short nudge (max 15 words) for the speaker. "
                    "Return only the nudge text, no quotes, no explanation.\n\n"
                    f"Transcript:\n{transcript_snippet}"
                ),
            }
        ],
    )
    return message.content[0].text.strip()


async def _post_nudge_to_main(nudge_text: str):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{MAIN_SERVER_URL}/nudge",
                json={"text": nudge_text},
            )
    except Exception as e:
        print(f"[agent] Failed to post nudge: {e}")


async def coaching_loop():
    print("[agent] Coaching loop started")
    while True:
        await asyncio.sleep(CHUNK_INTERVAL_SECONDS)

        async with _buffer_lock:
            if not _transcript_buffer:
                continue
            snippet = " ".join(_transcript_buffer)
            _transcript_buffer.clear()

        if not snippet.strip():
            continue

        print(f"[agent] Generating nudge for snippet ({len(snippet)} chars)…")
        try:
            nudge = await _get_nudge_from_claude(snippet)
            print(f"[agent] Nudge: {nudge}")
            await _post_nudge_to_main(nudge)
        except Exception as e:
            print(f"[agent] Error generating nudge: {e}")


@app.on_event("startup")
async def startup():
    asyncio.create_task(coaching_loop())


if __name__ == "__main__":
    uvicorn.run("background_agent:app", host="0.0.0.0", port=8001, reload=False)
