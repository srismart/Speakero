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
from stt_provider import STTEvent, get_stt_provider
from tts_provider import speak
from report import generate_report
from auth import verify_token
import limits
import scoring
import feedback
import persistence

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

CHUNK_INTERVAL_SECONDS = 30
GRACE_SECONDS = 90  # keep a disconnected session alive this long for reconnects


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


SILENCE_TIMEOUT_SECONDS = _env_float("SILENCE_TIMEOUT_SECONDS", 180.0)  # 0 disables
SILENCE_CHECK_INTERVAL_SECONDS = 15

def _parse_allowed_origins(raw: str):
    """Comma-separated origin allowlist; '*' or empty means allow all (dev default)."""
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins or origins == ["*"]:
        return "*"
    return origins


sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=_parse_allowed_origins(os.getenv("ALLOWED_ORIGINS", "*")),
)


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
        self.room: str = ""
        self.cleanup_task: asyncio.Task | None = None
        self.user_id: str | None = None
        self.tier: str = "anonymous"
        self.stream_epoch: int = -1  # increments per audio-WS connect; Pulse clock resets each time
        self.limit_task: asyncio.Task | None = None
        self.last_final_at: float | None = None
        self.silence_task: asyncio.Task | None = None
        self.audio_ws: WebSocket | None = None
        self.tts_calls: int = 0
        self.report_cache: dict | None = None
        self.usage: dict = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}

    def start(self):
        self.active = True
        self.detector.start_session()
        self.full_transcript_parts = []
        self.start_time = time.time()
        self.highlight_window = ""
        self.stream_epoch = -1
        self.last_final_at = time.time()
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

_anthropic_client: anthropic.AsyncAnthropic | None = None


def _get_anthropic() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=10.0)
    return _anthropic_client


async def _get_nudge_from_claude(transcript_snippet: str, sess: SessionState) -> str:
    client = _get_anthropic()

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
        model="claude-haiku-4-5",
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
    sess.usage["input_tokens"] += message.usage.input_tokens
    sess.usage["output_tokens"] += message.usage.output_tokens
    sess.usage["llm_calls"] += 1
    return message.content[0].text.strip()


def _silence_exceeded(sess: "SessionState", now: float, timeout: float) -> bool:
    """True when an active session has heard no final transcript for `timeout`s.
    A dead mic still streams audio to STT, so this is the only spend guard for
    abandoned tabs below the tier time cap."""
    if not sess.active or timeout <= 0:
        return False
    last = sess.last_final_at or sess.start_time or now
    return (now - last) > timeout


async def _silence_watchdog(sess: "SessionState"):
    while sess.active:
        await asyncio.sleep(SILENCE_CHECK_INTERVAL_SECONDS)
        if _silence_exceeded(sess, time.time(), SILENCE_TIMEOUT_SECONDS):
            sess.stop()
            if sess.coaching_task and not sess.coaching_task.done():
                sess.coaching_task.cancel()
            if sess.limit_task and not sess.limit_task.done():
                sess.limit_task.cancel()
            _log_session_usage(sess, "silence")
            await sio.emit("event", {
                "type": "auto_stopped",
                "reason": "silence",
                "timeout_seconds": SILENCE_TIMEOUT_SECONDS,
            }, room=sess.room)
            # Cut the server-side audio bridge too: the client may be wedged,
            # and forwarding to STT is the spend this guard exists to cap.
            ws = sess.audio_ws
            if ws is not None:
                try:
                    await ws.close(code=4005)
                except Exception:
                    pass
            return


async def coaching_loop(sess: SessionState):
    print(f"[agent] Coaching loop started for {sess.room}")
    while True:
        await asyncio.sleep(CHUNK_INTERVAL_SECONDS)

        async with sess._buffer_lock:
            if not sess._transcript_buffer:
                continue
            snippet = " ".join(sess._transcript_buffer)
            sess._transcript_buffer.clear()

        if not snippet.strip():
            continue

        print(f"[agent] Generating nudge for {sess.room} ({len(snippet)} chars)…")
        try:
            nudge = await _get_nudge_from_claude(snippet, sess)
            print(f"[agent] Nudge: {nudge}")
            await sio.emit("event", {"type": "nudge", "text": nudge}, room=sess.room)
        except Exception as e:
            print(f"[agent] Error generating nudge: {e}")


@asynccontextmanager
async def lifespan(app):
    yield


fastapi_app = FastAPI(title="Speakero", lifespan=lifespan)

fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")


@sio.event
async def connect(sid, environ, auth=None):
    sess = SessionState()
    sess.room = sid
    token = (auth or {}).get("token") if isinstance(auth, dict) else None
    ctx = verify_token(f"Bearer {token}") if token else None
    if ctx:
        sess.user_id, sess.tier = ctx.user_id, ctx.tier
    SESSIONS[sid] = sess
    await sio.enter_room(sid, sid)
    print(f"[sio] Client connected: {sid} (user={sess.user_id or 'anon'})")


@sio.event
async def disconnect(sid):
    sess = SESSIONS.get(sid)
    if sess is None:
        return
    print(f"[sio] Client disconnected: {sid} — {GRACE_SECONDS}s grace before cleanup")

    async def _cleanup():
        await asyncio.sleep(GRACE_SECONDS)
        gone = SESSIONS.pop(sid, None)
        if gone and gone.coaching_task and not gone.coaching_task.done():
            gone.coaching_task.cancel()
        if gone and gone.limit_task and not gone.limit_task.done():
            gone.limit_task.cancel()
        if gone and gone.silence_task and not gone.silence_task.done():
            gone.silence_task.cancel()
        print(f"[sio] Session {sid} cleaned up after grace period")

    sess.cleanup_task = asyncio.create_task(_cleanup())


@sio.event
async def resume(sid, data):
    """Re-attach a reconnected client (new sid) to its pre-disconnect session."""
    old_sid = (data or {}).get("old_sid", "")
    old_sess = SESSIONS.get(old_sid)
    if old_sess is None or old_sid == sid:
        return {"resumed": False}
    if old_sess.cleanup_task and not old_sess.cleanup_task.done():
        old_sess.cleanup_task.cancel()
    old_sess.cleanup_task = None
    old_sess.room = sid
    SESSIONS[sid] = old_sess
    del SESSIONS[old_sid]
    print(f"[sio] Session resumed: {old_sid} -> {sid}")
    return {"resumed": True}


@fastapi_app.get("/")
async def index():
    return FileResponse("static/index.html")


@fastapi_app.get("/api/config")
async def api_config():
    return JSONResponse({
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
    })


@fastapi_app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "ok", "sessions": len(SESSIONS)})


def _binding_check(request: Request, sess: SessionState):
    """Returns (auth_ctx, error_response|None). A session with an owner may
    only be driven by that owner; anonymous sessions are open (no identity
    to verify) and adopt the caller's identity at /api/start."""
    ctx = verify_token(request.headers.get("authorization"))
    if sess.user_id is not None and (ctx is None or ctx.user_id != sess.user_id):
        return ctx, JSONResponse({"error": "not your session"}, status_code=401)
    return ctx, None


@fastapi_app.post("/api/speak")
async def api_speak(request: Request):
    data = await request.json()
    text = data.get("text", "")
    sid = data.get("sid", "")
    if not text:
        return Response(status_code=400)
    sess = SESSIONS.get(sid)
    if sess is None:
        return JSONResponse({"error": "no active session"}, status_code=401)
    _ctx, err = _binding_check(request, sess)
    if err:
        return err
    cap = limits.get_config().get("tts_calls_per_session")
    if cap is not None and sess.tts_calls >= cap:
        return JSONResponse({"error": "tts limit reached for this session"}, status_code=429)
    sess.tts_calls += 1
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
    _ctx, err = _binding_check(request, sess)
    if err:
        return err
    if sess.report_cache is not None:
        return JSONResponse(sess.report_cache)
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
            usage_sink=sess.usage,
        )
        report["filler_breakdown"] = stats.get("fillerBreakdown", {})
        report["delivery"] = scoring.compute_delivery(
            filler_count=stats.get("fillerCount", 0),
            total_words=sess.detector.total_words,
            wpm=stats.get("wpm", 0),
            pause_count=stats.get("pauseCount", 0),
            duration_seconds=sess.elapsed_seconds(),
        )
        report["replay"] = sess.detector.get_replay_windows(
            roughest_index=report.pop("roughest_window_index", None)
        )
        sess.report_cache = report
        # Persist once, in the generation path only (cached returns bail out
        # above), for signed-in users. Fail-open: never blocks the debrief.
        persistence.record_session(sess.user_id, persistence.build_summary(
            topic=topic, mode=mode, stats=stats,
            total_words=sess.detector.total_words,
            duration_seconds=sess.elapsed_seconds(),
            delivery=report.get("delivery"),
            content_score=report.get("content_score"),
            verdict=report.get("verdict", ""),
        ))
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


@fastapi_app.post("/api/feedback")
async def api_feedback(request: Request):
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    sid = data.get("sid", "")
    sess = SESSIONS.get(sid)
    if sess is None:
        return JSONResponse({"error": "unknown session"}, status_code=404)
    _ctx, err = _binding_check(request, sess)
    if err:
        return err
    rating = data.get("rating", "")
    if rating not in ("up", "down"):
        return JSONResponse({"error": "rating must be 'up' or 'down'"}, status_code=400)
    persisted = feedback.record_feedback(
        sess.user_id, sid, rating, data.get("comment", ""), sess.topic
    )
    print(json.dumps({
        "event": "debrief_feedback",
        "user_id": sess.user_id,
        "tier": sess.tier,
        "rating": rating,
        "persisted": persisted,
    }))
    return JSONResponse({"status": "ok"})


def _log_session_usage(sess: SessionState, ended_by: str):
    print(json.dumps({
        "event": "session_usage",
        "user_id": sess.user_id,
        "tier": sess.tier,
        "seconds": round(sess.elapsed_seconds(), 1),
        "ended_by": ended_by,
        **sess.usage,
    }))


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
    ctx, err = _binding_check(request, sess)
    if err:
        return err
    if ctx:
        # Authoritative identity stamp: signing in mid-connection upgrades the session
        sess.user_id, sess.tier = ctx.user_id, ctx.tier

    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else ""))
    denied = limits.check_and_count(ctx, ip, data.get("marker", ""))
    if denied:
        return JSONResponse(denied, status_code=429)

    minutes = limits.resolve_session_minutes(sess.tier, data.get("requested_minutes"))

    sess.start()
    sess.mode = mode
    sess.topic = topic
    sess.tts_calls = 0
    sess.report_cache = None
    sess.usage = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
    if sess.coaching_task and not sess.coaching_task.done():
        sess.coaching_task.cancel()
    sess.coaching_task = asyncio.create_task(coaching_loop(sess))

    if sess.limit_task and not sess.limit_task.done():
        sess.limit_task.cancel()
    if sess.silence_task and not sess.silence_task.done():
        sess.silence_task.cancel()

    async def _limit_stop():
        await asyncio.sleep(minutes * 60)
        if not sess.active:
            return
        sess.stop()
        if sess.coaching_task and not sess.coaching_task.done():
            sess.coaching_task.cancel()
        if sess.silence_task and not sess.silence_task.done():
            sess.silence_task.cancel()
        _log_session_usage(sess, "limit")
        await sio.emit("event", {"type": "session_limit", "tier": sess.tier}, room=sess.room)

    sess.limit_task = asyncio.create_task(_limit_stop())
    sess.silence_task = asyncio.create_task(_silence_watchdog(sess))
    return JSONResponse({"status": "started", "max_minutes": minutes})


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
    _ctx, err = _binding_check(request, sess)
    if err:
        return err
    sess.stop()
    if sess.coaching_task and not sess.coaching_task.done():
        sess.coaching_task.cancel()
    sess.coaching_task = None
    if sess.limit_task and not sess.limit_task.done():
        sess.limit_task.cancel()
    sess.limit_task = None
    if sess.silence_task and not sess.silence_task.done():
        sess.silence_task.cancel()
    sess.silence_task = None
    _log_session_usage(sess, "user")
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
    sess.stream_epoch += 1
    sess.audio_ws = websocket
    if sess.active:
        # Fresh silence grace on every (re)connect so a network blip that just
        # healed is not immediately judged as user silence.
        sess.last_final_at = time.time()
    print(f"[ws] Browser audio WebSocket connected (sid={sid}, sample_rate={sample_rate}, epoch={sess.stream_epoch})")

    provider = get_stt_provider()
    stt_url = provider.ws_url(sample_rate)

    try:
        async with websockets.connect(
            stt_url,
            additional_headers=provider.ws_headers(),
            ping_interval=20,
            ping_timeout=30,
        ) as stt_ws:
            await _bridge_audio(websocket, stt_ws, sess, sid, provider)
    except websockets.exceptions.InvalidURI:
        print(f"[ws] WARNING: Could not connect to {provider.name} STT, running in offline mode")
        await _offline_mode(websocket, sess, sid)
    except Exception as e:
        print(f"[ws] {provider.name} connection error: {type(e).__name__}: {e}")
        print(f"[ws] STT URL was: {stt_url}")
        await _offline_mode(websocket, sess, sid)
    finally:
        if sess.audio_ws is websocket:
            sess.audio_ws = None
        print("[ws] Audio WebSocket closed")


async def _bridge_audio(browser_ws: WebSocket, stt_ws, sess: SessionState, sid: str, provider):
    async def forward_browser_to_stt():
        try:
            while True:
                chunk = await browser_ws.receive_bytes()
                await stt_ws.send(chunk)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[ws] Browser→STT error: {e}")
        finally:
            # Tear down the STT leg as soon as the browser leg ends (client
            # disconnect or watchdog close); otherwise the stt->frontend
            # forwarder lingers until the provider's ping timeout.
            try:
                await stt_ws.close()
            except Exception:
                pass

    async def forward_stt_to_frontend():
        try:
            async for raw_msg in stt_ws:
                event = provider.parse(raw_msg)
                if event is None:
                    print(f"[ws] Failed to parse {provider.name} message")
                    continue
                await _handle_stt_event(event, sess, sid)
        except Exception as e:
            print(f"[ws] STT→frontend error: {e}")

    await asyncio.gather(
        forward_browser_to_stt(),
        forward_stt_to_frontend(),
    )


async def _handle_stt_event(event: STTEvent, sess: SessionState, sid: str):
    is_final = event.is_final
    words = event.words
    transcript_text = event.text

    if words:
        print(f"[stt] words: {[w.get('word') for w in words]}")
        result = sess.detector.process_words(words, epoch=max(sess.stream_epoch, 0))
        new_fillers = result["new_fillers"]
        stats = result["stats"]

        text_from_words = " ".join(w.get("word", "").strip() for w in words)
        sess.add_text(text_from_words)

        await sio.emit("event", {"type": "transcript", "text": text_from_words, "is_final": is_final}, room=sid)
        await sio.emit("event", {"type": "stats", **stats}, room=sid)

        if new_fillers:
            await sio.emit("event", {"type": "filler_detected", "words": new_fillers}, room=sid)

        if result.get("streak"):
            await sio.emit("event", {"type": "filler_streak"}, room=sid)

        if is_final and text_from_words.strip():
            sess.last_final_at = time.time()
            async with sess._buffer_lock:
                sess._transcript_buffer.append(text_from_words)

    elif transcript_text:
        if is_final:
            sess.last_final_at = time.time()
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
                sess.last_final_at = time.time()  # offline mode must not trip the silence watchdog
                await sio.emit("event", {"type": "transcript", "text": text, "is_final": True}, room=sid)
                await sio.emit("event", {"type": "stats", **result["stats"]}, room=sid)
                if result["new_fillers"]:
                    await sio.emit("event", {"type": "filler_detected", "words": result["new_fillers"]}, room=sid)
                if result.get("streak"):
                    await sio.emit("event", {"type": "filler_streak"}, room=sid)
    except WebSocketDisconnect:
        pass


app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
