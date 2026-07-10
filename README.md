# Speakero — Real-time AI Speaking Coach

> Built at **Voice AI HackSprint 2.0** · March 14, 2025 · San Francisco, CA
> https://lu.ma/ikzcmqld

**Speakero** is a real-time AI speaking coach that listens as you speak, catches filler words the moment they happen, measures your pace and pauses, delivers live coaching nudges via voice, and generates a full Claude-powered debrief at the end of your session.

---

## Demo

> 📹 _Demo video coming soon_

---

## Features

| Feature | Description |
|---|---|
| **Live STT** | smallest.ai Pulse streams word-level transcripts with timestamps |
| **Filler detection** | Catches um, uh, ah, like, so, basically, right, you know — with beep + flash |
| **Filler streak alert** | Fires when 3+ fillers occur in 10 seconds |
| **Real-time stats** | Fillers, pauses (>2s gaps), WPM sparkline — all live |
| **Mic energy bar** | Amplitude visualization with trailing-off detection |
| **AI coaching nudges** | Claude Sonnet generates a short nudge every 30s, spoken aloud via TTS |
| **Session modes** | Individual Talk, Panel, Pitch — nudges adapt to each context |
| **Topic tracking** | Set a topic or let Claude self-identify; watches for topic drift |
| **Lightning TTS** | smallest.ai Lightning speaks every nudge and feedback point aloud |
| **AI debrief report** | Full end-of-session analysis: strengths, improvements, content feedback, filler breakdown, repetition flags, jargon flags, sentence completion, highlight moment |
| **Example re-delivery** | Claude rewrites a speech snippet showing better delivery, then speaks it |

---

## Architecture

```
Browser (index.html)
  │
  │  PCM audio chunks (WebSocket /ws/audio)
  │  socket.io events ← transcript, stats, filler_detected, filler_streak, nudge
  │
  ▼
main.py  (:8080)  FastAPI + python-socketio
  │
  ├──► smallest.ai Pulse STT  (cloud WebSocket, wss://api.smallest.ai/waves/v1/pulse/get_text)
  │      └─ word timestamps ──► filler_detector.py
  │              ├─ filler words / phrases detected
  │              ├─ pause gaps (>2s)
  │              ├─ filler streak (3 in 10s)
  │              ├─ WPM calculation
  │              └─ best-window tracking (for highlight reel)
  │
  ├──► coaching loop (in-process, one task per session)
  │      └─ accumulates 30s of transcript → Claude Sonnet (mode-aware prompt)
  │         → socket.io nudge → browser → TTS
  │
  ├──► tts.py
  │      └─ POST /api/speak → smallest.ai Lightning TTS → WAV bytes → Web Audio API
  │
  └──► report.py
         └─ POST /api/report → Claude Sonnet → structured JSON debrief → browser
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, python-socketio, uvicorn |
| Speech-to-Text | [smallest.ai Pulse](https://smallest.ai) — streaming, word-level timestamps |
| Text-to-Speech | [smallest.ai Lightning](https://smallest.ai) — ultra-low-latency voice |
| AI coaching | [Claude Sonnet](https://anthropic.com) (Anthropic) — nudges + debrief |
| Frontend | Vanilla HTML/CSS/JS, Socket.IO CDN, Web Audio API |
| Real-time | WebSockets + Socket.IO |

---

## Setup

```bash
# 1. Clone
git clone https://github.com/srismart/Speakero.git
cd Speakero

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Fill in SMALLEST_API_KEY and ANTHROPIC_API_KEY

# 4. Start the server
uvicorn main:app --port 8080

# Open http://localhost:8080
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `SMALLEST_API_KEY` | [smallest.ai](https://smallest.ai) API key — used for Pulse STT and Lightning TTS |
| `ANTHROPIC_API_KEY` | [Anthropic](https://console.anthropic.com) API key — used for Claude Sonnet |

---

## Session Modes

| Mode | Nudge focus |
|---|---|
| **Individual Talk** | Narrative flow, transitions, conclusion strength |
| **Panel** | Conciseness, direct answers, not over-talking |
| **Pitch** | Hook quality, persuasiveness, call-to-action |

---

## Author

**Sriram Santhanam**

Built for [Voice AI HackSprint 2.0](https://lu.ma/ikzcmqld) — March 14, 2025, San Francisco, CA

---

## License

MIT — see [LICENSE](LICENSE)
