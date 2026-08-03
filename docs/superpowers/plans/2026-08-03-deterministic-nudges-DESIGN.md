# Deterministic Nudge Layer - Design and Open Questions

> Status: design draft for user review. This is the biggest cost/latency lever in the assessment, but it changes coaching behavior, so it needs sign-off on the rules and copy before implementation.

## Goal

Most live nudges are responses to mechanical delivery issues (filler streak, talking too fast, a long silence). Those do not need an LLM. Detect them with rules and play pre-synthesized canned audio: zero LLM cost, zero TTS latency, instant playback. Reserve the Claude nudge for content-level coaching (drifting off-topic, rambling, weak structure), where its judgment actually earns its cost.

## Current behavior (what changes)

`coaching_loop` fires every 30s: it buffers final transcripts, sends the snippet to Claude Haiku, and emits one `nudge` with synthesized TTS. The filler detector already computes filler streaks (3 in 10s), WPM, and pause counts and emits `filler_streak` events.

## Proposed approach (additive, non-degrading)

1. **Rule layer** (`nudge_rules.py`): pure functions over the detector's live stats returning an optional canned-nudge key, e.g.:
   - `filler_streak` (already detected) -> "Watch the filler words. Take a breath."
   - `wpm > fast_threshold` sustained -> "Slow down a touch, let it land."
   - `pause_count` spike / long silence -> "It's okay to pause. Gather your thought."
   A small fixed set (start with 3-4), each mapped to one canned line.

2. **Canned audio, pre-synthesized once** (`scripts/generate_canned_nudges.py`): run the TTS provider over each canned line at build/deploy time, commit the WAVs under `static/nudges/`. Playback is a static file fetch, no `/api/speak` call. (This is the small authorized TTS spend, one-time.)

3. **Arbitration with the Claude loop**: when a rule fires, emit the canned nudge immediately and suppress the next scheduled Claude nudge (debounce, so the user is not double-nudged). The 30s Claude loop stays as the fallback for content coaching when no rule has fired recently. The existing `nudge` socket event and playback manager are reused; a new `canned_nudge` event carries the audio URL (or reuse `nudge` with an audio_url field).

4. **Config**: thresholds (fast WPM, silence length, debounce window) as module constants with env overrides, matching limits.py style.

## Why this is the cost/latency lever

At the current 30s cadence a 15-min session is up to ~30 Claude calls + ~30 TTS calls. If rules handle the mechanical majority, Claude calls drop substantially and the mechanical nudges become instant (no synth wait), which is also a better user experience.

## Open questions for the user

1. Nudge copy: approve the canned lines (I will draft a set of ~4). Voice/tone should match the coach persona. What is the coach's voice - encouraging, direct, neutral?
2. Thresholds: what WPM counts as "too fast" for this audience (interview prep vs presentations differ)? Default proposal: ~185 wpm sustained.
3. Arbitration: when a rule fires, fully suppress the next Claude nudge, or let both fire if they are about different things? Recommend suppress-to-debounce to avoid nudge spam.
4. Cadence: keep the 30s Claude fallback, or make Claude fire less often (e.g. every 60s) once rules cover the mechanical cases?
5. Do canned nudges respect the existing mute-during-coach-audio logic and the pause-aware behavior from PR history? (They should; needs wiring.)

## Test plan
- `nudge_rules.py` pure-function tests: each rule fires on the right stats, none on clean delivery, debounce respected.
- Arbitration test: a fired rule suppresses the scheduled Claude nudge within the debounce window.
- Frontend wiring: canned-nudge event handled, audio URL played through the existing playback manager, respects mute-during-coach-audio.
- `scripts/generate_canned_nudges.py` is idempotent and does not run in CI (committed WAVs are the source of truth).
