import os
import json
import asyncio
import time
import uuid
import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse
import socketio
from dotenv import load_dotenv

from filler_detector import FillerDetector
from tts import speak
from report import generate_report

load_dotenv()

SMALLEST_API_KEY = os.getenv("SMALLEST_API_KEY", "")
BACKGROUND_AGENT_URL = os.getenv("BACKGROUND_AGENT_URL", "http://localhost:8001")

PULSE_WS_URL = "wss://api.smallest.ai/v1/pulse/stream"

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
fastapi_app = FastAPI(title="Speakero")

fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")


class SessionState:
    def __init__(self):
        self.active = False
        self.detector = FillerDetector()
        self.full_transcript_parts: list[str] = []
        self.start_time: float | None = None
        self.mode: str = "individual"
        self.topic: str = ""
        self.highlight_window: str = ""

    def start(self):
        self.active = True
        self.detector.start_session()
        self.full_transcript_parts = []
        self.start_time = time.time()
        self.highlight_window = ""

    def stop(self):
        self.active = False

    def add_text(self, text: str):
        if text.strip():
            self.full_transcript_parts.append(text)

    def full_transcript(self) -> str:
        return " ".join(self.full_transcript_parts)

    def elapsed_seconds(self) -> float:
        return (time.time() - self.start_time) if self.start_time else 0.0


session = SessionState()


@sio.event
async def connect(sid, environ):
    print(f"[sio] Client connected: {sid}")


@sio.event
async def disconnect(sid):
    print(f"[sio] Client disconnected: {sid}")


@fastapi_app.get("/")
async def index():
    return FileResponse("static/index.html")


@fastapi_app.post("/api/speak")
async def api_speak(request: Request):
    data = await request.json()
    text = data.get("text", "")
    if not text:
        return Response(status_code=400)
    try:
        audio_bytes = await speak(text)
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={"Cache-Control": "no-cache"},
        )
    except Exception as e:
        print(f"[tts] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@fastapi_app.post("/api/report")
async def api_report(request: Request):
    transcript = session.full_transcript()
    stats = session.detector.get_stats()
    data = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
        except Exception:
            pass
    topic = data.get("topic", "")
    mode = getattr(session, "mode", "individual")
    highlight_window = session.detector.get_best_window()
    try:
        report = await generate_report(
            transcript,
            stats,
            topic=topic,
            mode=mode,
            highlight_window=highlight_window,
        )
        return JSONResponse(report)
    except Exception as e:
        print(f"[report] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@fastapi_app.post("/nudge")
async def receive_nudge(request: Request):
    data = await request.json()
    nudge_text = data.get("text", "").strip()
    if nudge_text:
        await sio.emit("event", {"type": "nudge", "text": nudge_text})
        print(f"[nudge] Emitted: {nudge_text}")
    return JSONResponse({"status": "ok"})


@fastapi_app.post("/api/start")
async def api_start(request: Request):
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    mode = data.get("mode", "individual")
    topic = data.get("topic", "")
    session.start()
    session.mode = mode
    session.topic = topic
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{BACKGROUND_AGENT_URL}/start", json={"mode": mode, "topic": topic})
    except Exception:
        pass
    return JSONResponse({"status": "started"})


@fastapi_app.post("/api/stop")
async def api_stop():
    session.stop()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{BACKGROUND_AGENT_URL}/stop")
    except Exception:
        pass
    return JSONResponse({"status": "stopped"})


@fastapi_app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket):
    await websocket.accept()
    sample_rate = websocket.query_params.get("sample_rate", "48000")
    print(f"[ws] Browser audio WebSocket connected (sample_rate={sample_rate})")

    pulse_url = (
        f"{PULSE_WS_URL}"
        f"?api_key={SMALLEST_API_KEY}"
        f"&sample_rate={sample_rate}"
        f"&encoding=linear16"
        f"&language=en-US"
        f"&channels=1"
    )

    try:
        async with websockets.connect(
            pulse_url,
            extra_headers={"Authorization": f"Bearer {SMALLEST_API_KEY}"},
            ping_interval=20,
            ping_timeout=30,
        ) as pulse_ws:
            await _bridge_audio(websocket, pulse_ws)
    except websockets.exceptions.InvalidURI:
        print("[ws] WARNING: Could not connect to Pulse STT — running in offline mode")
        await _offline_mode(websocket)
    except Exception as e:
        print(f"[ws] Pulse connection error: {e}")
        await _offline_mode(websocket)
    finally:
        print("[ws] Audio WebSocket closed")


async def _bridge_audio(browser_ws: WebSocket, pulse_ws):
    async def forward_browser_to_pulse():
        try:
            while True:
                chunk = await browser_ws.receive_bytes()
                await pulse_ws.send(chunk)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[ws] Browser→Pulse error: {e}")

    async def forward_pulse_to_frontend():
        try:
            async for raw_msg in pulse_ws:
                await _handle_pulse_message(raw_msg)
        except Exception as e:
            print(f"[ws] Pulse→frontend error: {e}")

    await asyncio.gather(
        forward_browser_to_pulse(),
        forward_pulse_to_frontend(),
    )


async def _handle_pulse_message(raw_msg: str | bytes):
    try:
        if isinstance(raw_msg, bytes):
            raw_msg = raw_msg.decode("utf-8")
        data = json.loads(raw_msg)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[ws] Failed to parse Pulse message: {e}")
        return

    msg_type = data.get("type", "")
    is_final = data.get("is_final", data.get("isFinal", False))

    words = data.get("words", [])
    transcript_text = data.get("transcript", data.get("text", ""))

    if words:
        print(f"[pulse] words: {[w.get('word') for w in words]}")
        result = session.detector.process_words(words)
        new_fillers = result["new_fillers"]
        stats = result["stats"]

        text_from_words = " ".join(w.get("word", "") for w in words)
        session.add_text(text_from_words)

        await sio.emit("event", {"type": "transcript", "text": text_from_words, "is_final": is_final})
        await sio.emit("event", {"type": "stats", **stats})

        if new_fillers:
            await sio.emit("event", {"type": "filler_detected", "words": new_fillers})

        if result.get("streak"):
            await sio.emit("event", {"type": "filler_streak"})

        if is_final and text_from_words.strip():
            asyncio.create_task(_forward_chunk_to_agent(text_from_words))

    elif transcript_text:
        dummy_words = [
            {"word": w, "start": 0.0, "end": 0.0}
            for w in transcript_text.split()
            if w.strip()
        ]
        if dummy_words:
            result = session.detector.process_words(dummy_words)
            new_fillers = result["new_fillers"]
            stats = result["stats"]
            session.add_text(transcript_text)
            await sio.emit("event", {"type": "transcript", "text": transcript_text, "is_final": is_final})
            await sio.emit("event", {"type": "stats", **stats})
            if new_fillers:
                await sio.emit("event", {"type": "filler_detected", "words": new_fillers})
            if result.get("streak"):
                await sio.emit("event", {"type": "filler_streak"})
        if is_final and transcript_text.strip():
            asyncio.create_task(_forward_chunk_to_agent(transcript_text))


async def _forward_chunk_to_agent(text: str):
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{BACKGROUND_AGENT_URL}/chunk", json={"text": text})
    except Exception:
        pass


async def _offline_mode(websocket: WebSocket):
    counter = 0
    mock_words = [
        "So", "um", "the", "main", "point", "I", "wanted", "to", "make",
        "is", "that", "uh", "basically", "we", "need", "to", "like",
        "focus", "on", "the", "core", "features",
    ]
    try:
        while True:
            await websocket.receive_bytes()
            counter += 1
            if counter % 20 == 0:
                word_obj = [{"word": mock_words[counter // 20 % len(mock_words)], "start": float(counter), "end": float(counter) + 0.4}]
                result = session.detector.process_words(word_obj)
                text = word_obj[0]["word"]
                session.add_text(text)
                await sio.emit("event", {"type": "transcript", "text": text, "is_final": True})
                await sio.emit("event", {"type": "stats", **result["stats"]})
                if result["new_fillers"]:
                    await sio.emit("event", {"type": "filler_detected", "words": result["new_fillers"]})
                if result.get("streak"):
                    await sio.emit("event", {"type": "filler_streak"})
    except WebSocketDisconnect:
        pass


app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
