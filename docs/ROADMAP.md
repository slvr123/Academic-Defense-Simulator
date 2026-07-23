# Academic Defense Simulator — Roadmap: v0.2 → v1.0

**Supersedes the "Week 1 Sprint Schedule" section of `academic-defense-simulator-plan.md`.**
The Week 1 schedule completed its job (Days 1–6 done; Day 7 deploy decoupled — see
Deployment Gate below). The "Versioned Roadmap" section of the plan doc is elaborated
and partially re-scoped here; where this doc conflicts with the plan doc, this doc wins
(more recent, more specific), conflict flagged inline per CLAUDE.md.

This document is the standing source of truth for version sequencing. Per-version design
decisions still get their own decision docs (`v{version}-{slug}-{type}.md` after the
pre-publish rename pass; interim `dayN`-style names until then).

---

## Current position

- **v0.2 complete.** Multi-turn agent loop, adaptive difficulty, follow-up branching,
  error handling (5a), eval runs (5b), minimal Streamlit wrap (5c), repo/deploy hygiene
  (Day 6).
- Development model: `gemini-3.1-flash-lite` (500 RPD / 15 RPM free tier);
  `gemini-2.5-flash` reserved for final verification runs. Both per `docs/v0.2-scoring-model-swap-decisions.md`.
- **v0.3a complete.** Logic (Tasks 1–7: panel composition, persona generation +
  fallback, `persona_framing` prompt injection, domain/topic extraction, Streamlit form
  reorder, question-gen probe re-run against the 0.3 templates) and Task 8 (full browser
  session) are both verified — Code run 4 (14-call browser session, full end-to-end)
  corroborated by three of Sean's manual full sessions. The originally-reported
  deterministic hang ("freezes at ~the 6th sequential Gemini call") did not reproduce
  under a dedicated investigation across four evidence-anchored conditions; the founding
  "fixed-count determinism" premise is retired on evidence, not confirmed fixed by a
  root-cause patch. **Gate converted:** "root-caused and fixed" → "bounded and
  observable" — a timeout guard plus thin permanent call-lifecycle logging stand in for a
  root-cause fix as the hard gate before 0.3d and the deployment decision. Full finding:
  `docs/v0.3-hang-investigation-decisions.md`.
- **Pre-deploy hardening session complete** (`docs/v0.3-hardening-decisions.md`).
  Closed the hang-closure carryover (timeout guard, call-lifecycle logging,
  `sleep(13)`→5s trim verified, `build_report` driver wire-up) plus the
  difficulty-4/5 content fabrication fix and Miss 3 (PDF extraction corruption).
- **v0.3b/0.3c/0.3d complete.** Multi-panelist orchestration (structured digest,
  Devil's Advocate, round-robin turn-taking), the scoring report (pure-Python
  aggregates + one narrative call), and the defense-simulation UI (oxblood theme,
  panelist cards, dev-view toggle, report view) all landed and verified per their
  decisions docs.
- **v0.3e complete** (`docs/v0.3e-panel-composition-decisions.md`, LOCKED
  2026-07-13, landed 2026-07-17). `panel_size` retired in favor of direct
  archetype selection (`DefenseProfile.selected_archetypes`, 1–3, schema-validated
  against `PANEL_COMPOSITION`) — the intake multiselect replaces the old count
  input. Uniform 4-total panel cap (max 3 domain archetypes + Devil's Advocate)
  across every defense type.
- **v0.3f complete** (`docs/v0.3f-adaptive-turn-retention-decisions.md`, LOCKED
  2026-07-13, Decision 5 confirmed by Sean 2026-07-17, landed same day). Fixed
  round-robin retired: a panelist who surfaces a weakness keeps the floor for
  capped follow-ups (`MAX_FOLLOW_UPS_PER_TOPIC = 2`); new-topic rotation advances
  only among domain panelists who haven't yet opened their own topic; Devil's
  Advocate fires exactly once, after every domain panelist has spoken, and gets
  the same follow-up treatment as everyone else. Session ends the moment DA's own
  follow-up chain concludes, backstopped by a computed `T_max = 3 × panel size`
  (12, given v0.3e's uniform cap) circuit breaker.
- **Deployed.** I confirmed the deployment gate on 2026-07-17 (deploy at end of
  v0.3, this roadmap's default) and shipped to Streamlit Community Cloud the same
  day - `docs/v0.3-deployment-decisions.md`.
  The link stays quiet until v0.4's key gating ships; my own key sits behind it
  with nothing but the 500 RPD ceiling, so no wide sharing yet.
- **v0.3g complete** (`docs/v0.3g-document-relevance-gate-decisions.md`).
  Document relevance gate at ingestion - one LLM call that rejects uploads the
  panel can't meaningfully examine, before a session ever starts.
- **v0.3h and v0.3i complete** (`docs/v0.3h-i-da-retention-scope-decisions.md` - one
  doc covers both, since the fix grew out of the measurement run).
  v0.3h: call-count instrumentation at the provider boundary, total plus
  per-stage tally stamped into the export payload; a real 4-turn session
  measured 12 calls, matching the stated budget arithmetic (standing
  constraint 2, now verified by instrument rather than by hand).
  That run surfaced the v0.3i bug: the follow-up retention counter was scoped
  to chunk instead of panelist, so Devil's Advocate arriving at another
  panelist's exhausted chunk got no follow-up chain of its own and the session
  ended early - worst against uniformly weak answers, exactly the users the
  app exists to serve.
  Fixed by scoping the counter to chunk plus asking panelist; reproduced and
  verified live.
- **v0.3 is fully closed.** README with hero and intake screenshots is in the
  repo. Next milestone: v0.4 - Depth & Persistence.

## Standing constraints (apply to every session below)

1. **Evidence bar** — per Project Instructions DoD: verified with real output
   (stdout/raw JSON/return values), not summarized. Simulated failure cases state the
   forcing mechanism (mock/monkeypatch/injected fault) and show that output.
2. **RPD budget is a design input, not an afterthought.** Every design doc from 0.3a
   onward states its expected per-session LLM call count. A panel of 3 at 6 turns must
   not silently multiply calls per turn.
3. **Prompt versioning starts at 0.3a.** A `PROMPT_VERSION` constant, stamped into every
   exported session transcript. Rationale: Day 4 proved rubric wording changes silently
   invalidate earlier verification — every eval artifact must record which prompt
   produced it.
4. **UI import boundary.** No business-logic module ever imports `streamlit`. The
   Streamlit layer orchestrates I/O only (already the 5c rule — now standing). This is
   the seam a future FastAPI wrapper slots into; protect it in every session.
5. **Design in chat first; Code sessions implement a locked brief.** Unchanged.

---

## v0.2.5 — Hardening *(1 Code session, mostly RPD-free; start immediately)*

Close open debts before building on them. No new features.

### Scope
**In:** the five items below. **Out:** any rubric, prompt-template, or orchestration
change; any UI work beyond one download button.

### Items

1. **Question-generation re-verification to the Day 3 bar.**
   Open item carried from `docs/v0.2-scoring-model-swap-decisions.md` ("sanity-checked, not formally
   re-verified"). The core mechanic runs on the model already proven to have judgment
   problems in scoring; question generation is a different task class, but that is an
   argument, not evidence. Build a small structured probe script over flash-lite
   question output on the DAZSMA doc: per question, record (a) grounded — is
   `grounding_reference` genuinely from the chunk, (b) in-lane — does the question stay
   in `archetype_lane`, (c) difficulty-appropriate — plausible for the requested level.
   Minimum 6 generated questions across ≥3 distinct chunks. Log raw JSON to a results
   file. This also serves as the deferred re-verification of the Day 4 archetype-lane
   drift fix if 5b's eval didn't already record it — check `docs/v0.2-eval-results.md`
   first; if it's recorded there, cite it instead of re-running.

2. **Programmatic grounding check (standing hallucination detector).**
   `grounding_reference` must be a fuzzy substring of the turn's `chunk_text`
   (normalized whitespace; exact-substring first, fall back to a high-threshold
   `difflib.SequenceMatcher` ratio on the best window). Runs every turn, permanently.
   On failure: log a warning with both strings — do not fail the session (a grounding
   miss is signal to collect, not a crash). Zero LLM calls.

3. **`tests/` directory, pytest.**
   Move the 11 `_should_follow_up` cases into `tests/` if they live anywhere else.
   Add: chunking (paragraph-aware packing, overlap), retrieval exclusion
   (`exclude_indices` honored, top-k ordering), difficulty clamp bounds, and the new
   grounding check from item 2 (both pass and fail fixtures). All pure functions — no
   API calls, no network in tests.

4. **Session transcript export.**
   `st.download_button` on `DefenseSession.model_dump_json(indent=2)`. Stamp
   `PROMPT_VERSION` into the export from day one of its existence (constant can be
   `"v0.2"` for now — the stamping mechanism is what's being built). This is the seed
   of v1.0 analytics and the artifact format for all future eval runs.

5. **Eval-record audit.**
   Confirm `docs/v0.2-eval-results.md` exists in the repo, is committed, and actually
   records the archetype-lane fix re-verification deferred from Day 4. If any of that
   is missing, produce/complete it in this session from the existing
   `eval_run_*.log` files — do not re-run sessions to regenerate evidence that
   already exists on disk.

### Definition of done (v0.2.5)
- Probe results file with raw JSON for item 1 (or a citation into
  `docs/v0.2-eval-results.md` where already covered)
- Grounding check firing on every turn, with a forced-failure test proving the
  warning path works
- `pytest` green, output pasted
- A real exported transcript JSON pasted (or attached) from a live or replayed session
- Existing v0.2 flow unchanged

---

## v0.3 — Panel Simulation *(planned: 4 design days + 4 Code sessions, 0.3a → 0.3d.
Actual: 0.3a–0.3d as planned, plus a pre-deploy hardening session and two
scope-refinement sub-versions, 0.3e and 0.3f, added after 0.3d landed — see
"Current position" above. All complete.)*

The version where the demo starts matching the pitch. Each sub-version gets its own
design day in chat producing its own decision doc before its Code session opens.
**0.3b is the hardest design problem left in the project — budget more than one
sitting for its design day.**

### 0.3a — Dynamic persona generation
- Defense profile → panel composition (which archetypes, how many) → generated
  personas (names, framing).
- **Design questions to lock in chat first:**
  - Composition rule-based from `defense_type` (deterministic, testable, zero LLM
    calls — recommended default) vs. LLM-selected (flag if chosen: adds a call and a
    failure mode for no demonstrated benefit).
  - Persona flavor (names, one-line framing) from one upfront LLM call, cached per
    session, vs. a static name pool. Decide against the RPD budget.
  - Domain/topic auto-extraction from the uploaded document lands here (deferred from
    Day 1) — one LLM call at ingestion, user-editable before session start, never
    silently overriding user input.
- `PROMPT_VERSION` mechanism goes live here if not already stamped in v0.2.5.

### 0.3b — Orchestration architecture *(the centerpiece)*
- **The core question:** what does panelist B know about panelist A's exchange?
  Pattern 2 (`docs/v0.2-agent-loop-decisions.md` §1 — structured state, single-previous-turn context)
  was explicitly scoped to one panelist; multi-panelist breaks it. Options to be
  argued in the design doc, not just picked:
  - Full transcript injection — Pattern 1's dilution failure mode returns
  - Rolling summary — new LLM call per turn, new cost, new failure mode
  - Structured digest — each prior turn's `question` + `primary_gap` + score summary,
    rendered from data the session already holds (token-cheap, no new calls;
    recommended lean, to be validated not assumed)
- **Devil's Advocate is designed here** — its identity is cross-panelist context; it
  was structurally excluded from the single-document-chunk template on Day 1 for
  exactly this reason. It is an orchestration feature, not a fifth config entry.
- **Turn-taking:** round-robin first (deterministic, testable). Score-driven handoff
  ("that answer had a methods gap — Methodology Expert presses next") is a v0.3.x
  upgrade after round-robin is verified, not a launch requirement.
- **Per-session call budget stated explicitly in the decision doc** (standing
  constraint 2). Baseline: only the active panelist generates and scores per turn.
- The 0.3b decision doc doubles as the portfolio's agent-architecture writeup — write
  it to be linkable, not just executable.

### 0.3c — Scoring report
- Aggregates `primary_gap` and `difficulty_delta` patterns across the session;
  "handling pushback" ships here as the aggregate metric deferred on Day 1.
- **Locked approach: Python computes the numbers, one final LLM call writes the
  narrative from those numbers only** — the narrative cannot contradict data it was
  handed. (Both-not-either was the open question; this resolves it. Flag in the 0.3c
  doc if the design day overturns this.)
- **Difficulty trajectory is a first-class report field** — the per-turn
  `difficulty_current` sequence, not just the deltas. Aggregate insights like
  "recovered after escalation" require the trajectory, not summary statistics of it.
- Report renders in UI (0.3d) and lands in the exported transcript JSON.

### 0.3d — UI: defense-simulation aesthetic (within Streamlit)
- Panelist cards (name, archetype, speaking/listening state), turn progress, report
  view. `st.chat_message` for the exchange; custom CSS via config.toml + injected
  styles; `st.fragment` where reruns cause visible jank.
- **Dev-view toggle** (`st.toggle` gating an expander) showing per-turn `AnswerScore`
  — a live debugging window for eval runs. Scores never render in the main flow;
  the "never surfaced mid-session" rule is unchanged and non-negotiable.
- Zero business-logic changes; the import boundary (standing constraint 4) is a DoD
  item for this session specifically.

### Definition of done (v0.3 overall)
- Full multi-panelist session (panel of ≥3) completable in the browser end-to-end
- Devil's Advocate demonstrably referencing another panelist's exchange, with the
  transcript as evidence
- One full eval run against the v0.3 orchestration (scripted weak/strong pattern from
  5b), raw log committed
- Scoring report generated from a real session, numbers traceable to the transcript
- Per-session LLM call count measured and within the budget stated in the 0.3b doc

---

## Deployment Gate — **resolved 2026-07-17: deployed at end of v0.3**

Two positions were on record:
- **My initial position:** postpone deployment to v1.0 — current UI would make a
  weak public demo.
- **Claude's recommendation:** deploy at end of v0.3 — first version where demo
  matches pitch; a live link beats two more versions of polish for a 90-second
  reviewer; deployment surfaces real problems (model-weight cold start,
  `st.cache_resource`, the public-API-key question) better found mid-project than
  at the finish line.

I took the roadmap default and confirmed deploy-at-end-of-v0.3 on 2026-07-17.
Execution and evidence: `docs/v0.3-deployment-decisions.md`.
The post-deploy fixes it surfaced (v0.3g through v0.3i - see Current position)
are exactly the kind of mid-project problem the recommendation predicted, so
I'm counting the call as vindicated.
The API-key design still lands in v0.4 as planned; until it does, the link is
for direct, low-volume use only.

---

## v0.4 — Depth & Persistence *(re-scoped)*

**Supersedes the plan doc's v0.4 (voice I/O, avatars, waveform animation).** Rationale:
high-effort integration work with near-zero AI-engineering signal, against this
project's stated purpose (upskilling in applied AI engineering + portfolio signal for
AI roles). Voice/animation moves to the Parking Lot as optional v1.x garnish.

### Scope
1. **Eval depth — second test document.** The entire evidence base currently rests on
   one PDF (DAZSMA). Run the full eval suite against a second document; the ideal
   candidate is Sean's own thesis draft when it exists (ESP32-CAM + Fast-SCNN gives
   the Technical Implementation Reviewer real material — and doubles as actual
   defense prep). Findings logged to the same standard as 5b.
2. **Session persistence.** Sessions survive refresh; past sessions reloadable.
   `st.session_state` → JSON-on-disk first; SQLite only if reload/query patterns
   demand it (avoid unnecessary dependencies — earn it). This is the prerequisite
   for v1.0 analytics. The v0.2.5 transcript export format is the starting schema.
3. **Public API-key design.** A public deploy means strangers burn the 500 RPD.
   Ship: user-supplied Gemini key field with a validation call, plus a rate-limited
   demo mode on the project key (small fixed session cap). Keys never logged, never
   persisted server-side beyond the session.

### Definition of done (v0.4)
- Second-document eval results committed, same evidence bar as 5b
- Kill the app mid-session, restart, resume — shown working with real output
- Demo-mode session cap demonstrably enforced (forced-exhaustion test, output shown)

---

## v1.0 — Public Polish

The version where the deployment gets *good* (or gets *done*, if the v0.3 gate was
overridden).

### Scope
1. **Session history + analytics across runs** — the persistence layer pays off:
   per-session summaries, difficulty trajectories over time, recurring
   `primary_gap` themes across practice runs.
2. **README + case study.** Written around the strongest real finding — the
   scoring-model eval story (`difficulty_delta` as concession detector, flash-lite
   unfit for judgment tasks, model-independent rubric rewrite) — with
   RAG-for-question-generation as the frame and hook. Architecture section explains
   RAG-for-questions, structured-output scoring, and the Pattern-2 → digest
   orchestration transition. One recorded demo GIF/video embedded.
3. **Deployment hardened.** Cold-start behavior (`st.cache_resource` on the
   embedding model), memory footprint on Streamlit Cloud verified, error states
   audited end-to-end in the deployed environment (not just locally).
4. **Pre-publish rename pass.** All decision logs renamed to
   `v{version}-{slug}-{type}.md` per the standing naming plan. One pass, one commit.
5. **LinkedIn/visibility writeup** — after everything above, never before.

## v1.0 scope addition — answer suggestions (2026-07-22)

v1.0 originally scoped analytics only (session 1.0b). Added during design: v1.0b-2,
grounded per-answer suggestions at session end (new gemini-2.5-flash call, reopens
v0.3c's numbers-only-narrative principle for this one new call site only — narrative
itself is untouched). Both ship in one combined Code session per Sean's call.
Docs: v1.0b-analytics-decisions.md, v1.0b-2-answer-suggestions-decisions.md.

### Definition of done (v1.0)
- Live public URL completing a full session for a first-time visitor with no setup
  beyond (optionally) pasting their own key
- README's architecture section reviewed against the actual code — no claims the
  code doesn't back
- Analytics view rendering from ≥3 real persisted sessions

---

## Parking Lot *(explicitly not scheduled; revisit only at their trigger)*

- **Stack swap — FastAPI + React (Vite, Tailwind, Framer Motion), game-simulator
  aesthetic.** Trigger: post-v1.0, and only as a deliberate new chapter — the
  experience becomes defined by motion/state-transitions (panelist demeanor states
  driven by `difficulty_delta`, SSE-streamed question reveal), which Streamlit's
  rerun model cannot do. Pydantic models become the API contract; the import
  boundary (standing constraint 4) is what makes this a wrap, not a rewrite.
  Estimated 3–6 weeks, mostly frontend work — a knowing trade of AI-signal time for
  full-stack-product signal. Gets its own design day(s) if triggered.
- **Voice I/O** — v1.x garnish at most, after the core is done. Zero AI-engineering
  signal; do not let it compete with orchestration or eval work for time.
- ~~Score-driven turn-taking — v0.3.x, only after round-robin is verified in
  evals.~~ **Shipped as v0.3f** (adaptive turn retention: follow-up floor
  retention + gated new-topic rotation) — no longer parked.
- **Retriever interface / vector-DB swap** — unchanged from Day 1: revisit at v1.0
  or when a second real implementation exists, whichever comes first.
- Production scoring runs on gemini-3.1-flash-lite despite the documented
  judgment-fitness finding — a deliberate tradeoff: gemini-2.5-flash's
  free-tier RPD (~20/day) cannot cover even one full session on a shared key,
  and a dead demo is worse than soft scoring. Revisit at v1.0, on the BYO-key
  design, or if a better model with generous RPD ships — whichever comes
  first.

---

## Known issues *(logged, not scheduled — pull into a session's scope when it blocks something)*

### Known issue — env-var test isolation defeated by real `.env` values (logged 2026-07-22)

`load_settings()` calls `load_dotenv()` at call-time (by design, so env changes
take effect without a process restart — the same pattern the demo-cap env
override now uses). `load_dotenv()` fills in any variable *absent* from the
process environment from the real `.env` file on disk; it does not override
one already set.

Consequence: `monkeypatch.delenv(VAR)` only removes VAR from the in-memory
process environment for that test. If my real local `.env` file also defines
VAR, the very next `load_settings()` call re-populates it from disk — the
monkeypatch deletion is silently undone by the function under test. Confirmed
on `test_persistence_defaults_off_when_unset`, reproduced identically on clean
`main` via `git stash` (not introduced by the 2026-07-22 demo-cap amendment).

Risk scope: any test using `delenv` to verify a default, on a variable that
also has a real value in my local `.env`, is exposed to this — pass/fail
becomes dependent on my machine's `.env` contents, not just the code. Not yet
audited across the full suite for other instances.

Not fixed here — logged for its own scoped session. Candidate fixes to weigh
then: monkeypatch `load_dotenv` itself in these tests rather than the
individual env var; point affected tests at an isolated/empty `.env` path;
or have `load_settings()` accept a flag to skip dotenv loading under test.

### Known issue — dev-hot-reload can invalidate a live session's persisted save (logged 2026-07-23)

Editing a file in `DefenseSession`'s import chain (`models/session.py` or
anything it imports) while a session already lives in `st.session_state`
triggers Streamlit's local dev-server file watcher to reload the changed
module mid-process. That produces a second `DefenseSession`/`PersistedSession`
class pair with the same `__module__`/`__qualname__` as before but a different
`id()`. Pydantic validates nested `BaseModel` fields by class identity, so the
next save attempt raises a `ValidationError` on `PersistedSession`'s `session`
field — confirmed with a real reproduction via `importlib.reload()`.

Confirmed dev-only: Streamlit Community Cloud restarts the whole process on
deploy rather than reloading a module inside a live one, so `st.session_state`
never survives across a real redeploy in a way that could hit this.

Not root-cause-fixable — it's inherent to Python module reload plus Pydantic's
identity-based validation, not something this app's code can prevent. Mitigated
this session (not eliminated): `_persist` now catches this `ValidationError` in
its own branch, distinct from the existing `OSError` handling, and degrades to
the same non-fatal "save skipped, session continues" behavior with a caption
naming the actual cause. See `v0.4b-session-persistence-decisions.md`'s
amendment for the full diagnosis and reproduction.

---

## Sequencing summary

| Milestone | Sessions | Gate to next |
|---|---|---|
| v0.2.5 Hardening | 1 Code session | All five items at evidence bar |
| v0.3a Personas | 1 design day + 1 Code | Decision doc locked before Code opens |
| v0.3b Orchestration | 1–2 design days + 1 Code | Call budget stated; digest design validated |
| v0.3c Report | 1 design day + 1 Code | Numbers-then-narrative verified against a real session |
| v0.3d UI | 1 design day + 1 Code | Import boundary intact; full browser session |
| Pre-deploy hardening | 1 Code session | Hang-closure carryover + difficulty-4/5 fabrication fix closed |
| v0.3e Panel composition | 1 design day + 1 Code | Schema validator + `compose_panel` tests green; uniform 4-total cap |
| v0.3f Adaptive turn retention | 1 design day + 1 Code | Termination logic tested at shortest/longest case; live session verified |
| **Deployment gate** | 1 Code session (taken 2026-07-17) | Deployed to Streamlit Cloud, verified live |
| v0.3g Relevance gate | 1 Code session | Off-topic upload rejected at ingestion, verified live |
| v0.3h/i Instrumentation + DA fix | 1 Code session | Call count matches budget; DA retention rescoped, reproduced then verified |
| v0.4 Depth | 2–3 Code sessions | Second-doc eval + persistence + key design done |
| v1.0 Polish | 2–3 sessions + writing | Live URL + README + analytics |
