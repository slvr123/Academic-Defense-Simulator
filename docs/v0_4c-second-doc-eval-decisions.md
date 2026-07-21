# v0.4c — Second-Document Eval + Difficulty-Tone Probe: Decisions & Code Brief

**Status:** Locked by Sean 2026-07-20. Decision 1 resolved directly by Sean
(document choice + permission). Decisions 3's pass bar, 3's 1-vs-4 contrast
choice, and 0's sequencing confirmed under Sean's explicit delegation
("lock all decisions as you say") — Claude's proposed values, as drafted,
stand as locked. Provenance recorded inline at each point below.

**Scope in one line:** the entire evidence base rests on one PDF (DAZSMA). v0.4c
runs the eval machinery against a second document, and closes the open
difficulty-tone hypothesis from the 0.3d live-testing findings with real evidence
instead of a guess.

**What v0.4c is not:** no prompt, rubric, orchestration, retrieval, or UI change of
any kind. `PROMPT_VERSION` does not move. This session *produces evidence*; any fix
the evidence motivates is its own future decision with its own re-verification.

---

## Decision 0 — Sequencing and gates 

- Runs **after v0.4b (session persistence) lands and closes review**, per the
  standing v0.4 ordering. Noting for the record: v0.4c has *no technical
  dependency* on v0.4b — the v0.2.5 transcript export is sufficient as the
  evidence artifact — so if v0.4b's Code session stalls, v0.4c can be pulled
  forward without redesign. Claude's recommendation: keep the current order;
  don't reorder without a reason.
- **Not gated on the Streamlit Cloud `ImportError` fix.** Every v0.4c run is
  local (CLI driver or local Streamlit). The deployment fix stays its own
  parallel track.
- The thesis-draft gate is resolved by Decision 1, not left implicit.

---

## Decision 1 — Second document selection (RESOLVED — `sample3`)

**Resolved: the second document is `sample3`** — *Design of a Wearable
TDOA-Based Sound Source Localization System for Assistive Spatial
Awareness*, a TIP Electronics Engineering capstone (Bartolome, Castillo,
Josef, Lopez, Sales, 2026), already in the repo. This section is kept below
largely as the design record of how that was decided — the roadmap's
original candidate was my own thesis draft (ESP32-CAM + Fast-SCNN), which
would have given the Technical Implementation Reviewer real material and
doubled as actual defense prep. A usable draft didn't exist yet, so the gate
below resolved to its fallback branch instead.

**Gate question I must answer before the Code session:** does a usable thesis
draft PDF exist yet?

- **If yes:** it is the second document. Minimum bar for "usable": has at least
  an introduction, a methodology/architecture section, and some implementation
  or preliminary-results content — enough distinct material for ≥3 distinct
  chunks per archetype lane. A rough draft is fine; a title page and outline is
  not.
- **If no:** I supply a fallback PDF meeting these criteria, and the thesis
  re-run moves to the v1.0 backlog (it stays valuable as defense prep even
  after v0.4c closes):
  - Public academic paper or capstone document, roughly 8–30 pages
  - **Different domain than DAZSMA** — the point is generalization evidence,
    not a second sample from the same distribution
  - Technical/engineering content present, so the Technical Implementation
    Reviewer and Methodology Expert both have real material
  - Text-based PDF (PyMuPDF-extractable), not scanned images

The chosen document's filename and a one-line description get recorded in the
results file header — future readers must know what the evidence was run
against.

**Fallback criteria check:** different domain from DAZSMA (acoustic SSL /
embedded DSP / haptics vs. DAZSMA's domain), strong technical content
(GCC-PHAT math, hardware architecture, TDOA algorithm, defined evaluation
criteria and target metrics), text-extractable. **Deviation noted:** runs
well past the ~8–30 page guideline (TOC alone to page 76, plus references)
— not disqualifying since RAG chunking doesn't care about total length and
the extra content only helps the ≥3-distinct-chunks requirement, but
flagged as a stated deviation rather than silently absorbed. My thesis
draft moves to the v1.0 backlog per the fallback branch's stated handling.

---

## Decision 2 — Eval scope: three stages, all against the current production configuration

The "full eval suite" from the roadmap, made concrete for the post-0.3f system.
Everything runs the **production config as deployed**: flash-lite for
generation *and* scoring (the documented free-tier tradeoff), gemini-2.5-flash
only where production already uses it (the relevance gate). The eval measures
what users actually get — not the ideal config.

**Stage A — Ingestion + relevance gate.** Ingest the second document; paste the
real `DocumentAssessment` JSON (is_defense_material / document_kind / reason)
and the chunk count. This is the gate's first exercise against a document it
wasn't developed on.

**Stage B — Question-generation probe.** Reuse `scripts/probe_question_gen.py`
against the new document:

- New-topic path: minimum 4 questions across ≥3 distinct chunks
  (`exclude_indices` forcing variety), difficulty levels 2 and 3.
- Follow-up path: minimum 2, forced via the standing scripted-weak-answer
  mechanism; both `framing_case` values exercised if the harness supports it
  cheaply, same-asker only if not (the colleague case was verified in 0.3d and
  is not the variable under test here).
- Archetype: `technical_implementation_reviewer` this time, not
  `methodology_expert`. Rationale: every prior probe row is methodology-expert
  on DAZSMA; a second doc probed with the same archetype tests the document
  axis only. Switching archetype on the new doc is the strongest
  generalization signal per call. (The methodology lane still gets exercised
  in Stage C's live session.)
- Per row: existing fields, `is_grounded()` boolean + ratio, human-judged
  `in_lane` / `difficulty_ok` nulls for me to fill. No LLM grading LLM —
  standing rule.
- Results: `scripts/probe_question_gen_v0.4_<docslug>_results.jsonl`,
  committed once human columns are filled. Never overwrite prior probe files.

**Stage C — One full live session, scripted answer plan.** A complete session
on the new document, full panel (3 domain archetypes + DA), driven by me with
a *pre-scripted answer-quality sequence* — not improvised — so the difficulty
trajectory is interpretable:

1. Strong, specific answer (expect: no follow-up, difficulty holds/climbs)
2. Fluent hedge — topic-adjacent, no commitment (expect: `difficulty_delta`
   +1 per the Day-4 concession-detector semantics, follow-up likely)
3. Genuine non-answer ("I'm not sure, I'd have to come back to that")
   (expect: `difficulty_delta` -1 — the one case that eases)
4. Remaining turns: strong answers, letting ATR/DA termination fire naturally

The point: Day 4's concession-detector finding was established on DAZSMA with
gemini-2.5-flash as scorer. Stage C checks whether the *production* scorer
(flash-lite) on a *new* document still moves `difficulty_delta` in the right
direction on the two unambiguous cases (hedge → +1, non-answer → -1). Known
caveat, stated up front: flash-lite was found unfit for judgment tasks on Day
4's probe — Stage C is measuring how that documented deficiency behaves in
production conditions, not re-litigating the model-split decision. If it
inverts again, that's confirmation logged as a finding, not a surprise.

Deliverables: full transcript export JSON committed (or persisted-session file
if v0.4b's format has landed — whichever is the live mechanism), generated
`DefenseReport`, and the per-turn difficulty trajectory pulled from the export.

**Stage D — Findings doc.** `docs/v0_4c-second-doc-eval-results.md`, same
standard as `day5_eval_results.md`: what was run, raw-evidence pointers,
pass/fail per stage, divergences from DAZSMA behavior called out explicitly.

---

## Decision 3 — Difficulty-tone probe: paired, same-chunk, blind-judged
(LOCKED — Claude's proposal, confirmed under Sean's delegation 2026-07-20:
the 1-vs-4 maximum-contrast design over Sean's original 1–2 vs. 3–4 band
framing, and the ≥3/4 blind-identification pass bar, both as drafted below)

This closes 0.3d live-testing finding #2(b): the hypothesis that
`PANELIST_SYSTEM_PROMPT` does not meaningfully differentiate *tone* between
difficulty 1–2 and 3–4, which would explain "sharp even early" independent of
escalation speed. Untested until now; this is the probe that finding explicitly
deferred to v0.4.

**Design — paired generation:**

- New script: `scripts/probe_difficulty_tone.py`
- For each pair: same document, same chunk, same archetype, same persona, same
  template — the *only* variable is `difficulty_level`: **1 vs 4**. Maximum
  contrast; if tone doesn't differentiate at 1-vs-4, it certainly doesn't at
  2-vs-3.
- **4 pairs minimum: 2 archetypes × 2 chunks** (methodology_expert +
  technical_implementation_reviewer — the two lanes with the most prior
  evidence and the clearest tonal expectations). 8 generation calls.
- New-topic path only. Follow-up tone is confounded by the
  prior-answer acknowledgment instruction; it's a different question for a
  different probe if this one motivates it.
- Difficulty 4 rows get `is_grounded()` run and logged — the difficulty-4
  fabrication history (chunk 70) means any grounding miss here is a
  first-class finding on the new document, not noise.

**Judgment — blind, paired:**

- The script emits two files: a *judging file* with each pair's two questions
  in shuffled order, labels stripped (pair_id + question A/B only), and a
  separate *key file* mapping A/B → difficulty. I judge blind, per pair:
  "which of these two reads as the harder/sharper question — A, B, or
  indistinguishable?" plus a free-text note. Then unblind against the key.
- Why blind: knowing which question was requested at difficulty 4 biases the
  read toward finding sharpness in it. This is standard eval hygiene and
  costs one shuffle. (Upskilling note: this is the same reason human evals of
  model outputs use blinded A/B comparisons — expectation contaminates
  judgment, and the fix is structural, not willpower.)
- **Pass/fail bar:** the hypothesis "tone differentiates" survives only if I
  correctly identify the difficulty-4 question in **at least 3 of 4 pairs**.
  2/4 is chance on a binary choice; "indistinguishable" counts against.
  Judged results appended to the JSONL
  (`scripts/probe_difficulty_tone_results.jsonl`, committed).

**Outcome handling — evidence only, pre-committed:**

- **≥3/4 distinguishable:** hypothesis (b) is rejected — tone does
  differentiate; "sharp even early" traces to escalation speed (the Day-4
  deliberate adversarial bias) and/or `difficulty_start`, both already
  understood. No prompt work motivated.
- **≤2/4:** hypothesis (b) is confirmed — the template ignores the numeric
  difficulty target tonally. The *fix* (template wording, difficulty-anchored
  tone descriptors, whatever) is explicitly **not** v0.4c scope: it's a
  prompt change, which means a `PROMPT_VERSION` bump and full probe
  re-verification per the Day-4 rule — its own design section, likely v0.4d
  or v0.5.
- Either way the result is recorded in Stage D's findings doc. Deciding the
  interpretation rule *before* seeing the data is the point — no
  post-hoc goalpost moves.

---

## Decision 4 — Models

- Generation (Stages B, C questions, tone probe): `gemini-3.1-flash-lite` —
  the production generation model. Probing a model production doesn't use
  produces evidence about nothing.
- Scoring (Stage C): `gemini-3.1-flash-lite` — production reality, with the
  Day-4 caveat stated in Decision 2.
- Relevance gate (Stage A): `gemini-2.5-flash` — production reality.
- **No 2.5-flash scoring comparison arm in this session.** Tempting, but it
  doubles Stage C's scoring calls to answer a question Day 4 already answered
  (2.5-flash is fit; flash-lite is not). If Stage C's flash-lite trajectory is
  badly wrong, a comparison arm becomes a motivated follow-up, not a default.

---

## Decision 5 — Call budget (standing constraint 2)

All flash-lite unless noted:

| Item | Calls |
|---|---|
| Stage A: extraction + relevance gate | 1 + 1 (gate on 2.5-flash) |
| Stage B: probe — 4 new-topic + 2 forced follow-ups (each follow-up ≈ 3 calls) | ≈ 10 |
| Stage C: persona gen + live session (2T, T ≈ 6–10 under ATR) + report | ≈ 14–22 |
| Tone probe: 4 pairs × 2 | 8 |
| **Total** | **≈ 34–42 flash-lite + 1 2.5-flash** |

Comfortably inside the flash-lite RPD; the single 2.5-flash call is nowhere near
its ~20/day ceiling. No pacing changes needed.

---

## Decision 6 — What gets committed

- Both probe JSONLs (question-gen v0.4 + difficulty-tone), after human columns
  are filled — irreplaceable human-judged records, per the v0.2.5 precedent.
- The Stage C transcript export / persisted session file.
- `docs/v0_4c-second-doc-eval-results.md`.
- **The second document itself is never committed — standing rule, no
  exception here.** Same as DAZSMA: source PDFs stay local, gitignored;
  identified in results headers by filename/title/author for traceability.
  Not a case-by-case call — applies equally regardless of which branch of
  Decision 1 resolved the document choice.

---

## Decision 7 — Explicitly out of scope — do not absorb

- Any prompt-template, rubric, or `PROMPT_VERSION` change
- Any orchestration, retrieval, chunking, or scoring-logic change
- Any UI work (v0.3j polish runs on its own parallel track)
- The Streamlit Cloud `ImportError` (own track, precedes public access, not
  this doc)
- The tone-probe *fix* if the hypothesis confirms — future version, own doc
- A 2.5-flash scoring comparison arm (Decision 4)

---

## Decision 8 — Locks (CLOSED 2026-07-20)

1. **Decision 1:** resolved directly by Sean — fallback document `sample2`,
   permission obtained from the document's authors.
2. **Decision 3's pass bar (≥3/4 blind identification)** and the 1-vs-4
   contrast design: confirmed under Sean's delegation.
3. **Sequencing (Decision 0, after v0.4b):** confirmed under Sean's
   delegation.

All decisions in this doc are locked. Ready for brief handoff to the Code
session.

---

# Code Session Brief (execute only after Decision 8 items are answered)

**First task, per standing workflow: state any open questions before writing
code.** Expected answer: none — Decision 8 resolves them pre-session; if
something new surfaces, ask before building.

## Task 1 — Stage A: ingest + gate *(2 LLM calls)*
Run ingestion on the locked second document. **Verify:** paste chunk count,
first-chunk head, and the raw `DocumentAssessment` JSON.

## Task 2 — Stage B: question-gen probe *(≈10 calls)*
`scripts/probe_question_gen.py` per Decision 2 Stage B: 4 new-topic (≥3 chunks,
difficulty 2 and 3) + 2 forced follow-ups, `technical_implementation_reviewer`,
results to `probe_question_gen_v0.4_<docslug>_results.jsonl` with human-judged
nulls. If the script hardcodes DAZSMA paths or methodology_expert, parameterize
minimally — flag every harness change made. **Verify:** raw JSONL head + line
count, `is_grounded()` tally by path.

## Task 3 — Tone probe script *(8 calls)*
Build `scripts/probe_difficulty_tone.py` per Decision 3: paired generation
(difficulty 1 vs 4, same chunk/archetype/persona), 2 archetypes × 2 chunks,
emits shuffled judging file + separate key file + raw JSONL. Shuffle must be
recorded (seed or explicit mapping in the key file) — the unblinding must be
mechanical, not reconstructed. **Verify:** raw JSONL, the judging file
contents, and the key file — plus `is_grounded()` results on the difficulty-4
rows.

## Task 4 — Stage C: live session *(≈14–22 calls)*
Full-panel session on the new document via the current live mechanism, me
driving with the Decision 2 scripted answer sequence. **Verify:** committed
export/persisted file, the per-turn `difficulty_delta` sequence extracted and
pasted, `DefenseReport` output pasted.

## Task 5 — Stage D: findings doc *(0 calls)*
`docs/v0_4c-second-doc-eval-results.md` assembling Tasks 1–4 + my blind
judgments once filled: per-stage pass/fail, divergences from DAZSMA behavior,
the tone-probe verdict against the pre-committed bar, and the flash-lite
scoring-trajectory observation with the Day-4 caveat cited.

## Definition of done
- Every task verified with real output per the standing evidence bar — raw
  JSON, stdout, file contents; forced cases state the forcing mechanism
- Both JSONLs committed (human columns may be filled post-session; commit
  follows their completion)
- Tone-probe verdict stated against the ≥3/4 bar, not narrated around it
- Zero diffs to prompts, rubric, engine, retrieval, UI — `git diff` scope
  evidence
- Existing tests green, count stated; no new dependencies; import boundary
  intact
- Final review message in chat is the checklist itself, item-by-item

## Execution
Standing workflow. Commit-only default; push requires my explicit
authorization — including the still-pending v0.4a commit 84ccaa4, which this
session does not implicitly authorize.
