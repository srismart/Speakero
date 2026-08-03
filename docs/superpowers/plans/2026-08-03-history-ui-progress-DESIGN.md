# Session History + Progress Chart (Spec B read-side) - Design and Open Questions

> Status: design draft for user review. The write-path (persistence.py, sessions table) shipped in PR #11. This is the read-side. It needs a few UX decisions before implementation, listed at the bottom.

## Goal

Let a signed-in user see their past sessions and their delivery-score / filler-rate / WPM trend over time. This is the retention loop: the reason to come back and the thing worth paying for.

## What already exists to build on

- `sessions` table (PR #11 migration): user_id, topic, mode, filler_count, pause_count, wpm, total_words, duration_seconds, delivery_score, content_score, verdict, created_at. Owner-scoped SELECT RLS policy already in place.
- Frontend already loads the Supabase JS client and has an auth token (`authToken`, `authHeaders()`), plus `userTier`. The debrief renders in `#debriefSection`.
- oklch theme tokens, Space Grotesk/Space Mono, the ring-gauge and chip components from the Momentum redesign are reusable.

## Proposed approach (Python-native, no JS framework, per CLAUDE.md)

Two thin slices, each its own PR:

### Slice 1: History list
- New backend route `GET /api/history` (auth required): reads the caller's rows from Supabase via the service key, returns the last N (e.g. 20) sessions as JSON. Fail-open to an empty list when Supabase is not configured. Reuses the `_binding_check` / `verify_token` auth already in main.py.
- Frontend: a "History" panel (new header menu entry, the menus are already functional) that fetches `/api/history` and renders a list of cards: date, topic, mode, delivery score ring (reuse the gauge), filler count, WPM. Empty state for new users ("Your finished sessions will show up here").
- Alternative considered: read directly from Supabase in the browser via the anon key + RLS policy (the migration already supports this). That removes a backend round-trip but splits data access across two paths. Recommend the backend route for one consistent access path and so anon-key exposure stays minimal.

### Slice 2: Progress chart
- Extend `/api/history` (or a `GET /api/progress`) to return the score/filler/WPM series.
- Frontend: a small inline SVG line chart (no chart library, matches the vanilla-JS constraint and the existing hand-built 21-bar waveform) showing delivery_score over the last N sessions, with filler-rate and WPM as toggles. The dataviz skill's guidance on a single accessible color system applies.

## Open questions for the user

1. History access path: backend route (recommended, one consistent path) vs. direct browser Supabase read via RLS (fewer round-trips)?
2. How many sessions in the list / chart by default (20? 50? all)?
3. Chart metric priority: is delivery score the hero line, with filler-rate and WPM as toggles, or show all three at once?
4. Should anonymous users see anything (e.g. a local-only "this session" view) or is history strictly a signed-in feature (matches current "sign in to save" copy)?
5. Free vs Pro gating: is full history a Pro feature (assessment suggested "history beyond 7 days" as a Pro hook), or free for everyone to drive retention first and gate later?

## Test plan
- Backend: `/api/history` returns owner rows only, empty list when unconfigured, 401 for anonymous, fail-open on Supabase error (mirror test_persistence.py).
- Frontend wiring: history panel IDs present, `/api/history` referenced, empty-state present, chart renders from a fixture series.
