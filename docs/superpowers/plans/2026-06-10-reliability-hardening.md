# Reliability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A practice session survives a flaky debrief call, a network blip, and a TTS outage; plus repo housekeeping (delete the superseded background agent and stale docs).

**Architecture:** Retry-once + degraded-fallback in the report path (stats and replay are computed locally, so they survive Claude failures). Disconnect handling gains a 90s grace period with a socket `resume` event that re-keys the old session to the new sid. The frontend gets a toast component for TTS failures, audio-WS auto-reconnect, and degraded-debrief rendering.

**Tech Stack:** Python (FastAPI, python-socketio, pytest with `asyncio.run` in sync tests — pytest-asyncio is NOT installed), vanilla JS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-10-reliability-hardening-design.md`

---

## File Structure

- `report.py` (modify) — retry the Claude call once on unparseable JSON.
- `main.py` (modify) — degraded `/api/report` fallback; `GRACE_SECONDS`; `SessionState.room`/`cleanup_task`; `coaching_loop(sess)` emits to `sess.room`; grace-period `disconnect`; new `resume` socket event.
- `static/index.html` (modify) — toast component; TTS failure toasts; resume-on-reconnect; audio-WS reconnect; degraded debrief rendering.
- `tests/test_report_retry.py` (create), `tests/test_report_replay.py` (extend), `tests/test_session_resume.py` (create).
- Housekeeping: delete `background_agent.py`; edit `README.md`, `HANDOFF.md`, `CLAUDE.md`, `.gitignore`, `.env.example`.

---

### Task 1: report.py retries once on unparseable JSON

**Files:**
- Modify: `report.py` (the `client.messages.create` → `json.loads` region)
- Test: `tests/test_report_retry.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_retry.py`:

```python
import asyncio
import json
from types import SimpleNamespace

import pytest

import report


def make_fake_anthropic(responses):
    """Fake AsyncAnthropic whose messages.create returns each response in turn."""
    calls = {"n": 0}

    class FakeMessages:
        async def create(self, **kwargs):
            calls["n"] += 1
            text = responses[min(calls["n"] - 1, len(responses) - 1)]
            return SimpleNamespace(content=[SimpleNamespace(text=text)])

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    return FakeClient, calls


def test_retries_once_on_bad_json_then_succeeds(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    FakeClient, calls = make_fake_anthropic(["this is not json", '{"summary": "ok"}'])
    monkeypatch.setattr(report.anthropic, "AsyncAnthropic", FakeClient)

    result = asyncio.run(report.generate_report("hello world", {}))

    assert result["summary"] == "ok"
    assert calls["n"] == 2


def test_raises_after_two_bad_responses(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    FakeClient, calls = make_fake_anthropic(["bad", "still bad"])
    monkeypatch.setattr(report.anthropic, "AsyncAnthropic", FakeClient)

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(report.generate_report("hello world", {}))
    assert calls["n"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_report_retry.py -q`
Expected: `test_retries_once_on_bad_json_then_succeeds` FAILS — current code raises `JSONDecodeError` on the first bad response (calls == 1, no retry).

- [ ] **Step 3: Implement the retry loop**

In `report.py`, find:

```python
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    report = json.loads(raw)
```

Replace with:

```python
    report = None
    parse_error: Exception | None = None
    for _attempt in range(2):
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            report = json.loads(raw)
            break
        except json.JSONDecodeError as e:
            parse_error = e

    if report is None:
        raise parse_error
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (15 passed: 13 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add report.py tests/test_report_retry.py
git commit -m "fix: retry the debrief Claude call once on unparseable JSON"
```

---

### Task 2: /api/report returns a degraded report instead of 500

**Files:**
- Modify: `main.py` (the `/api/report` `except` branch)
- Test: `tests/test_report_replay.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_report_replay.py`:

```python
def test_report_degrades_gracefully_when_claude_fails(monkeypatch):
    async def broken_report(*args, **kwargs):
        raise RuntimeError("claude is down")

    monkeypatch.setattr(main, "generate_report", broken_report)

    sess = main.SessionState()
    sess.start()
    sess.detector.process_words([
        {"word": "um", "start": 0.0, "end": 0.2},
        {"word": "we", "start": 0.2, "end": 0.5},
        {"word": "ship", "start": 0.5, "end": 0.9},
        {"word": "fast", "start": 0.9, "end": 1.3},
    ])
    sess.add_text("um we ship fast")
    main.SESSIONS["testsid3"] = sess

    client = TestClient(main.fastapi_app)
    res = client.post("/api/report", json={"sid": "testsid3", "topic": "x"})

    assert res.status_code == 200
    body = res.json()
    assert body["degraded"] is True
    assert "temporarily unavailable" in body["summary"]
    assert body["filler_breakdown"] == {"um": 1}
    assert body["transcript"] == "um we ship fast"
    # replay still works — it never needed Claude
    assert body["replay"]["best"]["text"] == "we ship fast"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report_replay.py -q`
Expected: the new test FAILS with `assert 500 == 200` (current code returns 500).

- [ ] **Step 3: Implement the degraded fallback**

In `main.py`, in the `/api/report` handler, find:

```python
    except Exception as e:
        print(f"[report] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
```

Replace with:

```python
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
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (16 passed).

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_report_replay.py
git commit -m "fix: /api/report falls back to a degraded stats+replay report"
```

---

### Task 3: disconnect grace period + resume event (server)

**Files:**
- Modify: `main.py` (constant, `SessionState`, `coaching_loop`, `connect`, `disconnect`, new `resume`, `api_start` call site)
- Test: `tests/test_session_resume.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_resume.py` (handlers registered with `@sio.event` remain plain callables, so we invoke them directly; tests are sync and use `asyncio.run`):

```python
import asyncio

import main


def test_disconnect_keeps_session_during_grace():
    async def scenario():
        sess = main.SessionState()
        sess.room = "olds"
        main.SESSIONS["olds"] = sess

        await main.disconnect("olds")

        assert "olds" in main.SESSIONS          # not deleted immediately
        assert sess.cleanup_task is not None    # cleanup scheduled
        sess.cleanup_task.cancel()
        main.SESSIONS.pop("olds", None)
    asyncio.run(scenario())


def test_grace_expiry_cleans_up(monkeypatch):
    monkeypatch.setattr(main, "GRACE_SECONDS", 0.01)

    async def scenario():
        sess = main.SessionState()
        sess.room = "olds"
        main.SESSIONS["olds"] = sess

        await main.disconnect("olds")
        await asyncio.sleep(0.1)

        assert "olds" not in main.SESSIONS
    asyncio.run(scenario())


def test_resume_rekeys_session_to_new_sid():
    async def scenario():
        old = main.SessionState()
        old.room = "olds"
        main.SESSIONS["olds"] = old
        await main.disconnect("olds")

        fresh = main.SessionState()             # what connect() creates for the new sid
        fresh.room = "news"
        main.SESSIONS["news"] = fresh

        resp = await main.resume("news", {"old_sid": "olds"})

        assert resp == {"resumed": True}
        assert main.SESSIONS["news"] is old     # old state re-keyed, fresh discarded
        assert old.room == "news"               # nudges now target the new room
        assert "olds" not in main.SESSIONS
        assert old.cleanup_task is None         # pending cleanup cancelled
        main.SESSIONS.pop("news", None)
    asyncio.run(scenario())


def test_resume_unknown_or_expired_sid_is_refused():
    async def scenario():
        resp = await main.resume("news", {"old_sid": "never-existed"})
        assert resp == {"resumed": False}
    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_resume.py -q`
Expected: FAIL — `SessionState` has no `room` attribute / `main` has no `GRACE_SECONDS` / no `resume`.

- [ ] **Step 3: Add the constant and SessionState fields**

In `main.py`, after `CHUNK_INTERVAL_SECONDS = 30` add:

```python
GRACE_SECONDS = 90  # keep a disconnected session alive this long for reconnects
```

In `SessionState.__init__`, after `self.highlight_window: str = ""` add:

```python
        self.room: str = ""
        self.cleanup_task: asyncio.Task | None = None
```

- [ ] **Step 4: coaching_loop emits to sess.room**

Replace the `coaching_loop` signature and the two places it uses `sid`:

```python
async def coaching_loop(sess: SessionState):
    print(f"[agent] Coaching loop started for {sess.room}")
```

and inside the loop:

```python
            await sio.emit("event", {"type": "nudge", "text": nudge}, room=sess.room)
```

Update the call site in `api_start`:

```python
    sess.coaching_task = asyncio.create_task(coaching_loop(sess))
```

- [ ] **Step 5: connect sets room; disconnect schedules cleanup; add resume**

Replace the `connect` and `disconnect` handlers:

```python
@sio.event
async def connect(sid, environ):
    sess = SessionState()
    sess.room = sid
    SESSIONS[sid] = sess
    await sio.enter_room(sid, sid)
    print(f"[sio] Client connected: {sid}")


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
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (20 passed).

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_session_resume.py
git commit -m "feat: 90s disconnect grace period with session resume by new sid"
```

---

### Task 4: frontend — toast, resume, audio-WS reconnect, degraded debrief

**Files:**
- Modify: `static/index.html` (CSS, toast div, socket handlers, `startAudioCapture`, `playNudgeAudio`, `renderDebrief`)

All edits are exact-match replaces; verify at the end with the node/id checks in Step 7.

- [ ] **Step 1: Toast CSS + element + helper**

CSS — find:

```css
    /* ── SCROLLBAR global ────────────────────────────────────────────────────── */
```

Insert BEFORE it:

```css
    /* ── TOAST ───────────────────────────────────────────────────────────────── */
    #toast {
      position: fixed;
      bottom: 96px;
      left: 50%;
      transform: translateX(-50%) translateY(8px);
      background: var(--surface-solid);
      border: 1px solid var(--border-solid);
      color: var(--text);
      font-family: 'Inter', sans-serif;
      font-size: 0.85rem;
      padding: 10px 18px;
      border-radius: 10px;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.25s, transform 0.25s;
      z-index: 200;
      max-width: 80vw;
    }
    #toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
    #debriefSummary.degraded { color: var(--warn); }

```

HTML — find:

```html
  <!-- Fixed control bar -->
```

Insert BEFORE it:

```html
  <!-- Toast notifications -->
  <div id="toast"></div>

```

JS helper — find:

```javascript
    function setStatus(text, active = false) {
```

Insert BEFORE it:

```javascript
    let toastTimer = null;
    let lastToastAt = 0;
    function showToast(msg) {
      const now = Date.now();
      if (now - lastToastAt < 5000) return; // rate-limit repeated failures
      lastToastAt = now;
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.classList.add('show');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => t.classList.remove('show'), 4000);
    }

```

- [ ] **Step 2: TTS failures show the toast**

In `playNudgeAudio`, find:

```javascript
        if (!res.ok) { console.warn('[tts] /api/speak returned', res.status); return; }
```

Replace with:

```javascript
        if (!res.ok) {
          console.warn('[tts] /api/speak returned', res.status);
          showToast('Voice playback is unavailable right now. Text feedback still works.');
          return;
        }
```

And find:

```javascript
      } catch (e) { console.warn('[tts] Audio playback failed:', e); }
    }

    // ── Audio capture ──────────────────────────────────────────────────────────
```

Replace with:

```javascript
      } catch (e) {
        console.warn('[tts] Audio playback failed:', e);
        showToast('Voice playback is unavailable right now. Text feedback still works.');
      }
    }

    // ── Audio capture ──────────────────────────────────────────────────────────
```

- [ ] **Step 3: resume on socket reconnect**

Find:

```javascript
    // Disable Start until the socket handshake completes and we have a stable sid
    document.getElementById('btnStart').disabled = true;
    socket.on('connect', () => {
      document.getElementById('btnStart').disabled = false;
      setStatus('Ready');
    });
```

Replace with:

```javascript
    // Disable Start until the socket handshake completes and we have a stable sid
    document.getElementById('btnStart').disabled = true;
    let lastSid = null;
    socket.on('connect', () => {
      document.getElementById('btnStart').disabled = false;
      if (lastSid && lastSid !== socket.id) {
        // Reconnected: re-attach to the server-side session state
        socket.emit('resume', { old_sid: lastSid }, (resp) => {
          if (!(resp && resp.resumed) && sessionActive) {
            showToast('Connection was lost too long. Stats restarted, keep talking.');
          }
        });
        if (sessionActive) reconnectAudioWs();
      }
      lastSid = socket.id;
      setStatus(sessionActive ? 'Streaming…' : 'Ready', sessionActive);
    });
```

- [ ] **Step 4: audio WS auto-reconnect**

In `startAudioCapture`, find:

```javascript
      const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
      audioWs = new WebSocket(`${wsProto}://${location.host}/ws/audio?sample_rate=${audioContext.sampleRate}&sid=${socket.id}`);
      audioWs.binaryType = 'arraybuffer';
      audioWs.onopen = () => setStatus('Streaming…', true);
      audioWs.onclose = () => { if (sessionActive) setStatus('Reconnecting…'); };
      audioWs.onerror = (e) => console.error('[ws] Audio error:', e);
    }
```

Replace with:

```javascript
      connectAudioWs();
    }

    function connectAudioWs() {
      const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
      audioWs = new WebSocket(`${wsProto}://${location.host}/ws/audio?sample_rate=${audioContext.sampleRate}&sid=${socket.id}`);
      audioWs.binaryType = 'arraybuffer';
      audioWs.onopen = () => setStatus('Streaming…', true);
      audioWs.onclose = () => {
        if (sessionActive) {
          setStatus('Reconnecting…');
          setTimeout(reconnectAudioWs, 1000);
        }
      };
      audioWs.onerror = (e) => console.error('[ws] Audio error:', e);
    }

    function reconnectAudioWs() {
      // Mic graph stays alive; onaudioprocess no-ops while the WS is closed,
      // so reopening the WS resumes streaming with no other work.
      if (!sessionActive || !audioContext) return;
      if (audioWs && (audioWs.readyState === WebSocket.OPEN || audioWs.readyState === WebSocket.CONNECTING)) return;
      connectAudioWs();
    }
```

- [ ] **Step 5: degraded debrief rendering**

In `renderDebrief`, find:

```javascript
      document.getElementById('debriefSummary').textContent = data.summary || '';
```

Replace with:

```javascript
      const summaryEl = document.getElementById('debriefSummary');
      summaryEl.textContent = data.summary || '';
      summaryEl.classList.toggle('degraded', !!data.degraded);
```

And find:

```javascript
      // Spoken feedback — fall back to first improvement or summary if field is empty
      const spokenText = data.spoken_feedback
        || (data.improvements && data.improvements[0])
        || data.summary
        || '';
```

Replace with:

```javascript
      // Spoken feedback — fall back to first improvement or summary if field is
      // empty. Suppressed entirely for degraded reports (it would speak the
      // outage notice aloud).
      const spokenText = data.degraded ? '' : (data.spoken_feedback
        || (data.improvements && data.improvements[0])
        || data.summary
        || '');
```

(The degraded response's `transcript` field needs no rendering — the live
transcript is already on the page behind the debrief.)

- [ ] **Step 6: verify syntax and wiring**

```bash
python - <<'PY'
import re
html = open("static/index.html", encoding="utf-8").read()
open("_inline_check.js", "w", encoding="utf-8").write("\n".join(re.findall(r"<script>(.*?)</script>", html, re.S)))
defined = set(re.findall(r'id="([^"]+)"', html))
referenced = set(re.findall(r"getElementById\('([^']+)'\)", html))
missing = sorted(referenced - defined)
print("MISSING IDS:", missing if missing else "none")
assert not missing
PY
node --check _inline_check.js && rm _inline_check.js && echo "JS OK"
```

Expected: `MISSING IDS: none` and `JS OK`.

- [ ] **Step 7: smoke-serve**

```bash
python -m pytest -q
```
Expected: 20 passed. Then start `uvicorn main:app --port 8080` briefly and `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/` → `200`. Stop the server.

- [ ] **Step 8: Commit**

```bash
git add static/index.html
git commit -m "feat: TTS failure toast, session resume on reconnect, audio WS retry, degraded debrief"
```

---

### Task 5: housekeeping — delete background_agent.py and purge stale docs

**Files:**
- Delete: `background_agent.py`
- Modify: `README.md` (NOTE: has uncommitted user edits — port/URL corrections; keep them, they land in this commit), `HANDOFF.md`, `CLAUDE.md`, `.gitignore`, `.env.example`

- [ ] **Step 1: delete the file**

```bash
git rm background_agent.py
```

- [ ] **Step 2: README architecture diagram**

Find:

```
  ├──► background_agent.py  (:8001)  separate process
  │      └─ receives transcript chunks via POST /chunk
  │         accumulates 30s → Claude Sonnet (mode-aware prompt)
  │         POST /nudge → main.py → socket.io → browser → TTS
  │
```

Replace with:

```
  ├──► coaching loop (in-process, one task per session)
  │      └─ accumulates 30s of transcript → Claude Sonnet (mode-aware prompt)
  │         → socket.io nudge → browser → TTS
  │
```

- [ ] **Step 3: README run instructions**

Find:

```
# 4. Start the main server
uvicorn main:app --reload

# 5. In a second terminal, start the background agent
python background_agent.py

# Open http://localhost:8000
```

Replace with:

```
# 4. Start the server
uvicorn main:app --port 8080

# Open http://localhost:8080
```

- [ ] **Step 4: README env-var table**

Find:

```
| `MAIN_SERVER_URL` | URL of main.py, seen by background agent (default: `http://localhost:8000`) |
| `BACKGROUND_AGENT_URL` | URL of background_agent.py, seen by main.py (default: `http://localhost:8001`) |
```

Delete both lines.

- [ ] **Step 5: HANDOFF.md**

Find the table row:

```
| `background_agent.py` | ⚠️ Superseded | Original separate process on :8001. Functionality fully absorbed into `main.py`. Safe to delete. |
```

Replace with:

```
| `background_agent.py` | ✅ Deleted (2026-06-10) | Original separate process on :8001. Functionality fully absorbed into `main.py`. |
```

- [ ] **Step 6: CLAUDE.md run section**

Find:

```
# Terminal 1

uvicorn main:app --port 8080

# Terminal 2

python background_agent.py
```

Replace with:

```
uvicorn main:app --port 8080
```

Also, in the "Cloud refactor" section, find:

```
1. Delete background_agent.py — its logic already lives in main.py's coaching_loop.
```

Replace with:

```
1. (done 2026-06-10) Delete background_agent.py — its logic already lives in main.py's coaching_loop.
```

- [ ] **Step 7: .gitignore and .env.example**

Append to `.gitignore`:

```
.playwright-mcp/
```

In `.env.example`, delete these two lines:

```
MAIN_SERVER_URL=http://localhost:8000
BACKGROUND_AGENT_URL=http://localhost:8001
```

- [ ] **Step 8: verify nothing references the deleted module**

```bash
grep -rn "background_agent\|BACKGROUND_AGENT\|8001" --include="*.py" --include="*.md" . | grep -v docs/superpowers | grep -v HANDOFF.md || echo "clean"
python -m pytest -q
```

Expected: `clean` (HANDOFF keeps its historical row) and 20 passed.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "chore: delete superseded background_agent.py and purge stale docs

Also folds in earlier README corrections (port 8080, current Pulse URL)."
```

---

## Self-Review (completed during planning)

**Spec coverage:** §1 debrief resilience → Tasks 1–2 (+ frontend Step 5 in Task 4). §2 reconnect grace/resume → Task 3 (server) + Task 4 Steps 3–4 (client). §3 TTS toast → Task 4 Steps 1–2. §4 housekeeping → Task 5. Deferred items need no tasks.
**Placeholder scan:** every code step has complete code; no TBDs.
**Type consistency:** `resume` returns `{"resumed": bool}` and the client reads `resp.resumed`; `sess.room`/`cleanup_task` names match across Tasks 3–4; `showToast`/`reconnectAudioWs`/`connectAudioWs` defined before use at runtime (function declarations hoist).
**Test count arithmetic:** 13 existing + 2 (T1) + 1 (T2) + 4 (T3) = 20.
