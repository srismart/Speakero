# Speakero — "Hear Yourself, Perfected"

**Date:** 2026-06-09
**Status:** Approved design, ready for implementation plan
**Scope:** In-session debrief feature. Replay the speaker's own audio and let them
hear a polished version of a fumbled moment in their own (cloned) voice.

## Goal

In the session debrief, give the speaker a before/after pair built from their own voice:

- **Before:** their *real* recorded audio of a rough stretch — "here's what you did."
- **After:** their *cloned* voice speaking Claude's polished rewrite — "here's how you
  could have sounded."

Same voice, both halves. The contrast is the point: documentary then aspirational.

This replaces an earlier idea of synthesizing speech with a generic AI voice, and an
even earlier misread that we needed third-party voice *cloning* research code
(Real-Time-Voice-Cloning). We need neither. The "before" is the user's literal audio;
the "after" uses a hosted instant-clone TTS already available in our stack.

## Non-goals (explicitly deferred)

These depend on auth (roadmap #2) and persistence (roadmap #3) and are their own specs:

- **Past-session replay** — revisiting a previous session's audio. Requires storing audio.
- **Cross-session progress tracking** — comparing runs of "the same speech" over time.

The window data shape in this spec (`{text, start, end}`) is deliberately
persistence-ready so those pieces reuse it rather than redo it.

## Constraints (reaffirmed)

- **Python-native, single small container, low operational overhead.** Hosted cloning
  preserves this — it is an outbound HTTPS call, no GPU, no model weights. Self-hosting
  an open model (Chatterbox/XTTS/F5) was rejected precisely because it breaks the
  container/ops constraint and a fixed GPU bill cannot pass through to the user.
- **Pass incremental costs to the user.** Cloning is metered per debrief and gated as a
  premium feature (ties to monetization, roadmap #4). Real replay stays free.
- **No em-dashes in user-facing copy.** (Repo content convention.)
- **No JS frameworks.** Vanilla JS only.

## Architecture overview

```
Increment 1 (free, browser-side)
  Pulse word timestamps ──> FillerDetector windows {text, start, end}
                               │
  /api/report  ──────────────> report["replay"] = {best, worst}
                               │  report["replay"]["worst"]["rewrite"] = Claude's
                               │     polished version of the WORST window text
                               ▼
  Browser PCM buffer ──slice[start*rate, end*rate]──> Web Audio playback

Increment 2 (premium, hosted clone)
  Browser slices BEST window PCM (cleanest sample) as reference clip
        │  + the worst window's "rewrite" (same content, cleaned up)
        ▼
  /api/clone_speak ──> tts_provider.clone_and_speak(text, reference)
                               │  (smallest.ai: add_voice -> synthesize -> delete_voice)
                               ▼
  returned audio ──> Web Audio playback ("here's how you could've sounded")
```

No audio persists on the server. Only a few seconds of reference audio leave the
browser, and the clone is created and deleted within a single request.

---

## Increment 1 — Real best/worst replay (browser-side, free)

Independently shippable. De-risks the player before any cloning work.

### `filler_detector.py`

- Each recorded window also stores its audio span, taken from the real Pulse word
  timestamps already read in `process_words`: `audio_start` = first word's `start`,
  `audio_end` = last word's `end`.
- `get_best_window()` returns a dict `{text, start, end}` instead of a bare string.
  - Existing caller in `report.py`/`main.py` passes the best window text into Claude's
    `highlight_window` prompt; update that call site to use `.text` so nothing breaks.
- Add `get_worst_window()`: the window with the highest filler density, requiring a
  minimum token count (~4) so a trivial 2-word stumble does not win.
- Add `get_replay_windows()` -> `{"best": {...}|None, "worst": {...}|None}`:
  - Deduped: if best and worst resolve to the same window, set `worst` to `None`.
  - Gated: a window is only eligible when `audio_end > audio_start` (real timestamps
    present). Offline mode and the `transcript`-only Pulse fallback yield `None`.

### `main.py` / `report.py`

- `/api/report` adds `report["replay"] = sess.detector.get_replay_windows()`.
- **For a true before/after (Increment 2):** when a worst window exists, have Claude
  rewrite *that specific text* into a polished delivery, and attach it as
  `report["replay"]["worst"]["rewrite"]`. This is a targeted rewrite of the worst window,
  not the generic `example_extract` (which is drawn from the whole transcript and may be
  unrelated to the rough clip). `report.py` gains the worst window text as input and
  returns the rewrite alongside the existing report keys. The generic `example_extract`
  stays as-is for the text debrief.
- No audio touches the server.

### `static/index.html`

- Add `sessionPcm = []` and `sessionPcmRate`. In `onaudioprocess`, push a **copy**
  (`new Float32Array(float32)` — the source buffer is reused by the audio graph) right
  where it already streams to the WS. Reset both on session start.
- On debrief render, if `data.replay` exists and PCM is present, render an A/B card:
  - **Your strongest moment** and **Your roughest moment** buttons, each showing its
    transcript text.
  - Click slices `[start*rate, end*rate]` from the buffer into an `AudioBuffer` and plays
    it through the existing `ttsCtx` (call `resume()` on the click gesture).
- If there is no replay data or no PCM (offline mode), the card stays hidden.

### Known limitations (v1, by design)

- Browser holds full-session PCM (~11 MB/min at 48 kHz). Fine for practice-length
  sessions; downsampling is a later optimization (YAGNI now).
- Window granularity = one Pulse final chunk, reusing existing windowing.
- Time base assumes no dropped PCM chunks between browser and Pulse; minor drift is
  acceptable for 10-30 s windows.

---

## Increment 2 — Ephemeral cloned "after" (premium, smallest.ai)

Completes the before/after. Sits behind the provider abstraction (roadmap #1).

### `tts_provider.py` (new)

- Abstract interface with two capabilities:
  - `speak(text) -> bytes` — plain TTS (current Lightning behavior).
  - `clone_and_speak(text, reference_audio) -> bytes` — instant clone + synth.
- `SmallestProvider` implements both via `WavesClient` (already wraps smallest.ai):
  - `speak`: `synthesize(text, voice_id="emily", ...)` (Lightning), as today.
  - `clone_and_speak`: write `reference_audio` to a temp file, `add_voice(name, path)`
    -> `voice_id`, `synthesize(text, voice_id=...)`, then `delete_voice(voice_id)` and
    remove the temp file in a `finally`. Ephemeral: no clone persists.
- Existing `tts.py` / `/api/speak` route through `SmallestProvider.speak` so there is a
  single path to TTS.

#### Future provider: Cartesia (documented, not built now)

- The abstraction exists so providers are swappable. A `CartesiaProvider` is a planned
  later option (fast, cheap, free instant-clone tier, no monthly floor).
- Setup when adopted: add a `CARTESIA_API_KEY` env var and implement `speak` /
  `clone_and_speak` against Cartesia's instant-clone API. No other code changes — call
  sites depend only on the interface. Provider is selected by an env var / config value.

### `main.py`

- New `/api/clone_speak` endpoint taking `{sid, reference_clip, text}`:
  - Resolve `SESSIONS[sid]`; call `provider.clone_and_speak(text, reference_clip)`;
    return audio bytes (`audio/wav`).
  - Called **lazily** — only when the user clicks the button — so cost is metered only on
    actual use.

### `static/index.html`

- On the roughest-moment card, add **Hear how you could've sounded**:
  - Browser slices the **best window** PCM (the cleanest sample of the user's voice) as
    the reference clip, encodes it (WAV), and POSTs it with the worst window's `rewrite`
    (same content, cleaned up) to `/api/clone_speak`.
  - Plays the returned clip right after the real "before" clip, so the user hears the same
    content rough-then-polished in their own voice.
- First use shows a one-time **consent disclosure**: "we use a few seconds of your audio
  to generate this clip, then discard it." No call is made without consent.

### Premium gating

- Auth/billing (#2/#4) do not exist yet. Increment 2 ships behind a simple feature flag,
  with a TODO to wire real metering/entitlement when billing lands.

### Cost posture

- Reference clip ~5 s; synth text = `example_extract` (1-2 sentences). Marginal cost per
  debrief is tiny and exactly meterable, so it passes through cleanly to the premium tier.

---

## Error handling

- **No timestamps** (offline / transcript fallback) -> empty replay -> card hidden.
- **best == worst** -> show only the best clip.
- **Out-of-range slice** -> clamp to the PCM buffer bounds.
- **Suspended AudioContext** -> `resume()` on the click (user gesture).
- **Clone API failure** -> the "after" button surfaces an error; the real "before" replay
  still works independently.
- **Reference clip too short / no consent** -> skip the clone call with a message.

## Testing

- **Increment 1 (TDD, pure Python):** unit tests for `filler_detector` — feed synthetic
  word lists with `start`/`end`; assert best/worst selection, correct offsets, the
  min-length rule for worst, dedup, and the `audio_end > audio_start` gate.
- **Increment 2:** unit test `tts_provider` with a mocked `WavesClient` — assert
  `clone_and_speak` calls `add_voice` -> `synthesize` -> `delete_voice` and cleans up the
  temp file even on synth failure.
- **Manual smoke:** record -> end -> play both real clips -> trigger the cloned "after";
  confirm offline mode hides the card; confirm two-tab isolation still holds (per CLAUDE.md
  multi-tenant smoke test).

## Setup checklist for the user

- **Increment 1:** nothing.
- **Increment 2:** confirm the smallest.ai plan permits voice cloning (`add_voice`
  access). SDK (`smallestai` 4.3.8) and `SMALLEST_API_KEY` are already in place; no new
  dependency or secret.
- **Cartesia (later):** add `CARTESIA_API_KEY` when adopting that provider.
