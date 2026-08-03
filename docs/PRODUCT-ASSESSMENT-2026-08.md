# Speakero Product Assessment and Improvement Plan

> Date: 2026-08-02
> Status of repo at assessment: all 5 PRs merged to main (auth/limits Spec A, UX polish, Pulse fixes, Momentum redesign + scoring dashboard). Not yet deployed publicly. Spec B (persistence), Spec C (Stripe), voice-clone Increment 2 queued.

## 1. Where the product actually stands

Strong foundation, invisible product. The engineering is ahead of the go-to-market:

**Working and merged:**
- Multi-tenant single-process backend (per-sid sessions, reconnect grace, room-scoped events)
- Supabase auth (magic link + Google) with tier-aware, config-driven limits that fail open
- Deterministic delivery scoring + structured Claude debrief with degraded-mode fallback
- Real-audio replay of best/worst moments
- TTS LRU cache, debrief memoization, per-session usage logging
- 60 pytest tests, Dockerfile, fly.toml, render.yaml

**Missing for "marketable":**
- No public deployment. Nobody can use it.
- No persistence: sessions vanish on refresh. No history, no progress-over-time. For a coaching product, progress trends ARE the retention loop and the thing people pay for.
- No billing.
- No CI, no error tracking, no analytics, no health endpoint, print() logging only.
- No landing page, demo video, or any acquisition surface.
- Provider risk: smallest.ai SDK already broke once (retired TTS models forced a raw REST workaround in tts.py). The stt_provider/tts_provider abstraction is still pending.

## 2. Recommended sequence (revised priorities)

The CLAUDE.md priority list puts the provider abstraction first. Recommend reordering: ship first, because everything else compounds off a public URL.

### Phase 0 - Ship it (days, not weeks)
1. Deploy to Fly.io (fly.toml exists). Set auto-stop to min 1 machine: WebSocket sessions die on scale-to-zero, and cold starts kill the first-visit demo.
2. Add `/healthz` endpoint + Fly health checks.
3. GitHub Actions CI: pytest on every PR. Add the badge to README.
4. Sentry free tier for backend + frontend errors (voice pipelines fail in ways users never report).
5. Tighten CORS from `*` to the real origin.
6. Recording-consent copy: a visible "your audio is processed for transcription, not stored" line. Voice is sensitive data; this is both a legal and a trust issue.

### Phase 1 - Retention core (Spec B, expanded)
- Supabase persistence of sessions: transcript, stats, scores, debrief JSON.
- History UI + progress chart (filler rate, WPM, delivery score over time). This is the screenshot people share and the reason to come back.
- In-app feedback widget (one thumbs-up/down + text box on the debrief, written to a Supabase table). Cheapest possible user-research pipeline.
- PostHog (free tier) for funnel analytics: visit -> mic grant -> session start -> debrief viewed -> signup.

### Phase 2 - Cost/latency hardening (before charging money)
- `stt_provider.py` + `tts_provider.py` abstraction (already priority; now justified by the SDK breakage and by cloning needs).
- Deterministic nudge layer (already spec'd as the biggest cost/latency lever): rule-triggered canned nudges ("slow down", "watch the filler streak") synthesized once, shipped as static audio, zero API cost, ~0ms latency. Claude nudges become the fallback for content-level coaching only.
- Prompt caching was evaluated and cut (2026-08-03): Anthropic's minimum cacheable prefix is 4096 tokens on Haiku 4.5 and 2048 on Sonnet 4.6, and Speakero's static prompt blocks are a few hundred tokens, so cache_control would silently never engage. The deterministic nudge layer is the cost/latency lever instead.
- Stream the debrief (progressive render) instead of one blocking JSON call.
- Move the TTS cache key set to disk or Supabase storage if ever multi-instance; in-memory is fine for one machine.

### Phase 3 - Monetization (Spec C)
- Stripe subscription for Pro. Keep it boring: Checkout + customer portal + webhook that flips `tier` in Supabase.
- Pro features: longer sessions (already tiered), history beyond 7 days, PDF export, voice-clone replay (below).

### Phase 4 - The wow feature (voice clone Increment 2)
- "Hear yourself, perfected": worst moment rewritten by Claude, synthesized in the user's own voice. This is the demo-video moment and the Pro conversion driver.
- Requires explicit consent checkbox and ephemeral voice deletion (create -> synth -> delete in one request path). Never store voice prints.

### Phase 5 - Distribution / reputation
- Landing page with a 30-second demo video or GIF above the fold.
- Niche the messaging: "interview answer practice" converts better than generic "speaking coach". Job seekers have urgency and budgets; pick panel-interview mode as the hero use case.
- Launch: Show HN, X thread, r/interviews / r/publicspeaking, a technical blog post ("real-time voice coaching with FastAPI, sockets, and Claude" - the architecture is genuinely interesting and is itself reputation currency).
- Clean public README with architecture diagram and test/CI badges.

## 3. Voice model landscape (August 2026)

Current stack is fine to launch with; the abstraction layer is what matters. Candidates when swapping:

### STT (streaming)
| Provider | Notes |
|---|---|
| smallest.ai Pulse (current) | Working; usage-based Waves pricing; keep until abstraction exists |
| Deepgram Nova-3 / Flux | Industry default for voice agents; Flux has lowest end-of-speech latency (May 2026); ~$0.46/hr streaming |
| AssemblyAI Universal-3 Pro Streaming | ~$0.45/hr, strong accuracy (7.0% WER class) |
| ElevenLabs Scribe v2 Realtime | ~150ms first partial, 90+ languages |
| Speechmatics Melia-1 | Best aggregate WER (6.4%), good if multilingual later |

### TTS (low latency)
| Provider | Notes |
|---|---|
| smallest.ai Lightning v3.1 (current) | $0.0135/1k chars; SDK unreliable, REST direct works |
| Cartesia Sonic 3 | ~40ms latency, ~$0.05/1k chars, instant voice cloning: the documented clone provider, best fit for Increment 2 |
| Deepgram Aura-2 | $0.030/1k chars, ~90ms; cheap and fast, no cloning story |
| ElevenLabs Flash v2.5 | ~75ms, ~$0.05/1k chars, best voice variety |

### SLM for nudges (30s cadence, 64 output tokens)
- Current Haiku 4.5 is appropriate; at this cadence TTFT is not the bottleneck, TTS+playback is.
- If cost pressure appears: Gemini 2.5 Flash-Lite, or open models (Llama 3.1 8B, Qwen3-8B, gpt-oss) on Groq/Cerebras give sub-100ms TTFT at near-zero cost. Keep Claude for the debrief where quality shows.
- The deterministic nudge layer beats all of these: most nudges do not need an LLM at all.

## 4. Unit economics sanity check (rough)

15-minute free session, current stack:
- STT: ~15 min at ~$0.40/hr class pricing = ~$0.10
- Nudges: up to 30 Haiku calls, tiny token counts = ~$0.01-0.02
- Nudge TTS: ~3k chars = ~$0.04 (less with cache + canned nudges)
- Debrief (Sonnet, structured) = ~$0.03-0.06
- **Total: roughly $0.15-0.25 per 15-min session**

Implication: a free tier of 8 sessions/month costs ~$1-2/user/month; a $10-12/mo Pro tier with 30-min sessions holds ~70-85% gross margin if the deterministic nudge layer lands. The existing per-session usage JSON logs should be persisted to Supabase in Spec B so this stops being an estimate.

## 5. Considerations not yet on the roadmap

1. **Privacy/legal**: privacy policy + ToS pages before charging money. Voice cloning needs explicit consent and provable deletion. Audio should be documented as transient (it currently is - only transcripts are kept, which is a selling point).
2. **Browser coverage**: test Safari/iOS mic capture explicitly (AudioContext sample-rate and autoplay rules differ). Mobile is likely half the interview-prep audience.
3. **Scaling model**: SESSIONS dict and anon rate counts are in-memory, so the app is single-instance by design. Fine for a long time on one Fly machine; document it, and revisit only if concurrent sessions exceed one box (STT bridging is I/O bound, hundreds of concurrent sessions per instance is plausible).
4. **Zombie session cost**: a tab left open keeps STT streaming. The per-tier session caps already bound this, but add a silence timeout (e.g. no final transcript for 3 min -> auto-stop) to stop paying for dead mics.
5. **Repo hygiene**: AGENTS.md and .claude/launch.json are untracked (commit or ignore); `Raw Dumps/` should be gitignored; PRESENTATION.md is hackathon-era and should be replaced by the landing copy.
6. **Accessibility**: the product coaches speech; captions/transcript-first UI is both accessible and a feature.

## 6. Success metrics to instrument from day one

- Activation: % of visitors who complete one session and view the debrief
- Retention: % who return for a 2nd session within 7 days
- Conversion: free -> Pro after hitting a limit
- Cost: per-session cost from usage logs vs. tier pricing
- Quality: thumbs-up rate on debriefs, nudge mute rate (if users mute the coach, nudges are annoying, and that is the core product signal)
