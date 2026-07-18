# Speakero: Momentum Redesign + Scoring Dashboard

**Date:** 2026-07-13
**Status:** Approved (user-supplied high-fidelity design bundle, "take what is
applicable"); this spec records the scoring model and adaptation decisions.
**Design source:** user's Claude-design bundle (Speakero App.dc.html, Momentum
direction 1c). The .dc.html files are references, not code to copy.

## Scope

1. **Scoring engine** (new `scoring.py` + report schema extension):
   - **Delivery score (0-100), deterministic** from detector stats. Same
     speech always scores the same; zero LLM tokens. Components:
     - filler rate (fillers per 100 words): penalty `min(30, round(rate*6))`;
       clean run yields a positive driver instead
     - pace: ideal band 110-160 wpm; outside penalty
       `min(25, round(distance_to_band * 0.5))`
     - pauses per minute above 4: penalty `min(15, round((ppm-4)*3))`
     - score = 100 - penalties, clamped 0-100
     - fewer than 20 words -> no score (insufficient sample)
   - Each component emits a **driver** `{label, delta}` with the real point
     delta (positive drivers use fixed small bonuses-as-labels: +6 low filler
     usage, +6 steady pace, +4 good pause discipline; deltas are display
     values, not added to the score).
   - **Content score (0-100), LLM-judged** via the existing structured-output
     debrief call (no extra request): new schema fields `content_score`
     (integer), `verdict` (one-line assessment), `content_drivers`
     (array of `{label, positive}`; direction-only, no fake numeric deltas).
   - `/api/report` returns `delivery: {score, drivers} | null` and passes the
     LLM fields through. Degraded reports carry no scores.

2. **Debrief: Dashboard layout only** (Classic variant not built):
   hero row = two conic-gradient ring gauges (DELIVERY green-arc, CONTENT
   amber-arc) -> one-line verdict -> driver chips (delivery chips show +/-n,
   content chips show direction triangle only) -> YOUR MOMENTS as two-column
   strongest/roughest cards -> HOW IT COULD SOUND panel with Hear Example and
   the PRO badge ("PRO - replay in your voice", same click behavior as the old
   upsell row, which is removed) -> strengths/improvements two-column ->
   content feedback -> filler breakdown + delivery flags two-column.
   Ring colors by band: >=75 green, >=50 amber, else coral.

3. **Momentum theme:** oklch token sets (dark default + light), applied by
   redefining the existing CSS custom properties (component rules keep their
   var() references) plus `[data-theme="light"]` overrides; fonts move to
   Space Grotesk (UI) + Space Mono (numerals); dark/light switch in the
   header persisted in the existing settings localStorage object.

4. **Live session, single column** (max-width 600px, centered): status row,
   mode pills + topic (+ pro length), timer card (waveform upgraded to 21
   thin gradient bars, timer, in-card controls), stat tiles row, mic energy,
   live transcript, bottom stats strip (words/clarity/filler rate). Existing
   element ids preserved (wiring tests enforce).

5. **Streak pill** in the header: consecutive-day streak computed client-side
   from a localStorage list of practice dates (recorded on session start);
   hidden below 2 days. Server-truth streak arrives with Spec B.

## Adaptations from the mock (deliberate deviations)

- Timer stays a countdown against the tier limit (caption "remaining");
  the mock's "Elapsed" predates session caps.
- WPM tile keeps the live sparkline.
- Cards remain individual surfaces in one column rather than one mega-card
  (lower regression risk; token unification carries the look).
- Scores/rings hidden when the report is degraded or the sample is too small.
- Classic/Dashboard toggle not built; Dashboard ships as the only layout.

## Testing

- `scoring.py`: unit tests for each penalty branch, clamping, the <20-words
  guard, and driver labels/deltas.
- `report.py`: schema includes the three new fields; normalization unchanged.
- `/api/report`: response carries `delivery` scores; degraded path carries none.
- Frontend wiring tests (ids + JS parse) extend automatically; manual pass for
  theme toggle, rings, and light mode.
