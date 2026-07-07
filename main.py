import os
import json
import asyncio
import time
import uuid
import httpx
import websockets
import anthropic
from contextlib import asynccontextmanager
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
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

PULSE_WS_URL = "wss://api.smallest.ai/waves/v1/pulse/get_text"
CHUNK_INTERVAL_SECONDS = 30

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


class SessionState:
    def __init__(self):
        self.active = False
        self.detector = FillerDetector()
        self._transcript_buffer: list[str] = []
        self._buffer_lock: asyncio.Lock = asyncio.Lock()
        self.coaching_task: asyncio.Task | None = None
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
        self._transcript_buffer.clear()

    def stop(self):
        self.active = False

    def add_text(self, text: str):
        if text.strip():
            self.full_transcript_parts.append(text)

    def full_transcript(self) -> str:
        return " ".join(self.full_transcript_parts)

    def elapsed_seconds(self) -> float:
        return (time.time() - self.start_time) if self.start_time else 0.0


SESSIONS: dict[str, SessionState] = {}


async def _get_nudge_from_claude(transcript_snippet: str, sess: SessionState) -> str:
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    if sess.mode == "panel":
        mode_instruction = (
            "This is a PANEL interview or Q&A. Focus on: conciseness (flag if the answer is too long), "
            "direct responsiveness to questions, and avoiding rambling. "
            "If the answer seems too long or loses focus, nudge the speaker to be more concise."
        )
    elif sess.mode == "pitch":
        mode_instruction = (
            "This is a PITCH presentation. Focus on: persuasiveness, hook quality, "
            "and whether there is a clear call-to-action. "
            "Nudge the speaker toward stronger value propositions or a clearer ask."
        )
    else:
        mode_instruction = (
            "This is an individual talk or presentation. Focus on: narrative flow, "
            "smooth transitions between points, and building toward a clear conclusion."
        )

    topic_instruction = ""
    if sess.topic:
        topic_instruction = (
            f"\n\nThe speaker's stated topic is: \"{sess.topic}\". "
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


async def coaching_loop(sess: SessionState, sid: str):
    print(f"[agent] Coaching loop started for {sid}")
    while True:
        await asyncio.sleep(CHUNK_INTERVAL_SECONDS)

        async with sess._buffer_lock:
            if not sess._transcript_buffer:
                continue
            snippet = " ".join(sess._transcript_buffer)
            sess._transcript_buffer.clear()

        if not snippet.strip():
            continue

        print(f"[agent] Generating nudge for {sid} ({len(snippet)} chars)…")
        try:
            nudge = await _get_nudge_from_claude(snippet, sess)
            print(f"[agent] Nudge: {nudge}")
            await sio.emit("event", {"type": "nudge", "text": nudge}, room=sid)
        except Exception as e:
            print(f"[agent] Error generating nudge: {e}")


@asynccontextmanager
async def lifespan(app):
    yield


fastapi_app = FastAPI(title="Speakero", lifespan=lifespan)

fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")


@sio.event
async def connect(sid, environ):
    SESSIONS[sid] = SessionState()
    await sio.enter_room(sid, sid)
    print(f"[sio] Client connected: {sid}")


@sio.event
async def disconnect(sid):
    sess = SESSIONS.pop(sid, None)
    if sess and sess.coaching_task and not sess.coaching_task.done():
        sess.coaching_task.cancel()
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
    data = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
        except Exception:
            pass
    sid = data.get("sid", "")
    sess = SESSIONS.get(sid)
    if sess is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    transcript = sess.full_transcript()
    stats = sess.detector.get_stats()
    topic = data.get("topic", "")
    mode = sess.mode
    best_window = sess.detector.get_best_window()
    highlight_window = best_window["text"] if best_window else ""
    try:
        report = await generate_report(
            transcript,
            stats,
            topic=topic,
            mode=mode,
            highlight_window=highlight_window,
            candidate_windows=sess.detector.get_replay_candidates(),
        )
        report["replay"] = sess.detector.get_replay_windows(
            roughest_index=report.pop("roughest_window_index", None)
        )
        return JSONResponse(report)
    except Exception as e:
        # Stats and replay windows are computed locally — never lose the session
        # just because the AI analysis failed.
        print(f"[report] Error: {e} — returning degraded report")
        return JSONResponse({
            "degraded": True,
            "summary": "AI analysis is temporarily unavailable. Your session stats and replay are below.",
            "topic_identified": "",
            "strengths": [],
            "improvements": [],
            "content_feedback": [],
            "filler_breakdown": stats.get("fillerBreakdown", {}),
            "transcript": transcript,
            "replay": sess.detector.get_replay_windows(),
        })


@fastapi_app.post("/api/start")
async def api_start(request: Request):
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    sid = data.get("sid", "")
    mode = data.get("mode", "individual")
    topic = data.get("topic", "")
    sess = SESSIONS.get(sid)
    if sess is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    sess.start()
    sess.mode = mode
    sess.topic = topic
    if sess.coaching_task and not sess.coaching_task.done():
        sess.coaching_task.cancel()
    sess.coaching_task = asyncio.create_task(coaching_loop(sess, sid))
    return JSONResponse({"status": "started"})


@fastapi_app.post("/api/stop")
async def api_stop(request: Request):
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    sid = data.get("sid", "")
    sess = SESSIONS.get(sid)
    if sess is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    sess.stop()
    if sess.coaching_task and not sess.coaching_task.done():
        sess.coaching_task.cancel()
    sess.coaching_task = None
    return JSONResponse({"status": "stopped"})


@fastapi_app.websocket("/ws/audio")
async def audio_ws(websocket: WebSocket):
    await websocket.accept()
    sample_rate = websocket.query_params.get("sample_rate", "48000")
    sid = websocket.query_params.get("sid", "")
    sess = SESSIONS.get(sid)
    if sess is None:
        await websocket.close(code=4004)
        return
    print(f"[ws] Browser audio WebSocket connected (sid={sid}, sample_rate={sample_rate})")

    pulse_url = (
        f"{PULSE_WS_URL}"
        f"?sample_rate={sample_rate}"
        f"&encoding=linear16"
        f"&language=en"
        f"&word_timestamps=true"
    )

    try:
        async with websockets.connect(
            pulse_url,
            additional_headers={"Authorization": f"Bearer {SMALLEST_API_KEY}"},
            ping_interval=20,
            ping_timeout=30,
        ) as pulse_ws:
            await _bridge_audio(websocket, pulse_ws, sess, sid)
    except websockets.exceptions.InvalidURI:
        print("[ws] WARNING: Could not connect to Pulse STT — running in offline mode")
        await _offline_mode(websocket, sess, sid)
    except Exception as e:
        print(f"[ws] Pulse connection error: {type(e).__name__}: {e}")
        print(f"[ws] Pulse URL was: {pulse_url}")
        print(f"[ws] API key present: {bool(SMALLEST_API_KEY)}, len={len(SMALLEST_API_KEY)}")
        await _offline_mode(websocket, sess, sid)
    finally:
        print("[ws] Audio WebSocket closed")


async def _bridge_audio(browser_ws: WebSocket, pulse_ws, sess: SessionState, sid: str):
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
                await _handle_pulse_message(raw_msg, sess, sid)
        except Exception as e:
            print(f"[ws] Pulse→frontend error: {e}")

    await asyncio.gather(
        forward_browser_to_pulse(),
        forward_pulse_to_frontend(),
    )


async def _handle_pulse_message(raw_msg: str | bytes, sess: SessionState, sid: str):
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
        result = sess.detector.process_words(words)
        new_fillers = result["new_fillers"]
        stats = result["stats"]

        text_from_words = " ".join(w.get("word", "") for w in words)
        sess.add_text(text_from_words)

        await sio.emit("event", {"type": "transcript", "text": text_from_words, "is_final": is_final}, room=sid)
        await sio.emit("event", {"type": "stats", **stats}, room=sid)

        if new_fillers:
            await sio.emit("event", {"type": "filler_detected", "words": new_fillers}, room=sid)

        if result.get("streak"):
            await sio.emit("event", {"type": "filler_streak"}, room=sid)

        if is_final and text_from_words.strip():
            async with sess._buffer_lock:
                sess._transcript_buffer.append(text_from_words)

    elif transcript_text:
        if is_final:
            dummy_words = [
                {"word": w, "start": 0.0, "end": 0.0}
                for w in transcript_text.split()
                if w.strip()
            ]
            if dummy_words:
                result = sess.detector.process_words(dummy_words)
                new_fillers = result["new_fillers"]
                stats = result["stats"]
                sess.add_text(transcript_text)
                await sio.emit("event", {"type": "stats", **stats}, room=sid)
                if new_fillers:
                    await sio.emit("event", {"type": "filler_detected", "words": new_fillers}, room=sid)
                if result.get("streak"):
                    await sio.emit("event", {"type": "filler_streak"}, room=sid)
            async with sess._buffer_lock:
                sess._transcript_buffer.append(transcript_text)
        await sio.emit("event", {"type": "transcript", "text": transcript_text, "is_final": is_final}, room=sid)


async def _offline_mode(websocket: WebSocket, sess: SessionState, sid: str):
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
                # No real word timestamps in offline mode — keep them zeroed so the
                # replay windows stay gated out and the debrief replay card stays hidden.
                word_obj = [{"word": mock_words[counter // 20 % len(mock_words)], "start": 0.0, "end": 0.0}]
                result = sess.detector.process_words(word_obj)
                text = word_obj[0]["word"]
                sess.add_text(text)
                await sio.emit("event", {"type": "transcript", "text": text, "is_final": True}, room=sid)
                await sio.emit("event", {"type": "stats", **result["stats"]}, room=sid)
                if result["new_fillers"]:
                    await sio.emit("event", {"type": "filler_detected", "words": result["new_fillers"]}, room=sid)
                if result.get("streak"):
                    await sio.emit("event", {"type": "filler_streak"}, room=sid)
    except WebSocketDisconnect:
        pass


app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
