# Speakero — Handoff Document

> Last updated: 2026-06-06
> Author: Sriram Santhanam
> Branch: refactor/single-process-voice-api

Use this document to resume development after any interruption. Update "Current Status" and "Next Steps" as work progresses.

---

## Project Summary

**Speakero** is a real-time AI speaking coach.
It captures mic audio, detects filler words live, sends Claude-powered coaching nudges via TTS every 30s, and generates a full end-of-session debrief. Now multi-tenant and deployable as a single container.

---

## File Map

| File | Status | Purpose |
|---|---|---|
| `main.py` | ✅ Complete | FastAPI + socket.io ASGI app. Per-sid session management, Pulse STT bridge, coaching loop, all REST endpoints. |
| `filler_detector.py` | ✅ Complete | Filler word/phrase detection, WPM, pause gaps, streak detection (3 in 10s), highlight window tracking. |
| `tts.py` | ✅ Complete | smallest.ai Lightning TTS wrapper (async). |
| `report.py` | ✅ Complete | Claude Sonnet debrief: strengths, improvements, content feedback, repetition/jargon flags, sentence completion, highlight moment, spoken feedback, example extract. |
| `static/index.html` | ✅ Complete | Full SPA: bento grid UI, floating control bar, animated visualizer, inline filler highlighting, debrief modal. Passes `socket.id` to all API calls and the audio WS. |
| `requirements.txt` | ✅ Complete | `smallestai`, `anthropic`, `fastapi`, `uvicorn`, `python-socketio`, `websockets`, `httpx`, etc. |
| `Dockerfile` | ✅ Complete | python:3.12-slim, reads `PORT` env var, EXPOSE 8080. |
| `render.yaml` | ✅ Complete | Render.com deployment config (docker runtime, secrets via dashboard). |
| `fly.toml` | ✅ Complete | Fly.io deployment config (sjc region, 512 MB shared VM, auto-stop). |
| `.dockerignore` | ✅ Complete | Excludes `.env`, `.git`, `__pycache__`, `.claude`, `.playwright-mcp`. |
| `background_agent.py` | ✅ Deleted (2026-06-10) | Original separate process on :8001. Functionality fully absorbed into `main.py`. |
| `.env.example` | ✅ Complete | Template — do NOT commit `.env` with real keys. |
| `README.md` | ✅ Updated (2026-06-10) | Documents the single-process architecture. |

---

## Architecture

```
Browser (index.html)
  │  socket.io connect → server assigns sid, creates SessionState, joins room sid
  │  POST /api/start   {sid, mode, topic} → spawns coaching_loop(sess, sid)
  │  PCM audio         WebSocket /ws/audio?sample_rate=N&sid=S
  │  socket.io ←       transcript, stats, filler_detected, filler_streak, nudge
  │                    (all events targeted to room=sid, never broadcast)
  ▼
main.py (:8080)
  ├── SESSIONS: dict[str, SessionState]   — one entry per connected tab
  ├── Pulse STT (smallest.ai)             — filler_detector.py → socket.io events to room=sid
  ├── coaching_loop(sess, sid)            — per-session asyncio.Task, 30s cadence → Claude nudge → room=sid
  ├── tts.py                              — POST /api/speak → WAV
  └── report.py                          — POST /api/report → JSON debrief
```

---

## Socket.IO Events (backend → frontend)

All events are sent to `room=sid` — never broadcast.

| Event | Payload | Trigger |
|---|---|---|
| `transcript` | `{text, is_final}` | Every STT segment |
| `stats` | `{fillerCount, pauseCount, wpm}` | Every STT segment |
| `filler_detected` | `{words: [...]}` | When fillers found in segment |
| `filler_streak` | `{}` | 3+ fillers within 10 seconds |
| `nudge` | `{text}` | coaching_loop fires every 30s |

---

## REST Endpoints

All session endpoints require `sid` (the socket.io client id) in the JSON body. Returns 404 if sid is unknown.

| Method + Path | Body | Description |
|---|---|---|
| `POST /api/start` | `{sid, mode, topic}` | Start session, spawn coaching loop |
| `POST /api/stop` | `{sid}` | Stop session, cancel coaching loop |
| `POST /api/speak` | `{text}` | Returns WAV bytes via Lightning TTS |
| `POST /api/report` | `{sid, topic}` | Claude debrief JSON |
| `GET /` | — | Serves `static/index.html` |

---

## How to Run

```bash
# Single process — no background agent needed
uvicorn main:app --port 8080

# Browser
open http://localhost:8080
```

### Docker

```bash
docker build -t speakero .
docker run -p 8080:8080 \
  -e SMALLEST_API_KEY=... \
  -e ANTHROPIC_API_KEY=... \
  speakero
```

---

## Key Implementation Notes

- **Session lifecycle**: `SESSIONS[sid]` created on socket.io `connect`, destroyed on `disconnect`. coaching_loop task cancelled on `disconnect` and `/api/stop`.
- **Pulse STT URL**: `wss://api.smallest.ai/waves/v1/pulse/get_text` — the older `/v1/pulse/stream` returns 404.
- **Sample rate**: browser passes `audioContext.sampleRate` as query param. Do not hardcode — browsers ignore the AudioContext `sampleRate` hint.
- **Filler detection — final only**: only run `process_words()` on `is_final=true` segments. Interim messages repeat words, causing double-counting.
- **smallestai package**: PyPI name is `smallestai` not `smallest`. Import: `from smallestai import WavesClient`.
- **TTS AudioContext**: create `ttsCtx = new AudioContext()` on Start button click (user gesture), reuse for all nudge/debrief playback. Never create per-call.
- **Start button disabled until socket connects**: frontend waits for `socket.on('connect')` before enabling Start, so `socket.id` is always valid when Start is clicked.
- **Port 8080**: port 8000 may have a zombie socket on this machine after force-kills.

---

## Current Status (2026-06-06)

Cloud refactor complete on branch `refactor/single-process-voice-api`:

- Multi-tenant per-sid session isolation ✅
- Per-session coaching loop (spawn on start, cancel on stop/disconnect) ✅
- All socket.io events targeted to room=sid ✅
- Frontend passes socket.id to all API calls and audio WS ✅
- Dockerfile + render.yaml + fly.toml ✅
- Smoke tested: two tabs, independent sessions, bogus sid → 404 ✅

Not yet merged to `main`.

---

## Next Steps (in order)

1. **Merge `refactor/single-process-voice-api` → `main`** — open PR, review, merge
2. **stt_provider.py + tts_provider.py abstraction** — swap smallest.ai without touching main.py
3. **User auth** — Clerk
4. **Session persistence** — Supabase
5. **Usage metering + billing** — Stripe
6. **Session history UI + PDF export**

---

## Parked Enhancements

- Eye contact prompts (webcam integration)
- Confidence scoring from audio amplitude envelope
- Vocabulary richness / type-token ratio
- Time limit warnings
- Export session transcript as PDF
