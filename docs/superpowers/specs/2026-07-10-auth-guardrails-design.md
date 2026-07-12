# Speakero: Auth, Free-Tier Guardrails, and LLM Cost Hardening (Spec A)

**Date:** 2026-07-10
**Status:** Approved design, pending user review of this document
**Scope:** First of three specs on the monetization path:
Spec A (this): auth + anonymous guardrails + LLM ops hardening.
Spec B (next): session persistence + history (Supabase tables).
Spec C (after): Stripe Pro subscription gate.

## Decisions locked during brainstorming

1. **Monetization shape:** Free + Pro flat monthly subscription. The voice-clone
   "how you could sound" feature (Increment 2 of the replay spec) is the paywall.
   Stripe subscriptions only; no usage metering at launch.
2. **Login gate:** try free, sign in to save. Anyone can run a practice session
   and see the debrief with no account. Sign-in is required to save sessions,
   see history (Spec B), or go Pro (Spec C).
3. **Auth provider:** Supabase Auth (replaces the CLAUDE.md Clerk plan; one
   vendor for auth and the upcoming persistence). Email magic link + Google OAuth
   via supabase-js in the frontend; FastAPI verifies the JWT locally.
4. **Nudge model:** Haiku 4.5 (a 15-word nudge is a simple, speed-critical task;
   roughly 3x cheaper and faster). Debrief stays on Sonnet.
5. **Limits philosophy (revised 2026-07-10):** hard session caps for EVERYONE as
   runaway-resource protection (a mic left on cannot stream STT for hours).
   Anonymous gets 1-2 tries. Signing in unlocks authenticated features (saves,
   tracking), not extra minutes as the primary pitch. Pro unlocks additional
   time (longer default cap plus a per-session override for keynote practice)
   and pro features (voice clone).
6. **Limits are live config, not code:** all limit values live in a Supabase
   `app_config` table and are editable in Supabase Studio with no redeploy.
   **Supabase Studio is the v1 admin console** (user management via its Auth
   section, ops toggles via the table editor). A custom in-app admin dashboard
   is deferred until there is traffic worth visualizing; the usage logging in
   this spec is its future data feed.

## Tier limits (cost vs value-unlock analysis, 2026-07-10)

A full 5-minute session costs roughly 7-9 cents of COGS (STT streaming
~$0.005/min dominates; a nudge is ~$0.002 on Sonnet, ~$0.0007 on Haiku; the
debrief ~$0.015-0.02; TTS is noise). Anonymous users therefore get the FULL
experience, briefly: voice nudges included (voice is the demo; withholding it
to save a tenth of a cent undersells the product).

All values below are `app_config` defaults, tunable live in Supabase Studio.

| | Anonymous | Signed-in Free | Pro (Spec C) |
|---|---|---|---|
| Session hard cap (auto-stop) | 5 min | 15 min | 30 min default |
| Per-session override | no | no | up to 60 min (keynote practice) |
| Sessions per day | 2 per IP + browser marker | config key, default unlimited | config key, default unlimited |
| Sessions per month | n/a (daily cap covers it) | config key, default 8 | config key, default unlimited |
| Voice nudges | yes | yes | yes |
| Debrief + real-audio replay | yes | yes | yes |
| Saved history / tracking | no | yes (Spec B) | yes |
| Voice clone "after" | no | no | yes |

- Every session has a server-enforced cap regardless of tier: runaway-resource
  protection first (STT streaming cost, ~11MB/min browser PCM), monetization
  second. The absolute ceiling anywhere is the Pro override max (60).
- The auto-stop is the conversion moment. Anonymous copy: "Sign in to save
  this session and keep practicing." Free copy: "Upgrade for longer sessions."
- Two anonymous sessions per day, not one: false starts (mic issues) are
  common, and a same-day retry is a hot lead. Worst-case abuser cost is about
  $0.15/day, which is ignorable.
- Free/Pro count caps (daily and monthly) are live config parameters for cost
  control: turn them to 0 during an incident, 25 for a promo, unlimited when
  comfortable, all in Studio with no deploy. Free monthly defaults to 8.
- Monthly counting requires persistence across restarts, so Spec A pulls one
  small table forward from Spec B: `session_starts` (user_id, tier,
  started_at), one row written per signed-in session start. Counting is a
  PostgREST count query. If Supabase is unreachable, counting fails OPEN
  (never block users because the config store is down; log a warning).
- These numbers are estimates. Re-verify against real smallest.ai and Anthropic
  invoices after a week of traffic and tune values in Studio; the structure
  (hard caps for all, counts for anonymous, Pro time override) is the durable
  decision.

## Architecture

```
Browser (single-file, vanilla JS)
  supabase-js (CDN)  ->  Supabase Auth (magic link, Google OAuth)
  |   access_token (JWT)
  v
  fetch /api/*  with Authorization: Bearer <token>
  socket.io connect with auth: { token }
  |
FastAPI (main.py)
  auth.py: verify JWT signature locally (HS256, SUPABASE_JWT_SECRET) -> user_id
  SessionState gains user_id + tier; sid is bound to the token's user
  Tier limits enforced server-side (length timer, anonymous day-counter)
```

No Supabase server-side SDK is needed in Spec A: the backend verifies JWTs
locally and reads one `app_config` table via a single PostgREST GET (httpx,
service-role key) with a 60s in-memory cache and env-var fallbacks when
Supabase is unreachable. Full Supabase data access arrives in Spec B.

### app_config table (created in Supabase Studio)

Key/value rows, service-role read only. Default rows:

| key | default |
|---|---|
| `max_session_minutes.anonymous` | 5 |
| `max_session_minutes.free` | 15 |
| `max_session_minutes.pro` | 30 |
| `pro_override_max_minutes` | 60 |
| `anon_sessions_per_day` | 2 |
| `sessions_per_day.free` / `.pro` | null (unlimited) |
| `sessions_per_month.free` | 8 |
| `sessions_per_month.pro` | null (unlimited) |
| `tts_calls_per_session` | 30 |

Editing a row changes live behavior within the cache TTL (60s), no deploy.

### session_starts table (created in Supabase Studio, pulled forward from Spec B)

`id (uuid, default gen_random_uuid()), user_id (uuid), tier (text),
started_at (timestamptz, default now())`. Service-role access only. One row
per signed-in session start; anonymous sessions are not recorded (no
identity). Daily/monthly caps count rows for the user in the window.

## Components

### 1. `auth.py` (new)

- `verify_token(authorization_header) -> AuthContext | None` where
  `AuthContext = {user_id, email, tier}`. Verifies the Supabase JWT with
  PyJWT (HS256, `SUPABASE_JWT_SECRET`, audience `authenticated`,
  expiry checked). Returns None for missing/invalid tokens (anonymous).
- `tier` reads `app_metadata.tier` from the JWT, defaulting to `free`.
  Spec C writes `pro` into user app_metadata; nothing here changes then.
- New env vars: `SUPABASE_URL`, `SUPABASE_ANON_KEY` (public, served to the
  frontend), `SUPABASE_JWT_SECRET` (backend only). New dependency: `PyJWT`.

### 2. `limits.py` (new)

- `get_config()` reads `app_config` (PostgREST GET, 60s TTL cache, env-var
  fallbacks named like `LIMIT_MAX_SESSION_MINUTES_FREE`); the table above is
  the schema.
- `resolve_session_minutes(tier, requested_minutes) -> int`: returns the
  tier's cap; for Pro, honors `requested_minutes` clamped to
  `pro_override_max_minutes`. Non-Pro requests above their cap are clamped,
  never errored (friendlier, nothing to exploit).
- Anonymous day-counter: in-memory `{key: [timestamps]}` where key is both the
  client IP and a browser marker (localStorage UUID the frontend sends on
  /api/start); either tripping blocks. Entries older than 24h pruned.
  In-memory is acceptable for the single-process deployment; a restart resets
  counters (documented; Spec B can persist if abuse shows up).
- `check_and_count(auth, ip, marker) -> allow | {error, reason}`:
  anonymous consults the in-memory day-counter; signed-in consults
  `session_starts` counts against the per-tier daily/monthly config keys and
  inserts the new row on allow. Supabase errors fail open with a warning.

### 3. `main.py` changes

- `/api/config` (GET): returns `{supabase_url, supabase_anon_key}` so the
  single-file frontend needs no build-time injection.
- Socket connect: read `auth.token` from the socket.io handshake, resolve
  AuthContext, stamp `sess.user_id` / `sess.tier`.
- `/api/start`: resolve AuthContext from the Authorization header; verify it
  matches the session's user (or both anonymous); run the anonymous
  day-counter; accept optional `requested_minutes` (Pro override, clamped via
  `resolve_session_minutes`); schedule a **session length timer** (asyncio
  task) that fires at the resolved limit, performs the same teardown as
  /api/stop, and emits a `session_limit` event to the room.
- `/api/stop`, `/api/report`, `/api/speak`: same sid-user binding check.
  This closes the deferred "any client with a sid can drive another session"
  hole for signed-in users.
- `/api/speak` gating: require a live session and cap TTS calls per session
  (30); return 429 beyond. Closes the open-TTS-proxy hole.
- Debrief memoization: store the generated report on SessionState; repeat
  /api/report calls return the cached report (saves ~$0.02 per re-click and
  makes re-opening the debrief instant).

### 4. Frontend (static/index.html)

- Load supabase-js from CDN; init from `/api/config`.
- Header auth UI: signed-out shows "Sign in" (modal: email magic link +
  "Continue with Google"); signed-in shows the user's email/avatar + sign-out.
- All `/api/*` fetches attach `Authorization: Bearer <access_token>` when a
  session exists; socket.io connect passes `auth: {token}` (and reconnect
  re-passes it).
- Browser marker: `localStorage["speakero_marker"] = crypto.randomUUID()`
  (created once), sent with /api/start.
- `session_limit` event: stop the local capture UI and show the conversion
  moment (anonymous: "Sign in to save this session and keep practicing" with
  the sign-in modal; free: "Upgrade for longer sessions" placeholder until
  Spec C).
- Pro users get a session-length selector before start (default 30, up to the
  override max) for long keynote rehearsals; hidden for other tiers.
- Timer display counts down from the resolved limit rather than up.

### 5. LLM ops hardening (small items folded in)

- **Structured outputs for the debrief:** replace the "Return ONLY valid
  JSON" prompt + fence stripping + retry-once with `output_config.format`
  (json_schema mirroring the existing report keys). Valid JSON becomes an API
  guarantee, so the malformed-JSON failure class disappears; the degraded
  fallback stays (network errors still exist). Requires a current `anthropic`
  pip package; verify at implementation time.
- **Shared client + timeouts:** one module-level `AsyncAnthropic` client;
  10s timeout on nudge calls, 30s on the debrief call.
- **Nudge model:** `claude-haiku-4-5`.
- **TTS LRU cache:** in-memory, ~128 entries, keyed on hash of text (voice is
  fixed by env). Repeated phrases cost zero TTS and play instantly.
- **Usage logging:** accumulate `response.usage` (input/output tokens, call
  count) per session; log one JSON line at session end. Groundwork for Spec C
  metering and for validating the cost estimates in this spec.
- **Prompt convention (note, no behavior change):** keep prompts ordered
  static-first (frozen instructions in `system`, volatile transcript last) so
  Anthropic prompt caching lights up automatically if prompts ever exceed the
  2048-token cacheable minimum. Today both prompts are below it, so no
  cache_control markers are added (they would silently no-op).

## Explicitly considered and rejected

- **Semantic caching** (embedding-similarity response reuse): at this scale it
  approximates what a rule-based nudge layer would do exactly, with far more
  infrastructure. Skip; revisit at volume.
- **Prompt caching markers today:** both prompts are under the model's
  cacheable minimum; markers would no-op. Convention above keeps the door open.
- **Batch API:** both calls are user-facing and latency-sensitive. Bookmark
  for future cross-session progress reports (perfect batch workload).

## Deferred to its own spec

- **Deterministic nudge layer:** map detector events (filler streak, WPM,
  pauses) to a canned rotating nudge library served locally in 0ms at $0,
  reserving Claude for content-aware nudges. Biggest cost/latency lever
  (roughly halves nudge LLM calls) but changes coaching behavior, so it gets
  its own design pass.
- Pro tier + Stripe (Spec C), voice-clone consent binding to user identity
  (Increment 2, after Spec C), custom in-app admin dashboard (Supabase Studio
  covers v1 admin needs).

## Error handling

- Invalid/expired JWT: treated as anonymous (never a 500). If a stale token is
  sent mid-session, the sid-user binding check returns 401 and the frontend
  prompts re-auth.
- Anonymous cap tripped: /api/start returns 429 with a friendly reason; the
  frontend shows the sign-in pitch.
- Length timer vs manual stop: the timer task is cancelled on /api/stop and on
  session cleanup; double-stop is a no-op.
- Supabase outage: sign-in unavailable, but anonymous practice still works
  (auth is additive, never load-bearing for the core loop).

## Testing

- `auth.py`: valid/expired/forged/absent tokens (signed with a test secret);
  tier extraction with and without app_metadata.
- `limits.py`: cap trips on IP, cap trips on marker, day-window pruning;
  config cache TTL and env fallback when the config fetch fails;
  `resolve_session_minutes` clamping (free asking 60 gets 15, pro asking 60
  gets 60, pro asking 90 gets 60).
- Endpoints: sid-user binding (mismatched token 401s), anonymous 429 on third
  start, session_limit timer fires and tears down (short monkeypatched limit),
  /api/speak 401/429 gating, debrief memoization returns the same object.
- report.py: structured-output call shape (mocked client), degraded fallback
  unchanged.
- Frontend: existing wiring tests extend (new element ids, JS parses).
- Manual: magic-link + Google sign-in round trip; anonymous 5:00 auto-stop
  shows the sign-in moment; two-tab isolation still holds.
