# Speakero — Demo Presentation
### Voice AI HackSprint 2.0 · March 14, 2025 · San Francisco

---

## Slide 1 — The Problem

**Most people don't realise how they actually sound when they speak.**

- Filler words (um, uh, like, basically) erode credibility
- Uneven pace — too fast or too slow — loses the audience
- Long pauses signal uncertainty
- No feedback loop: you finish a talk, you never know what went wrong

> Toastmasters has a human "Ah Counter." We built an AI one — that also coaches you in real time.

---

## Slide 2 — What is Speakero?

**A real-time AI speaking coach that listens, detects, nudges, and debriefs.**

- Runs in your browser — no app install
- Works for talks, interviews, pitches, panel discussions
- Gives you feedback *during* the speech, not just after
- Speaks coaching nudges aloud so you don't break eye contact

---

## Slide 3 — Live Demo Flow

**Show this sequence:**

1. Open http://localhost:8000
2. Select mode: **Individual Talk** → topic: *"The future of voice AI"*
3. Click **Start Session** (mic button)
4. Speak for ~60 seconds — deliberately use filler words
5. Point out:
   - Filler counter flashing red + beep
   - Streak alert sliding up after 3 quick fillers
   - Live transcript with fillers highlighted inline
   - WPM sparkline updating
   - Mic energy bar showing volume
6. Wait for the 30-second nudge (or fast-forward by speaking fast)
7. Click **End & Debrief**
8. Walk through the debrief:
   - Spoken feedback plays automatically
   - Strengths, improvements, content feedback
   - Filler breakdown chips
   - Highlight moment
   - "Hear Example" — plays the improved delivery

---

## Slide 4 — How It Works

```
Your voice
    │
    ▼
smallest.ai Pulse STT ──► word timestamps
    │
    ▼
Filler Detector (Python)
    ├─ um / uh / like / so / basically...
    ├─ Pause gaps > 2 seconds
    ├─ Streak: 3 fillers in 10s
    └─ WPM rolling average
    │
    ▼
Claude Sonnet (every 30s)
    └─ "Slow down — you said 'like' 5 times in the last 30 seconds."
    │
    ▼
smallest.ai Lightning TTS
    └─ Speaks the nudge aloud in < 300ms
```

---

## Slide 5 — Tech Stack

| What | How |
|---|---|
| Speech-to-text | **smallest.ai Pulse** — streaming WebSocket, word-level timestamps |
| Text-to-speech | **smallest.ai Lightning** — sub-300ms latency TTS |
| AI coaching | **Claude Sonnet** (Anthropic) — mode-aware nudges + full debrief |
| Backend | Python, FastAPI, python-socketio |
| Frontend | Vanilla HTML/JS, Web Audio API, Socket.IO |
| Real-time bridge | WebSocket audio → Pulse → socket.io → browser |

---

## Slide 6 — Session Modes

| Mode | Optimised for |
|---|---|
| **Individual Talk** | Narrative flow, transitions, strong conclusion |
| **Panel** | Conciseness, direct answers, not over-talking |
| **Pitch** | Hook, value proposition, persuasiveness, CTA |

Claude's nudges and debrief adapt to the selected mode.

---

## Slide 7 — The Debrief

After your session, Claude generates:

- **Strengths** — what you did well
- **Areas to Improve** — top 3 priority actions
- **Content Feedback** — was the speech relevant, structured, clear?
- **Filler Breakdown** — which words, how many times
- **Repetition Flags** — phrases you overused
- **Jargon Flags** — terms that may confuse a general audience
- **Sentence Completion Rate** — did you finish your thoughts?
- **Highlight Moment** — your best delivery window, called out
- **Spoken Feedback** — the single most important point, spoken aloud
- **Example Re-delivery** — Claude rewrites a snippet and speaks it

---

## Slide 8 — What's Next

- Session history + progress tracking
- Webcam: eye contact and gesture prompts
- Confidence scoring from audio amplitude
- Time limit mode (conference talk countdown)
- Export transcript + report as PDF
- Mobile mic support

---

## Slide 9 — Try It

```
git clone https://github.com/srismart/Speakero
pip install -r requirements.txt
cp .env.example .env   # add your keys
uvicorn main:app --reload
python background_agent.py
```

**Open http://localhost:8000**

---

*Built by Sriram Santhanam at Voice AI HackSprint 2.0 · San Francisco · March 14, 2025*
