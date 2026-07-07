# Speakero — AI Speaking Coach

## What this is

Real-time AI speaking coach. Detects filler words live, delivers
Claude-powered voice nudges every 30s, generates full debrief at end.
Runner-up at Voice AI HackSprint 2.0, SF. Now being developed into
a monetizable product.

## Stack

- Backend: Python, FastAPI, python-socketio, uvicorn
- STT: smallest.ai Pulse (OPEN TO SWAP — needs stt_provider.py abstraction)
- TTS: smallest.ai Lightning (OPEN TO SWAP — needs tts_provider.py abstraction)
- AI: Claude Sonnet via Anthropic SDK
- Frontend: Vanilla HTML/JS, single file at static/index.html

## How to run

uvicorn main:app --port 8080

# Browser: http://localhost:8080

## Key implementation notes

- Pulse STT URL: wss://api.smallest.ai/waves/v1/pulse/get_text (NOT /v1/pulse/stream)
- Process words on is_final=True only — interim segments double-count
- Port 8080 (not 8000 — zombie socket risk)
- TTS AudioContext must be created on user gesture, reused after

## Current priorities (in order)

1. stt_provider.py + tts_provider.py abstraction layer
2. User auth (Clerk)
3. Session persistence (Supabase)
4. Usage metering + Stripe billing
5. Session history UI + PDF export

## Constraints

- Python-native only. No JS frameworks.
- No em-dashes in written content.
- Always tie feature suggestions to a paying customer problem.

## Cloud refactor (do before auth/billing)

Goal: make Speakero multi-tenant and deployable as a single container.

1. (done 2026-06-10) Delete background_agent.py — its logic already lives in main.py's
   coaching_loop. Remove BACKGROUND_AGENT_URL references and the :8001 endpoints from
   README/HANDOFF.
2. Replace global `session` singleton with SESSIONS: dict[str, SessionState] keyed
   by socket.io sid. Update SessionState to be instantiated per connection.
3. On socket connect, join a room named after sid. Change every sio.emit to
   target room=sid instead of broadcasting. Map the audio WS to the right session.
4. Replace the single global coaching_loop + \_transcript_buffer with one coaching
   task per session, spawned on /api/start, cancelled on /api/stop and on disconnect.
   Each task emits nudges to its own room.
5. Update /api/report and /api/speak to resolve the caller's SESSIONS[sid].
6. Add a Dockerfile (python slim, uvicorn, read PORT env var, EXPOSE 8080) and a
   render.yaml / fly.toml. Move secrets to platform env vars, not .env.
7. Smoke test: two browser tabs, two different topics, confirm stats, transcripts,
   nudges, and debriefs stay fully isolated per tab.
