# Academic Defense Simulator — Day 5 Decisions + Code Session Brief

Covers the three remaining Day 5 deliverables per the original plan: error handling,
eval verification, and a minimal Streamlit wrap. Builds on `day1_decisions.md`
(schemas, prompts, rubric), `day3_decisions.md` (agent loop), and `day4_decisions.md`
(model swap, rubric rewrite, branching gate — confirmed closed, no open blockers
carrying into Day 5).

**Recommended split: three separate Code sessions (5a → 5b → 5c), in that order.**
Error handling must land before eval runs — an unhandled crash mid-session would
corrupt the eval evidence, not just fail loudly. Eval should complete before the
Streamlit wrap, since that's the first time the full loop gets human-tested on a
new I/O surface and you want the underlying loop already proven solid.

Each section below is written to be pasted as the opening brief for its own Code
session.

---

## Session 5a — Error Handling

### Scope
**In:** the three failure classes below, handled at existing module boundaries.
**Out:** no rubric changes, no new scoring fields, no retry logic beyond what's
specified here.

### Failure classes

| # | Failure | Detection | Behavior |
|---|---|---|---|
| 1 | Malformed / unreadable document upload | PyMuPDF raises on open/extract, **or** extraction succeeds but total extracted text < 500 characters across the whole doc | Fail **before** chunking/embedding starts. Raise a specific, readable error identifying which check failed. No retry — bad input, not a transient fault. |
| 2 | Empty / blank user answer (terminal input) | `answer.strip() == ""` | Re-prompt in place, same turn. Do not send blank input to Gemini for scoring — wastes a call, gives meaningless signal. Does not count as a used turn. Cap at 3 consecutive blanks, then let it proceed to scoring as a genuine non-answer (a candidate going blank 3 times running is itself real signal). |
| 3 | Gemini API failure — timeout | Request exceeds timeout | Retry once with short backoff (~2s), then **fail the session cleanly** with a clear message. Do not silently skip the turn. |
| 3 | Gemini API failure — malformed/unparseable JSON | Schema validation fails on response | Retry once (re-issue same call), then fail session cleanly, same as above. |
| 3 | Gemini API failure — unexpected 429 despite confirmed pacing | 429 received after the `time.sleep(13)` pacing already verified in Day 4 | **No retry.** An unexpected 429 here means real quota exhaustion, not a pacing bug — retrying burns quota that's already gone. Fail session cleanly, message telling you to check quota / wait for reset. |

**Rationale for "fail session" over "fail turn and continue":** a defense session
that silently drops a question or a score isn't a defense anymore — it's a
corrupted transcript that looks complete but isn't. Better to stop clean and let
you re-run.

**Where it lives:** wrap failure classes 3 at the LLM provider abstraction
boundary (the single call-site isolation from Day 1) — not scattered
try/excepts inside the `main.py` loop. Failure class 1 lives in the ingestion
step, not inside `retrieve()`.

### Definition of done (5a)
- All three failure classes reproducible on demand (you can force each one and
  see the intended behavior, not a raw traceback)
- Existing v0.2 happy-path flow unchanged
- No new dependencies

---

## Session 5b — Eval Verification

### Scope
**In:** scripted weak/strong answer script, 2 new full 6-turn runs, persona
consistency check, redundancy check (Day 3 §9).
**Out:** no rubric changes. If eval surfaces something new, log it as a finding —
don't quietly patch `SCORING_SYSTEM_PROMPT` mid-session; that's Day 4's door and
it's already closed.

### Test document
Reuse the same DAZSMA capstone PDF from Day 2 — keeps this eval comparable
against `day2_verification.md` and `day4_decisions.md` instead of introducing a
new unknown variable.

### Scripted answers
Pre-write answers before the run. An improvised answer isn't repeatable
evidence. Example pair, tied to the actual Day-2-verified turn-1 question
(whether 20 respondents supports a "holistic evaluation" claim across 5
criteria):

**Weak (vague, no numbers, no commitment):**
> "We felt 20 was a reasonable number given our timeline, and the results
> seemed to support what we expected, so we think it's fine for a project of
> this scope."

**Strong (specific, defends the choice, names a real limitation):**
> "20 was chosen via purposive sampling — 10 IT, 10 non-IT — to capture
> usability perception differences by technical background. That's in line
> with common practice for surfacing major usability issues in mixed-methods
> testing, though I'll acknowledge it limits statistical generalizability,
> which is why we present the evaluation as descriptive rather than
> inferential."

Later-turn questions depend on what the model retrieves live, so they can't be
fully scripted ahead of time — draft weak/strong answers *in the moment* using
the same pattern (vague-and-numberless vs. specific-and-defends-a-limitation),
not purely improvised.

### Runs
- Minimum 2 full 6-turn sessions, in addition to the 1 organic run already
  logged in `day4_decisions.md` — total evidence across Day 4 + 5 = 3 runs.
- Save raw stdout per run to a file (`eval_run_1.log`, `eval_run_2.log`). Per
  your own standing rule: self-reports need real command output, not a
  summarized "it worked."

### Checks per run (record pass/fail + evidence, not just a verdict)
1. **Difficulty adaptation** — does `difficulty_current` move up after a
   weak-scripted answer, and down/hold after a strong one, consistent with
   `difficulty_delta`?
2. **Persona consistency** — does "Dr. Reyes, Methodology Expert" hold tone and
   character through turn 6, or drift toward generic-assistant voice? Does it
   stay in `archetype_lane` (methodology) rather than wandering into
   literature/ethics territory?
3. **Redundancy** (the risk flagged and deliberately deferred in
   `day3_decisions.md` §9) — do any two turns' `grounding_reference` values
   overlap conceptually even when `chunk_index` differs? This is the first
   real data against a risk that was previously untested speculation.

### Output
A short `day5_eval_results.md` — raw findings per run, not a rewritten
narrative. If redundancy or persona drift shows up, log it as a new finding,
don't silently fix it inside this session.

---

## Session 5c — Minimal Streamlit Wrap

### Scope
**In:** thin UI layer over existing, unmodified business logic.
**Out:** panelist cards, avatars, styling polish (all v0.3+), any change to
`retrieve()`, prompt templates, or scoring logic.

### Structure
- Single page. Form: `defense_type` (select), `other_subtype` (conditional,
  only if `defense_type == other`), `domain` (text), `topic` (text).
  `panel_size` and `difficulty_start` stay hardcoded/hidden per existing Day 1
  app logic — not exposed as UI controls yet.
- File upload widget for the PDF.
- "Start Session" button → runs ingestion + first turn.
- Turn view: question text, `st.text_area` for the answer, submit → next
  question, looped via `st.session_state`. Reuse `DefenseSession` as-is —
  persisted in `st.session_state` instead of a local variable, no new state
  model.
- End state: simple "Session complete — N turns" message. No scoring report UI
  (that's v0.3).
- Error handling from 5a must surface as `st.error()`, not a raw traceback —
  both the malformed-doc and API-failure paths need a clean stop state here too.

### Definition of done (5c)
- Full 6-turn session completable start-to-finish through the browser
- Zero changes to `retrieve()`, prompt templates, or scoring logic — the
  Streamlit file only orchestrates I/O
- 5a's error paths are visibly handled in the UI, not just the backend

---

## Standing rules (apply to all three sessions)
Readable over clever; composition over inheritance; small single-purpose
functions. Business logic separate from UI; RAG separate from LLM logic;
prompts separate from Python. No global mutable state; Pydantic for structured
data; provider-specific code isolated.

**Definition of done, every session:** works, understandable, type-safe,
existing functionality intact, temp debugging code removed.

**Verified with real evidence, not summarized.** For each item in the brief,
show actual output — command output, stdout, raw JSON, or return values — not
just a description of what happened. For cases that can't be triggered live
(rare API errors, edge-case failures), state exactly how they were forced
(mock, monkeypatch, injected fault) and show the output from that forced run.
A task is not done if "verified" isn't backed by something pasteable.

---

## Open defaults flagged for your confirmation before this goes to Code
1. Blank-answer re-prompt cap — defaulted to **3**, then proceeds as a
   non-answer. Change the number if you want.
2. "Fail session" (not "fail turn and continue") on all three Gemini API error
   subcases — defaulted this way because a defense session with a silently
   skipped question isn't a real defense anymore. Flag if you want a
   fail-turn-and-continue path instead.
3. 500-character threshold for "unreadable document" — arbitrary, set your own
   number if you have a better sense of what a real corrupt/empty PDF looks
   like in practice.
