# Speakero — Reliability Hardening

**Date:** 2026-06-10
**Status:** Approved (from the 2026-06-10 enhancement review; user approved the
"reliability trio + housekeeping" recommendation)
**Scope:** Make a paying user's practice session survive the three most likely
failures: a flaky debrief call, a network blip, and a TTS outage. Plus repo
housekeeping from the CLAUDE.md cloud-refactor checklist.

## Why (paying-customer problems)

- "I practiced 15 minutes and got nothing" — one malformed Claude response or a
  2-second wifi hiccup currently destroys the whole session.
- "The buttons are broken" — TTS failures are swallowed into console.warn, so a
  provider outage looks like a frontend bug (exactly what happened during the
  smallest.ai Lightning outage on 2026-06-10).

## 1. Debrief never comes back empty

**`report.py`:** if Claude's response fails `json.loads`, retry the API call once.
If the retry also fails (parse or API error), raise as today.

**`main.py` `/api/report`:** catch `generate_report` failures and return HTTP 200
with a **degraded report** built from data we already have (no Claude needed):

```json
{
  "degraded": true,
  "summary": "AI analysis is temporarily unavailable. Your session stats and replay are below.",
  "topic_identified": "",
  "strengths": [], "improvements": [], "content_feedback": [],
  "filler_breakdown": {...from stats...},
  "transcript": "...full transcript...",
  "replay": {...get_replay_windows()...}
}
```

Key insight: the replay windows and stats are computed locally, so the "Your
Moments" audio replay still works even when Claude is down. The transcript is
included so the user can keep their words.

**Frontend:** when `data.degraded`, style the summary as a warning notice,
suppress auto-TTS of spoken feedback (it would speak the error notice), and
render everything that exists (stats, replay, transcript).

## 2. A network blip does not kill the session

Today `disconnect` immediately pops `SESSIONS[sid]` and cancels the coaching
task; socket.io reconnects with a NEW sid, so the old state is unreachable.

**Server (`main.py`):**
- `SessionState` gains `room: str` (current sid for emissions) and
  `cleanup_task`. `coaching_loop` emits to `sess.room` instead of a captured sid.
- `disconnect`: instead of deleting, schedule a cleanup task that fires after
  `GRACE_SECONDS = 90`; only then cancel coaching and pop the session.
- New socket event `resume` with `{"old_sid": ...}`: if the old session is still
  in `SESSIONS`, re-key it to the new sid, set `sess.room = new_sid`, cancel the
  pending cleanup, discard the fresh SessionState created on connect, and ack
  `{"resumed": true}`. Otherwise ack `{"resumed": false}`.

**Frontend (`static/index.html`):**
- Track `lastSid`. On socket `connect`, if `lastSid` differs from the new
  `socket.id`, emit `resume` with the old sid; update `lastSid`.
- Audio WS: `onclose` currently says "Reconnecting…" but never reconnects. If
  `sessionActive`, retry opening the audio WS (with the current `socket.id`)
  after 1s. The mic capture graph stays alive; the `onaudioprocess` guard
  already no-ops while the WS is closed, so streaming resumes automatically.
  The client-side PCM buffer is unaffected (page never reloaded).
- If resume is refused (grace expired) during an active session, show a toast:
  stats restarted, keep talking.

Out of scope: surviving a page reload (that is the persistence roadmap item).

## 3. TTS failures are visible

- Add a small toast component (`#toast` + `showToast(msg)`, auto-hide ~4s,
  rate-limited so repeated failures don't spam).
- `playNudgeAudio`: on fetch failure, non-200, or decode error, show
  "Voice playback is unavailable right now. Text feedback still works."

## 4. Housekeeping (CLAUDE.md cloud-refactor item 1 + strays)

- Delete `background_agent.py` (its logic lives in `main.py`'s per-session
  coaching loop since the multi-tenant refactor).
- Purge `background_agent` / `BACKGROUND_AGENT_URL` / port-8001 references from
  `README.md` and `HANDOFF.md`; fix the "How to run" in `CLAUDE.md` (single
  process, no Terminal 2). NOTE: `README.md` has uncommitted user edits in the
  working tree — inspect the diff first and preserve those edits.
- `.gitignore`: add `.playwright-mcp/`.
- `.env.example`: drop stale `MAIN_SERVER_URL` / `BACKGROUND_AGENT_URL` lines.
  (The user's `.env` is theirs to clean; do not touch.)

## Deferred (documented so nothing is lost)

- **Filler-metric quality:** `FILLER_WORDS` unconditionally counts "so",
  "right", "well", "just", "actually", "literally" — inflating the core metric.
  Recommendation when picked up: tier the list (hard fillers um/uh/like always
  count; soft fillers only when clustered/repeated). Changes the product metric,
  so it needs its own design pass. Also: `"ah"` is duplicated in the set; WPM
  double-counts phrase fillers.
- **Security → fold into the auth/billing spec:** bind sid→authenticated user
  (today any client knowing a sid can start/stop/report that session), and gate
  `/api/speak` to active sessions + rate-limit (it is currently an open TTS
  proxy that burns credits once deployed).
- **PCM memory cap** (~11 MB/min in-browser): revisit before long-session
  customers.
- **Two-tab isolation smoke test** (cloud-refactor item 7): manual browser
  checklist item; automation needs a browser driver.

## Testing

- `report.py` retry: mock the Anthropic client to return bad JSON then good;
  assert one retry and a parsed result. Bad twice → raises.
- `/api/report` degraded: monkeypatch `generate_report` to raise; assert 200,
  `degraded: true`, stats + replay + transcript present.
- Disconnect grace: call the `disconnect` handler; assert the session is still
  in `SESSIONS` and a cleanup task is scheduled. Call `resume` from a new sid;
  assert re-key, room update, cleanup cancelled. Expired/unknown old_sid →
  `resumed: false`.
- Frontend: node `--check` syntax gate + DOM-id wiring check (established in
  this repo); manual toast/reconnect verification.
