# Speakero — Handoff Document

> Last updated: 2025-03-14
> Author: Sriram Santhanam
> Event: Voice AI HackSprint 2.0 — San Francisco

Use this document to resume development after any interruption. Update "Current Status" and "Next Steps" as work progresses.

---

## Project Summary

**Speakero** is a real-time AI speaking coach built for Voice AI HackSprint 2.0.
It captures mic audio, detects filler words live, sends Claude-powered coaching nudges via TTS, and generates a full end-of-session debrief.

---

## File Map

| File | Status | Purpose |
|---|---|---|
| `main.py` | ✅ Complete | FastAPI + socket.io server. Bridges browser audio → Pulse STT, routes all events, exposes REST endpoints. |
| `filler_detector.py` | ✅ Complete | Filler word/phrase detection, WPM, pause gaps, streak detection (3 in 10s), highlight window tracking. |
| `background_agent.py` | ✅ Complete | Separate FastAPI on :8001. Accumulates transcript 30s → Claude nudge (mode-aware + topic-drift aware) → POSTs to main.py. |
| `tts.py` | ✅ Complete | smallest.ai Lightning TTS wrapper (async). |
| `report.py` | ✅ Complete | Claude Sonnet debrief: strengths, improvements, content feedback, repetition/jargon flags, sentence completion, highlight moment, spoken feedback, example extract. |
| `static/index.html` | ✅ Complete | Full SPA: bento grid UI, floating control bar, animated visualizer, inline filler highlighting, debrief rendering. |
| `requirements.txt` | ✅ Complete | `smallestai`, `anthropic`, `fastapi`, `uvicorn`, `python-socketio`, `websockets`, `httpx`, etc. |
| `.env.example` | ✅ Complete | Template — do NOT commit `.env` with real keys. |
| `.gitignore` | ✅ Complete | Excludes `.env`, `__pycache__`, `.venv`, etc. |
| `README.md` | ✅ Complete | Full setup, architecture, feature table, author, license. |
| `LICENSE` | ✅ Complete | MIT — Sriram Santhanam 2025. |
| `PRESENTATION.md` | ✅ Complete | Demo deck in markdown. |

---

## Architecture

```
Browser (index.html)
  │  PCM audio (WebSocket /ws/audio?sample_rate=N)
  │  socket.io ← transcript, stats, filler_detected, filler_streak, nudge
  ▼
main.py (:8000)
  ├──► Pulse STT (smallest.ai) → filler_detector.py → socket.io events
  ├──► background_agent.py (:8001) → Claude Sonnet nudge → POST /nudge → socket.io
  ├──► tts.py → POST /api/speak → WAV → Web Audio API
  └──► report.py → POST /api/report → JSON debrief
```

---

## Socket.IO Events (backend → frontend)

| Event | Payload | Trigger |
|---|---|---|
| `transcript` | `{text, is_final}` | Every STT segment |
| `stats` | `{fillerCount, pauseCount, wpm, fillerBreakdown}` | Every STT segment |
| `filler_detected` | `{words: [...]}` | When fillers found in segment |
| `filler_streak` | `{}` | 3+ fillers within 10 seconds |
| `nudge` | `{text}` | Background agent fires every 30s |

---

## REST Endpoints

| Method + Path | Body | Description |
|---|---|---|
| `POST /api/start` | `{mode, topic}` | Start session, reset state, notify agent |
| `POST /api/stop` | — | Stop session |
| `POST /api/speak` | `{text}` | Returns WAV bytes via Lightning TTS |
| `POST /api/report` | `{topic}` | Claude debrief JSON |
| `POST /nudge` | `{text}` | Internal: agent → main.py |
| `POST /chunk` (:8001) | `{text}` | Internal: main.py → agent |
| `POST /start` (:8001) | `{mode, topic}` | Internal: main.py → agent |
| `POST /stop` (:8001) | — | Internal: main.py → agent |

---

## Key Implementation Notes

- **Sample rate**: browser reports `audioContext.sampleRate` in WS query param so Pulse gets the correct rate. Do not hardcode 16000 — browsers ignore it.
- **Filler detection fallback**: if Pulse returns text (no word timestamps), `main.py` synthesizes dummy word objects so filler detection still runs.
- **smallestai package**: the PyPI package is `smallestai` not `smallest`. Import: `from smallestai import WavesClient`.
- **`so` is a single-word filler**: it lives in `FILLER_WORDS`, not `FILLER_PHRASES` (phrase list is two-word only, currently just "you know").
- **Streak detection**: uses `time.time()` wall clock, not STT timestamps — works even with dummy word objects.

---

## How to Run

```bash
# Terminal 1 — main server
uvicorn main:app --reload

# Terminal 2 — background agent
python background_agent.py

# Browser
open http://localhost:8000
```

---

## Current Status

All features implemented and running. Known open items:

- Filler detection depends on Pulse STT returning words — check server logs (`[pulse] words: [...]`) to verify what Pulse sends for sounds like "ahh"/"umm".
- `@app.on_event("startup")` in `background_agent.py` is deprecated in newer FastAPI — harmless for hackathon, fix post-event.
- `asyncio.get_event_loop()` in `tts.py` warns on Python 3.10+ — non-breaking.
- Session persistence (across sessions) parked as post-hackathon enhancement.

---

## Parked Enhancements (post-hackathon)

- Session history + progress tracking across multiple sessions
- Eye contact prompts (webcam integration)
- Confidence scoring from audio amplitude envelope
- Vocabulary richness / type-token ratio
- Time limit warnings
- Export session transcript + report as PDF
