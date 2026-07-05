# Day 4 Decisions — Scoring Model Swap + Rubric/Branching Refinements

A post-v0.2 change, not a new build day. This doc supersedes two earlier locked
decisions; the conflicts are flagged inline per CLAUDE.md ("the more recent, more
specific doc wins — but flag the conflict, don't silently pick one"). `day1_decisions.md`
and `day3_decisions.md` are left intact as the honest record of what we decided then.

## The story

v0.2 shipped on `gemini-2.5-flash` — locked in `day1_decisions.md` → "Gemini model." During
iterative dev the real wall turned out not to be RPM but **RPD**: the free tier gives
`gemini-2.5-flash` ~20 requests/day, and a single 6-turn session already burns 12, so two
test runs exhaust a day. Day 1 had reasoned that Flash-Lite's per-minute headroom was
irrelevant for a human-paced session — true, but answering the wrong axis. Daily volume, not
per-minute rate, is what throttles building.

`gemini-3.1-flash-lite` clears it: 500 RPD / 15 RPM on the same free tier, and — unlike the
3.x *preview* models Day 1 explicitly avoided — it is now a **stable** release, so the
"preview quotas get cut without notice" objection no longer applies.

We didn't take the swap on generic benchmarks. `scripts/compare_scoring_models.py` ran the
actual `SCORING_SYSTEM_PROMPT` + `AnswerScore` rubric head-to-head on a fixed weak/strong
answer pair, then probed harder (tone-vs-substance, difficulty_delta saturation). Those
findings drove the rubric and branching changes below — the swap wasn't clean on its own.

## Decision 1 — Model: `gemini-3.1-flash-lite` as the dev default, env-swappable

**Supersedes `day1_decisions.md` → "Gemini model — `gemini-2.5-flash`, not Flash-Lite or Pro."**

A `GEMINI_MODEL` env var selects the model; default is `gemini-3.1-flash-lite`. 2.5-flash is
one env var away (`GEMINI_MODEL=gemini-2.5-flash`) and stays the model for final verification
runs. The provider abstraction is unchanged — still the single boundary, which is exactly why
Day 1 built it. This is a *default*, not a burned bridge; the "for now" is load-bearing.

## Decision 2 — Scoring rubric made model-independent

The diagnostic showed both models rewarded fluent-but-empty prose on `clarity`, and that
`difficulty_delta` behavior differed by model. Rather than lean on either model's disposition,
`SCORING_SYSTEM_PROMPT` now states the intent explicitly:

- **clarity** scores engagement with the question, not the fluency/confidence of the prose. A
  smoothly-worded non-answer is low clarity.
- **difficulty_delta** = "press on weakness": escalate (+1) on flawed-but-engaged answers,
  ease (-1) only on a true non-answer. Chosen over the gentler "ease whenever weak" because it
  matches the adversarial product intent — and it needed the least steering, since flash-lite
  already leaned this way.
- **primary_gap** stays honest — name the top real weakness even on a strong answer; null only
  when there genuinely is none. (The Day 1 note "null if the answer was strong" is retired: the
  scorer legitimately finds material gaps in strong answers, and the v0.3 report wants them.)

## Decision 3 — Branching gate widened from gap-presence to answer-strength

**Supersedes `day3_decisions.md` §2 ("`primary_gap is None` → new-topic; not `None` → follow-up").**

Direct consequence of Decision 2: under "press on weakness," the scorer surfaces a gap on
nearly every answer, including strong ones — which collapsed the old gap-only branch into
always-follow-up, so the session stopped exploring new chunks. New rule (`_should_follow_up`
in `main.py`): follow up only when a gap was named **and** the answer was not strong. "Strong"
= every quality axis ≥ 3 **and** their sum ≥ 11 of 15 — robust to the model's per-axis noise,
so a genuinely strong 5/3/5 answer isn't misread as weak, while a mediocre 3/3/3 or a
fluent-but-ungrounded 5/5/1 correctly still gets pressed. `primary_gap` stays populated for the
v0.3 report regardless of the branch.

## Evidence

- `scripts/compare_scoring_models.py` (`--plan full` / `--plan probe`); raw runs logged to
  `scripts/scoring_comparison_results.jsonl` (gitignored — regenerated per run).
- Commits `c238769` (swap + rubric) and `8dae3da` (primary_gap honesty + strong-answer
  detection).
- Full 6-turn end-to-end run on flash-lite (2026-07-05): both branches fired, a strong answer
  advanced to a new topic (chunk 52 → 70), difficulty diverged in both directions, `EXIT=0`.
  `_should_follow_up` is covered by 11 unit cases including the live 5/3/5 pattern.

## Still open / deliberately not done

- Question generation also runs on flash-lite (shared provider). Sanity-checked (grounded,
  in-lane), not formally re-verified to the Day 3 bar. Decouple into its own model only under
  real pressure — no premature second model.
- `main.py`'s `time.sleep(13)` at both call sites is now over-conservative for flash-lite's
  15 RPM; left as pre-existing, trim when convenient.
