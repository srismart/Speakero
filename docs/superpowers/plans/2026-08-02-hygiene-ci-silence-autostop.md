# Hygiene, CI, Healthz, Consent, Silence Auto-Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the small, zero-decision hardening items as two PRs: (A) repo hygiene + CI + /healthz + CORS env + consent copy, (B) silence auto-stop watchdog.

**Architecture:** No structural changes. PR A adds a GitHub Actions workflow, one endpoint, one env-driven config parse, static copy, and gitignore entries. PR B adds a per-session watchdog task mirroring the existing limit_task pattern in main.py.

**Tech Stack:** FastAPI, python-socketio, pytest (fastapi.testclient), GitHub Actions.

**Context notes for the implementer:**
- Repo: github.com/srismart/Speakero. Base all branches on origin/main.
- Tests run with `python -m pytest -q` from repo root (conftest.py at root).
- Prompt caching was cut from scope: Speakero's static prompt blocks (~200-900 tokens) are below Anthropic's minimum cacheable prefix (4096 tokens Haiku 4.5, 2048 Sonnet 4.6), so cache_control would silently never engage. Task A6 documents this in the assessment doc.
- No em-dashes in any user-facing copy.

---

## PR A — branch `chore/hygiene-ci` off origin/main

### Task A1: Gitignore + AGENTS.md

**Files:**
- Modify: `.gitignore`
- Commit (already exists, untracked): `AGENTS.md`

- [ ] **Step 1: Create branch**

```bash
git fetch origin && git checkout -b chore/hygiene-ci origin/main
```

- [ ] **Step 2: Append to .gitignore**

Append these lines to `.gitignore`:

```
Raw Dumps/
.claude/launch.json
.pytest_cache/
```

- [ ] **Step 3: Verify clean status**

Run: `git status --porcelain`
Expected: only `.gitignore` modified and `AGENTS.md` untracked; `Raw Dumps/` and `.claude/launch.json` no longer listed.

- [ ] **Step 4: Commit**

```bash
git add .gitignore AGENTS.md
git commit -m "chore: track AGENTS.md, ignore Raw Dumps and local launch config"
```

### Task A2: /healthz endpoint

**Files:**
- Modify: `main.py` (after the `/api/config` route, ~line 235)
- Test: `tests/test_health.py` (create)

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

import main


def test_healthz_returns_ok():
    client = TestClient(main.fastapi_app)
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert isinstance(body["sessions"], int)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_health.py -q`
Expected: FAIL (404)

- [ ] **Step 3: Implement** (in `main.py`, after the `api_config` route)

```python
@fastapi_app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "ok", "sessions": len(SESSIONS)})
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_health.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_health.py
git commit -m "feat: /healthz endpoint for deploy health checks"
```

### Task A3: CORS origins from env

**Files:**
- Modify: `main.py:32` (the `sio = socketio.AsyncServer(...)` line)
- Test: `tests/test_health.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_health.py`)

```python
def test_parse_allowed_origins():
    assert main._parse_allowed_origins("*") == "*"
    assert main._parse_allowed_origins("") == "*"
    assert main._parse_allowed_origins("https://a.com") == ["https://a.com"]
    assert main._parse_allowed_origins("https://a.com, https://b.com") == [
        "https://a.com", "https://b.com",
    ]
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_health.py -q` → AttributeError

- [ ] **Step 3: Implement** (in `main.py`, replace line 32)

```python
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
```

- [ ] **Step 4: Run full suite** — `python -m pytest -q` → all pass

- [ ] **Step 5: Add `ALLOWED_ORIGINS` to `.env.example`** with comment line:

```
# Comma-separated socket.io CORS allowlist; * (default) allows all origins (dev)
ALLOWED_ORIGINS=*
```

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_health.py .env.example
git commit -m "feat: ALLOWED_ORIGINS env var for socket.io CORS"
```

### Task A4: Consent copy

**Files:**
- Modify: `static/index.html` (control bar closes at line ~1255; insert after `</div>` of `.control-bar`)
- Test: `tests/test_frontend_wiring.py` (append; match existing test style in that file)

- [ ] **Step 1: Write the failing test** (append to `tests/test_frontend_wiring.py`)

```python
def test_consent_note_present():
    html = open("static/index.html", encoding="utf-8").read()
    assert "transcription only" in html
    assert "never stored" in html
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_frontend_wiring.py -q`

- [ ] **Step 3: Implement.** Insert after the `.control-bar` closing `</div>` (before `<!-- Stat tiles -->`):

```html
<p class="consent-note">Mic audio is streamed for live transcription only and never stored. Only the text transcript stays with your session.</p>
```

Add CSS near the other component styles (find `.control-bar` CSS block, add after it):

```css
.consent-note {
  font-size: 0.72rem;
  color: var(--text-muted, #888);
  text-align: center;
  margin: 6px 0 0;
}
```

Use the file's existing muted-text token if one exists (search for `--text-muted` or similar oklch token; use whatever the status text uses).

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_frontend_wiring.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add static/index.html tests/test_frontend_wiring.py
git commit -m "feat: recording-consent note under session controls"
```

### Task A5: GitHub Actions CI + README badge

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md` (badge at top)

- [ ] **Step 1: Create workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt pytest
      - run: python -m pytest -q
```

- [ ] **Step 2: Add badge** as the first line under the H1 in `README.md`:

```markdown
![CI](https://github.com/srismart/Speakero/actions/workflows/ci.yml/badge.svg)
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml README.md
git commit -m "ci: run pytest on every push and PR"
```

### Task A6: Correct prompt-caching claim in assessment doc

**Files:**
- Modify: `docs/PRODUCT-ASSESSMENT-2026-08.md` (Phase 2 bullet and item 6 references)

- [ ] **Step 1: Replace the "Anthropic prompt caching" bullet** in Phase 2 with:

```markdown
- Prompt caching was evaluated and cut: Anthropic's minimum cacheable prefix is 4096 tokens
  on Haiku 4.5 and 2048 on Sonnet 4.6, and Speakero's static prompt blocks are a few hundred
  tokens, so cache_control would silently never engage. The deterministic nudge layer is the
  cost/latency lever instead.
```

- [ ] **Step 2: Commit**

```bash
git add docs/PRODUCT-ASSESSMENT-2026-08.md
git commit -m "docs: prompt caching not viable below model prefix minimums"
```

### Task A7: Open PR A, verify CI, merge

- [ ] **Step 1:** `python -m pytest -q` → full suite green locally
- [ ] **Step 2:** `git push -u origin chore/hygiene-ci`
- [ ] **Step 3:** Open PR with `gh pr create` (title: "Hygiene: CI, healthz, CORS env, consent copy"; body lists the six commits; end body with the Claude Code attribution line)
- [ ] **Step 4:** Wait for the CI workflow to pass on the PR (`gh pr checks --watch`)
- [ ] **Step 5:** Request code review per superpowers:requesting-code-review; fix findings
- [ ] **Step 6:** `gh pr merge --squash --delete-branch` (user pre-authorized self-merge when green)

---

## PR B — branch `feat/silence-auto-stop` off origin/main (after PR A merges)

### Task B1: Silence tracking on SessionState

**Files:**
- Modify: `main.py` (SessionState class ~line 35, `_handle_pulse_message` ~line 494)
- Test: `tests/test_silence_autostop.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
import time

from fastapi.testclient import TestClient

import main


def test_start_initializes_last_final_at():
    sess = main.SessionState()
    sess.start()
    assert sess.last_final_at is not None
    assert abs(sess.last_final_at - time.time()) < 2


def test_silence_exceeded_logic():
    sess = main.SessionState()
    sess.start()
    now = sess.last_final_at
    assert main._silence_exceeded(sess, now + 10, timeout=180) is False
    assert main._silence_exceeded(sess, now + 181, timeout=180) is True
    sess.stop()
    assert main._silence_exceeded(sess, now + 181, timeout=180) is False


def test_silence_disabled_when_timeout_zero():
    sess = main.SessionState()
    sess.start()
    assert main._silence_exceeded(sess, sess.last_final_at + 9999, timeout=0) is False
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_silence_autostop.py -q`

- [ ] **Step 3: Implement.**

In `SessionState.__init__`: add `self.last_final_at: float | None = None` and `self.silence_task: asyncio.Task | None = None`.

In `SessionState.start()`: add `self.last_final_at = time.time()`.

Module-level, near `GRACE_SECONDS`:

```python
SILENCE_TIMEOUT_SECONDS = float(os.getenv("SILENCE_TIMEOUT_SECONDS", "180"))  # 0 disables


def _silence_exceeded(sess: "SessionState", now: float, timeout: float) -> bool:
    """True when an active session has heard no final transcript for `timeout`s.
    A dead mic still streams audio, so this is the only spend guard for
    abandoned tabs below the tier time cap."""
    if not sess.active or timeout <= 0:
        return False
    last = sess.last_final_at or sess.start_time or now
    return (now - last) > timeout
```

In `_handle_pulse_message`, in BOTH final-segment branches (the `if is_final and text_from_words.strip():` block and the `elif transcript_text:` / `if is_final:` block), add before appending to the buffer:

```python
sess.last_final_at = time.time()
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_silence_autostop.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_silence_autostop.py
git commit -m "feat: track last final transcript time per session"
```

### Task B2: Watchdog task + frontend handling

**Files:**
- Modify: `main.py` (`/api/start` ~line 350, `/api/stop`, disconnect `_cleanup`)
- Modify: `static/index.html` (socket event switch ~line 1729)
- Test: `tests/test_silence_autostop.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_start_spawns_and_stop_cancels_silence_task(monkeypatch):
    import limits
    limits.reset_cache_for_tests()
    monkeypatch.setenv("LIMIT_ANON_SESSIONS_PER_DAY", "unlimited")
    sess = main.SessionState()
    sess.room = "s-silence"
    main.SESSIONS["s-silence"] = sess
    client = TestClient(main.fastapi_app)
    res = client.post("/api/start", json={"sid": "s-silence", "mode": "individual"})
    assert res.status_code == 200
    assert sess.silence_task is not None
    client.post("/api/stop", json={"sid": "s-silence"})
    assert sess.silence_task is None
    main.SESSIONS.pop("s-silence", None)
    limits.reset_cache_for_tests()
```

(Adapt the limits bypass to match how existing passing /api/start tests in `tests/test_guardrail_endpoints.py` neutralize the anon cap; reuse their pattern verbatim if it differs.)

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement.** In `main.py` add near `coaching_loop`:

```python
SILENCE_CHECK_INTERVAL_SECONDS = 15


async def _silence_watchdog(sess: SessionState):
    while sess.active:
        await asyncio.sleep(SILENCE_CHECK_INTERVAL_SECONDS)
        if _silence_exceeded(sess, time.time(), SILENCE_TIMEOUT_SECONDS):
            sess.stop()
            if sess.coaching_task and not sess.coaching_task.done():
                sess.coaching_task.cancel()
            if sess.limit_task and not sess.limit_task.done():
                sess.limit_task.cancel()
            _log_session_usage(sess, "silence")
            await sio.emit("event", {"type": "auto_stopped", "reason": "silence"}, room=sess.room)
            return
```

In `/api/start`, after the `limit_task` setup: cancel any prior `sess.silence_task` (same pattern as `limit_task`), then `sess.silence_task = asyncio.create_task(_silence_watchdog(sess))`.

In `/api/stop`, alongside limit_task cancellation:

```python
    if sess.silence_task and not sess.silence_task.done():
        sess.silence_task.cancel()
    sess.silence_task = None
```

In the disconnect `_cleanup()` closure, cancel `gone.silence_task` the same way `limit_task` is cancelled. Also cancel it inside `_limit_stop` when the limit fires.

- [ ] **Step 4:** In `static/index.html`, add a case to the socket event switch after `case 'session_limit'`:

```javascript
        case 'auto_stopped': {
          sessionActive = false;
          stopAudioCapture();
          stopTimer();
          setVisualizerActive(false);
          document.getElementById('btnStart').disabled = false;
          document.getElementById('btnStart').classList.add('pulsing');
          document.getElementById('btnStop').disabled = true;
          setStatus('Ended: no speech detected');
          showToast('Session ended after 3 minutes of silence. Your debrief is still available.');
          break;
        }
```

Add a wiring test (append to `tests/test_silence_autostop.py`):

```python
def test_frontend_handles_auto_stopped_event():
    html = open("static/index.html", encoding="utf-8").read()
    assert "case 'auto_stopped'" in html
```

- [ ] **Step 5: Run full suite** — `python -m pytest -q` → all pass

- [ ] **Step 6: Add `SILENCE_TIMEOUT_SECONDS=180` to `.env.example`** with a comment (`# Auto-stop after this many seconds without speech; 0 disables`).

- [ ] **Step 7: Commit**

```bash
git add main.py static/index.html tests/test_silence_autostop.py .env.example
git commit -m "feat: silence auto-stop watchdog ends sessions after 3 min without speech"
```

### Task B3: Open PR B, verify CI, merge

- [ ] Same flow as Task A7. Title: "Silence auto-stop: end sessions after 3 minutes without speech". Merge when CI green and review findings addressed.

---

## Self-review notes

- Spec coverage: items 1 (CI), 2 (healthz), 3 (hygiene/CORS), 4 (consent) → PR A; item 5 (silence auto-stop) → PR B; item 6 (caching) → cut with documented rationale (Task A6). Items 7-10 are separate plans.
- `_silence_exceeded` guards `active` and `timeout<=0`; watchdog exits when session stops naturally because the `while sess.active` recheck fails after sleep.
- Watchdog cancellation added at every place limit_task is cancelled (start-restart, stop, limit fire, disconnect cleanup).
