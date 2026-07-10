# Real-Audio Best/Worst Replay (Increment 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the session debrief, let the speaker replay their own recorded audio of their strongest and roughest moments.

**Architecture:** The `FillerDetector` already records per-chunk "windows" with text and filler info. We add the audio time span (from Pulse word timestamps) to each window, expose best/worst windows with `{text, start, end}`, and return them from `/api/report`. The browser buffers the full session PCM it already captures, then slices `[start*rate, end*rate]` and plays it via the existing Web Audio context. No audio touches the server.

**Tech Stack:** Python (FastAPI, pytest), vanilla JS (Web Audio API). No new dependencies — `pytest` 9.0.2 is already installed.

**Scope note:** This is Increment 1 of the spec at `docs/superpowers/specs/2026-06-09-voice-replay-clone-design.md`. Increment 2 (cloned "after") is a separate, later plan. Build only what is below.

---

## File Structure

- `filler_detector.py` (modify) — add audio offsets to windows; `get_best_window` returns a dict; add `get_worst_window`, `get_replay_windows`.
- `main.py` (modify) — `/api/report` returns `report["replay"]`; update the `get_best_window` call site.
- `static/index.html` (modify) — buffer session PCM; render the replay A/B card; slice + play.
- `conftest.py` (create) — empty file so pytest puts the repo root on `sys.path`.
- `tests/test_filler_detector.py` (create) — unit tests for window selection + offsets.
- `tests/test_report_replay.py` (create) — endpoint test that `/api/report` includes `replay`.

---

## Task 0: Test scaffolding

**Files:**
- Create: `conftest.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create an empty root conftest so tests can import top-level modules**

Create `conftest.py` (repo root) with this exact content (a comment so the file is non-empty):

```python
# Presence of this file puts the repo root on sys.path for pytest imports.
```

- [ ] **Step 2: Create the tests package marker**

Create `tests/__init__.py` as an empty file.

- [ ] **Step 3: Verify pytest collects from the repo root**

Run: `python -m pytest -q`
Expected: `no tests ran` (exit code 5) — no tests exist yet, but collection works with no import errors.

- [ ] **Step 4: Commit**

```bash
git add conftest.py tests/__init__.py
git commit -m "test: add pytest scaffolding"
```

---

## Task 1: Window audio offsets + best window returns a dict

**Files:**
- Modify: `filler_detector.py` (the `process_words` window append, and `get_best_window`)
- Modify: `main.py:203` (the `get_best_window` call site)
- Test: `tests/test_filler_detector.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_filler_detector.py`:

```python
from filler_detector import FillerDetector


def test_best_window_returns_text_and_audio_offsets():
    d = FillerDetector()
    d.start_session()
    d.process_words([
        {"word": "the", "start": 1.0, "end": 1.2},
        {"word": "core", "start": 1.2, "end": 1.5},
        {"word": "idea", "start": 1.5, "end": 1.9},
        {"word": "matters", "start": 1.9, "end": 2.4},
    ])
    bw = d.get_best_window()
    assert bw["text"] == "the core idea matters"
    assert bw["start"] == 1.0
    assert bw["end"] == 2.4


def test_best_window_is_none_when_no_windows():
    d = FillerDetector()
    d.start_session()
    assert d.get_best_window() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_filler_detector.py -q`
Expected: FAIL — `get_best_window()` currently returns a string, so `bw["text"]` raises `TypeError` (string indices must be integers).

- [ ] **Step 3: Record audio offsets on each window**

In `filler_detector.py`, inside `process_words`, find the window append block:

```python
        # Record this chunk for highlight reel
        chunk_text = " ".join(chunk_words)
        if chunk_text.strip() or chunk_fillers:
            self._word_windows.append({
                "text": chunk_text,
                "t": chunk_t,
                "fillers": list(chunk_fillers),
                "words": list(chunk_words),
            })
```

Replace it with this (computes the span across all words in the chunk, fillers included):

```python
        # Record this chunk for highlight reel
        chunk_text = " ".join(chunk_words)
        if chunk_text.strip() or chunk_fillers:
            starts = [w.get("start", 0.0) for w in words]
            ends = [w.get("end", w.get("start", 0.0)) for w in words]
            self._word_windows.append({
                "text": chunk_text,
                "t": chunk_t,
                "fillers": list(chunk_fillers),
                "words": list(chunk_words),
                "audio_start": min(starts) if starts else 0.0,
                "audio_end": max(ends) if ends else 0.0,
            })
```

- [ ] **Step 4: Change `get_best_window` to return a dict**

In `filler_detector.py`, replace the whole `get_best_window` method:

```python
    def get_best_window(self) -> dict | None:
        """Return the best-delivery window: most words, lowest filler ratio.

        Returns {"text", "start", "end"} or None if no windows recorded.
        """
        if not self._word_windows:
            return None

        def score(w: dict) -> float:
            word_count = len(w["words"])
            filler_count = len(w["fillers"])
            if word_count == 0:
                return -1.0
            filler_ratio = filler_count / (word_count + filler_count)
            return word_count * (1.0 - filler_ratio)

        best = max(self._word_windows, key=score)
        return {"text": best["text"], "start": best["audio_start"], "end": best["audio_end"]}
```

- [ ] **Step 5: Update the call site in `main.py`**

In `main.py`, the `/api/report` handler currently has at line 203:

```python
    highlight_window = sess.detector.get_best_window()
```

Replace that single line with:

```python
    best_window = sess.detector.get_best_window()
    highlight_window = best_window["text"] if best_window else ""
```

(`generate_report` expects `highlight_window` to be a string and calls `.strip()` on it, so we pass the text.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_filler_detector.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add filler_detector.py main.py tests/test_filler_detector.py
git commit -m "feat: record audio offsets on windows; best window returns dict"
```

---

## Task 2: Worst window by filler density

**Files:**
- Modify: `filler_detector.py` (add `get_worst_window`)
- Test: `tests/test_filler_detector.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filler_detector.py`:

```python
def test_worst_window_picks_highest_filler_density():
    d = FillerDetector()
    d.start_session()
    # clean window (no fillers)
    d.process_words([
        {"word": "we", "start": 0.0, "end": 0.2},
        {"word": "build", "start": 0.2, "end": 0.6},
        {"word": "products", "start": 0.6, "end": 1.1},
        {"word": "daily", "start": 1.1, "end": 1.5},
    ])
    # filler-heavy window: um, like, basically are fillers; "stuff" is the only word
    d.process_words([
        {"word": "um", "start": 2.0, "end": 2.2},
        {"word": "like", "start": 2.2, "end": 2.4},
        {"word": "basically", "start": 2.4, "end": 2.8},
        {"word": "stuff", "start": 2.8, "end": 3.1},
    ])
    ww = d.get_worst_window()
    assert ww["start"] == 2.0
    assert ww["end"] == 3.1
    assert ww["text"] == "stuff"


def test_worst_window_ignores_trivial_and_clean_windows():
    d = FillerDetector()
    d.start_session()
    # a clean window with no fillers -> not a valid "worst"
    d.process_words([
        {"word": "hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.0},
        {"word": "again", "start": 1.0, "end": 1.4},
        {"word": "now", "start": 1.4, "end": 1.8},
    ])
    assert d.get_worst_window() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_filler_detector.py::test_worst_window_picks_highest_filler_density -q`
Expected: FAIL — `AttributeError: 'FillerDetector' object has no attribute 'get_worst_window'`.

- [ ] **Step 3: Implement `get_worst_window`**

In `filler_detector.py`, add this method directly after `get_best_window`:

```python
    def get_worst_window(self) -> dict | None:
        """Return the roughest window: highest filler density.

        Requires at least 4 tokens (words + fillers) so trivial stumbles do not
        win, and at least one filler to be a meaningful "worst". Returns
        {"text", "start", "end"} or None.
        """
        eligible = [
            w for w in self._word_windows
            if (len(w["words"]) + len(w["fillers"])) >= 4 and len(w["fillers"]) > 0
        ]
        if not eligible:
            return None

        def density(w: dict) -> float:
            total = len(w["words"]) + len(w["fillers"])
            return len(w["fillers"]) / total if total else 0.0

        worst = max(eligible, key=density)
        return {"text": worst["text"], "start": worst["audio_start"], "end": worst["audio_end"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_filler_detector.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add filler_detector.py tests/test_filler_detector.py
git commit -m "feat: add get_worst_window by filler density"
```

---

## Task 3: Combine into get_replay_windows (dedup + timestamp gate)

**Files:**
- Modify: `filler_detector.py` (add `get_replay_windows`)
- Test: `tests/test_filler_detector.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filler_detector.py`:

```python
def test_replay_windows_single_clean_window_has_no_worst():
    d = FillerDetector()
    d.start_session()
    d.process_words([
        {"word": "hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.0},
        {"word": "again", "start": 1.0, "end": 1.4},
        {"word": "now", "start": 1.4, "end": 1.8},
    ])
    rw = d.get_replay_windows()
    assert rw["best"] is not None
    assert rw["worst"] is None


def test_replay_windows_gated_on_real_timestamps():
    d = FillerDetector()
    d.start_session()
    # all-zero timestamps (offline / transcript fallback) -> not eligible
    d.process_words([
        {"word": "um", "start": 0.0, "end": 0.0},
        {"word": "like", "start": 0.0, "end": 0.0},
        {"word": "basically", "start": 0.0, "end": 0.0},
        {"word": "thing", "start": 0.0, "end": 0.0},
    ])
    rw = d.get_replay_windows()
    assert rw["best"] is None
    assert rw["worst"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_filler_detector.py::test_replay_windows_gated_on_real_timestamps -q`
Expected: FAIL — `AttributeError: 'FillerDetector' object has no attribute 'get_replay_windows'`.

- [ ] **Step 3: Implement `get_replay_windows`**

In `filler_detector.py`, add this method directly after `get_worst_window`:

```python
    def get_replay_windows(self) -> dict:
        """Return {"best": window|None, "worst": window|None} for audio replay.

        Each window is {"text", "start", "end"}. A window is only returned when it
        has a real audio span (end > start). If best and worst are the same span,
        worst is dropped.
        """
        def valid(w: dict | None) -> bool:
            return w is not None and w["end"] > w["start"]

        best = self.get_best_window()
        worst = self.get_worst_window()
        best = best if valid(best) else None
        worst = worst if valid(worst) else None

        if best and worst and best["start"] == worst["start"] and best["end"] == worst["end"]:
            worst = None

        return {"best": best, "worst": worst}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_filler_detector.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add filler_detector.py tests/test_filler_detector.py
git commit -m "feat: add get_replay_windows with dedup and timestamp gate"
```

---

## Task 4: /api/report returns the replay windows

**Files:**
- Modify: `main.py` (the `/api/report` handler)
- Test: `tests/test_report_replay.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_replay.py`:

```python
from fastapi.testclient import TestClient
import main


def test_report_includes_replay(monkeypatch):
    async def fake_report(*args, **kwargs):
        return {"summary": "ok"}

    monkeypatch.setattr(main, "generate_report", fake_report)

    sess = main.SessionState()
    sess.start()
    sess.detector.process_words([
        {"word": "we", "start": 0.0, "end": 0.3},
        {"word": "ship", "start": 0.3, "end": 0.7},
        {"word": "fast", "start": 0.7, "end": 1.1},
        {"word": "today", "start": 1.1, "end": 1.6},
    ])
    main.SESSIONS["testsid"] = sess

    client = TestClient(main.fastapi_app)
    res = client.post("/api/report", json={"sid": "testsid", "topic": "x"})

    assert res.status_code == 200
    body = res.json()
    assert "replay" in body
    assert body["replay"]["best"]["text"] == "we ship fast today"
    assert body["replay"]["best"]["start"] == 0.0
    assert body["replay"]["best"]["end"] == 1.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report_replay.py -q`
Expected: FAIL — response body has no `replay` key (`KeyError` / assertion error).

- [ ] **Step 3: Add the replay payload to the report**

In `main.py`, in the `/api/report` handler, find this block:

```python
        report = await generate_report(
            transcript,
            stats,
            topic=topic,
            mode=mode,
            highlight_window=highlight_window,
        )
        return JSONResponse(report)
```

Replace it with:

```python
        report = await generate_report(
            transcript,
            stats,
            topic=topic,
            mode=mode,
            highlight_window=highlight_window,
        )
        report["replay"] = sess.detector.get_replay_windows()
        return JSONResponse(report)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_report_replay.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_report_replay.py
git commit -m "feat: /api/report returns best/worst replay windows"
```

---

## Task 5: Browser buffers the full session PCM

**Files:**
- Modify: `static/index.html` (globals near `ttsCtx`; `startAudioCapture`; `onaudioprocess`; btnStart handler)

This task is browser JS with no unit-test runner; verify manually in the browser console.

- [ ] **Step 1: Add the PCM buffer globals**

In `static/index.html`, find this line (around line 1244):

```javascript
    let ttsCtx = null; // shared AudioContext for TTS — created on first user gesture
```

Add directly after it:

```javascript
    let sessionPcm = [];     // array of Float32Array chunks for the whole session
    let sessionPcmRate = 0;  // sample rate of the captured audio
```

- [ ] **Step 2: Record the sample rate when capture starts**

In `startAudioCapture`, find:

```javascript
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      audioContext = new AudioContext();
```

Add directly after it:

```javascript
      sessionPcmRate = audioContext.sampleRate;
```

- [ ] **Step 3: Buffer each PCM chunk**

In `startAudioCapture`, inside `processorNode.onaudioprocess`, find:

```javascript
        if (!audioWs || audioWs.readyState !== WebSocket.OPEN) return;
        const pcm16 = float32ToPcm16(float32);
        audioWs.send(pcm16.buffer);
```

Replace it with (buffer a copy before the early-return, since the source buffer is reused by the audio graph):

```javascript
        sessionPcm.push(new Float32Array(float32));

        if (!audioWs || audioWs.readyState !== WebSocket.OPEN) return;
        const pcm16 = float32ToPcm16(float32);
        audioWs.send(pcm16.buffer);
```

- [ ] **Step 4: Reset the buffer when a new session starts**

In the `btnStart` click handler, find:

```javascript
        // Prime TTS AudioContext during user gesture so it's unlocked for socket-triggered nudges
        if (!ttsCtx) ttsCtx = new AudioContext();

        await startAudioCapture();
```

Add the reset before `startAudioCapture()`:

```javascript
        // Prime TTS AudioContext during user gesture so it's unlocked for socket-triggered nudges
        if (!ttsCtx) ttsCtx = new AudioContext();

        sessionPcm = [];
        sessionPcmRate = 0;
        await startAudioCapture();
```

- [ ] **Step 5: Manually verify buffering**

Run the app: `uvicorn main:app --port 8080`. Open `http://localhost:8080`, start a session, speak ~5 seconds, stop.
In the browser console, run: `sessionPcm.length` and `sessionPcmRate`.
Expected: `sessionPcm.length` is a large positive number (hundreds), `sessionPcmRate` is the device rate (e.g. 48000).

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat: buffer full-session PCM in the browser for replay"
```

---

## Task 6: Render the replay A/B card and play sliced audio

**Files:**
- Modify: `static/index.html` (debrief HTML block; CSS; `renderDebrief`; new `playPcmRange` helper)

Browser JS — verify manually.

- [ ] **Step 1: Add the replay card markup**

In `static/index.html`, find the highlight-moment block (around line 1198):

```html
        <!-- Highlight moment -->
        <div id="highlightMomentBlock" style="display:none" class="highlight-box">
          <div class="hl-label">Highlight Moment — Best Delivery</div>
          <p id="highlightMomentText"></p>
        </div>
```

Add this new block directly after it:

```html
        <!-- Voice replay (the speaker's real audio) -->
        <div id="replayBlock" style="display:none" class="example-box">
          <div class="ex-label">Replay Your Voice</div>
          <div class="replay-row">
            <button class="btn-hear" id="btnPlayBest">&#9654; Your strongest moment</button>
            <span class="replay-text" id="bestMomentText"></span>
          </div>
          <div class="replay-row">
            <button class="btn-hear" id="btnPlayWorst">&#9654; Your roughest moment</button>
            <span class="replay-text" id="worstMomentText"></span>
          </div>
        </div>
```

- [ ] **Step 2: Add minimal CSS for the replay rows**

In `static/index.html`, find this CSS rule (around line 977):

```css
      .debrief-grid { grid-template-columns: 1fr; }
```

Add these rules directly after it (still inside the same `<style>` block):

```css
      .replay-row { display: flex; align-items: center; gap: 12px; margin-top: 10px; flex-wrap: wrap; }
      .replay-text { font-size: 0.9rem; opacity: 0.85; }
```

- [ ] **Step 3: Add the `playPcmRange` helper**

In `static/index.html`, find the `renderDebrief` function definition:

```javascript
    function renderDebrief(data) {
```

Add this helper directly BEFORE it:

```javascript
    function playPcmRange(startSec, endSec) {
      if (!sessionPcm.length || !sessionPcmRate) return;
      const total = sessionPcm.reduce((n, c) => n + c.length, 0);
      const from = Math.min(Math.max(0, Math.floor(startSec * sessionPcmRate)), total);
      const to = Math.min(Math.max(from, Math.floor(endSec * sessionPcmRate)), total);
      const length = to - from;
      if (length <= 0) return;

      const out = new Float32Array(length);
      let pos = 0, copied = 0;
      for (const chunk of sessionPcm) {
        const chunkStart = pos;
        const chunkEnd = pos + chunk.length;
        if (chunkEnd > from && chunkStart < to) {
          const s = Math.max(0, from - chunkStart);
          const e = Math.min(chunk.length, to - chunkStart);
          out.set(chunk.subarray(s, e), copied);
          copied += (e - s);
        }
        pos = chunkEnd;
      }

      if (!ttsCtx) ttsCtx = new AudioContext();
      if (ttsCtx.state === 'suspended') ttsCtx.resume();
      const buf = ttsCtx.createBuffer(1, out.length, sessionPcmRate);
      buf.getChannelData(0).set(out);
      const src = ttsCtx.createBufferSource();
      src.buffer = buf;
      src.connect(ttsCtx.destination);
      src.start();
    }
```

- [ ] **Step 4: Wire the card in `renderDebrief`**

In `static/index.html`, inside `renderDebrief`, find:

```javascript
      const ds = document.getElementById('debriefSection');
      ds.classList.add('open');
      ds.scrollTop = 0;
```

Add this block directly BEFORE those three lines:

```javascript
      // Voice replay card — real audio of best/worst moments
      const replay = data.replay || {};
      const best = replay.best;
      const worst = replay.worst;
      const haveAudio = sessionPcm.length > 0 && sessionPcmRate > 0;
      const replayBlock = document.getElementById('replayBlock');
      if (haveAudio && (best || worst)) {
        const bestBtn = document.getElementById('btnPlayBest');
        if (best) {
          document.getElementById('bestMomentText').textContent = best.text || '';
          bestBtn.style.display = '';
          bestBtn.onclick = () => playPcmRange(best.start, best.end);
        } else {
          bestBtn.style.display = 'none';
          document.getElementById('bestMomentText').textContent = '';
        }
        const worstBtn = document.getElementById('btnPlayWorst');
        if (worst) {
          document.getElementById('worstMomentText').textContent = worst.text || '';
          worstBtn.style.display = '';
          worstBtn.onclick = () => playPcmRange(worst.start, worst.end);
        } else {
          worstBtn.style.display = 'none';
          document.getElementById('worstMomentText').textContent = '';
        }
        replayBlock.style.display = 'block';
      } else {
        replayBlock.style.display = 'none';
      }
```

- [ ] **Step 5: Manually verify the full flow**

Run: `uvicorn main:app --port 8080`. Open `http://localhost:8080`, record ~30 seconds with some clean speech and some deliberate "um like basically" stretches, then click End & Debrief.
Expected: the "Replay Your Voice" card appears. Clicking "Your strongest moment" plays your real audio of the clean stretch; "Your roughest moment" plays the filler-heavy stretch. The text next to each button matches what plays.

- [ ] **Step 6: Verify offline mode hides the card**

Temporarily stop any Pulse connectivity (or run without a valid `SMALLEST_API_KEY`) so the server uses offline/mock mode, record, and debrief.
Expected: the "Replay Your Voice" card does NOT appear (no real timestamps -> empty replay).

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat: replay A/B card plays real best/worst audio in the debrief"
```

---

## Self-Review (completed during planning)

**Spec coverage (Increment 1 only):**
- Window audio offsets -> Task 1. `get_best_window` dict + caller update -> Task 1. `get_worst_window` + min-token rule -> Task 2. `get_replay_windows` dedup + timestamp gate -> Task 3. `/api/report` returns `replay` -> Task 4. Browser PCM buffer + reset on start -> Task 5. A/B card, slice-and-play, offline-hidden -> Task 6. All Increment 1 spec items covered.
- Increment 2 (cloning, consent, `tts_provider.py`) is intentionally out of scope — separate plan.

**Error handling from spec:** no timestamps -> card hidden (Task 6 Step 6 verifies); best==worst -> worst dropped (Task 3); out-of-range slice -> clamped in `playPcmRange` (Task 6 Step 3); suspended AudioContext -> `resume()` in `playPcmRange` (Task 6 Step 3).

**Type consistency:** `get_best_window`, `get_worst_window` both return `{"text", "start", "end"}` or `None`; `get_replay_windows` returns `{"best", "worst"}`; frontend reads `data.replay.best/worst` with `.text/.start/.end`. Consistent across tasks.

**Placeholder scan:** no TBD/TODO; every code step shows complete code.
