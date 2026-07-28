# Case Study — Academic Defense Simulator

**What I set out to build, what the evidence made me change, and the finding my
own eval walked back.**

This is the long version. The [README](../README.md) is the 90-second one. Every
number below comes from a probe or a run committed in this repo; where a claim
rests on something I haven't measured, I say so rather than leaving it implied.

---

## 1. The premise

Retrieval-augmented generation is almost always used one way: retrieve passages,
answer a question. I wanted to invert it. Upload a research document, and a panel
of AI examiners retrieves from it to *interrogate you* — every question anchored
to a specific passage of your own work.

The inversion isn't a gimmick. It changes what "good retrieval" means. In a
question-answering system, a chunk is good if it contains the answer. Here, a
chunk is good if it contains something *attackable* — a claim with a soft
justification, a method with an unstated assumption, a number with no derivation.
That's a different objective, and I didn't appreciate how different until the
retrieval layer started failing in ways that only made sense under it.

Secondary goal, stated honestly: this is my vehicle for learning applied AI
engineering. The eval discipline below is the point at least as much as the
product is.

---

## 2. What's actually built

A defense profile drives persona generation. The document is chunked
paragraph-aware with PyMuPDF, passed through a relevance gate, and embedded
locally with `all-MiniLM-L6-v2`. Retrieval is numpy cosine similarity over a few
dozen vectors — brute force, because at this scale a vector database is
complexity without payoff.

The agent loop retrieves a chunk, has a panelist generate a grounded question,
takes your answer, scores it internally via structured output, and lets that
score steer the next question's difficulty. Nine archetypes are available;
panelists take turns, and one who smells weakness keeps the floor. At session end
you get a report: narrative, difficulty trajectory, per-panelist averages,
pushback outcomes, and per-answer suggestions grounded in the same passages the
questions came from.

Three architectural seams held the whole way, and each paid for itself later:

**Provider isolation.** All Gemini-specific code sits behind one interface. Built
on Day 1 against a vague "we might swap providers" argument. It paid off when the
model split happened — two providers pinned to two different models, constructed
separately, no changes anywhere else.

**No `streamlit` in business logic.** Verified by grep, not assumed. The seam a
FastAPI wrapper would slot into.

**Structured state, not chat-history replay.** Every LLM call renders a fresh
prompt from a typed session model rather than replaying a growing transcript. The
reason was persona drift. The unplanned payoff came at v0.4b: crash-resume turned
out to be *loading state* rather than reconstructing a conversation, because no
context ever lived only in memory. The persisted state **is** the entire input to
the next prompt render.

---

## 3. The finding, and the eval that walked it back

This is the most important section in this document, and it does not end where I
expected it to.

### 3.1 The original design

I designed the internal scoring signal `difficulty_delta` as a correctness
detector: right answers ease off, wrong answers escalate. That was written into
the Day 1 decisions doc and I believed it for three days.

### 3.2 The probe that changed it

Three-way probe. Same question, same grounding, three deliberately different
answers:

| Answer | clarity | depth | grounding | `difficulty_delta` |
|---|---|---|---|---|
| Genuine non-answer — "not sure, I'd have to come back to that" | 1 | 1 | 1 | **−1** |
| Fluent hedge — topic-adjacent, no commitment | 1 | 1 | 1 | **+1** |
| Confident, specific, contradicts the document | 2 | 1 | 1 | **+1** |

My first read was that this failed: the hedge and the confidently-wrong answer
got the same treatment, and my pass criterion said they deserved opposite ones.

The second read was the finding. The signal wasn't measuring correct-versus-wrong
but *engaged-versus-lost* — did the candidate stay in the fight, or concede the
floor. Under that axis the table is right: hedge and wrong-but-committed are both
flawed-but-engaged and get pressed; the non-answer is the only concession and the
only one that eases. The vague-versus-wrong distinction I feared losing survives
in `clarity` and `primary_gap`, which discriminate all three correctly.

I flagged the obvious objection at the time and I'll keep it here: "I called it a
failure, then reinterpreted it as a success" is the exact shape of a
rationalization. My defence was that the rubric rewrite redefining the axis was
locked *before* the probe ran, so the redefinition wasn't invented to save the
result. I still think that holds. I also thought it was close enough to the line
that every probe since pre-commits its interpretation rule in writing before the
data exists.

### 3.3 The eval that didn't reproduce it

Three versions later I ran the full machinery against a document the system had
never been developed on — a wearable TDOA sound-source-localization capstone from
electronics engineering, a different domain and structure entirely. Stage C drove
a full live session with a **pre-scripted** answer-quality sequence, so the
trajectory would be interpretable rather than anecdotal. Two of those turns were
direct replications of the Day 4 probe: turn 2 a fluent hedge, turn 3 a genuine
non-answer.

Day 4 said the hedge escalates (+1) and the non-answer eases (−1). That contrast
is the entire basis of the concession-detector reading.

Observed sequence across all twelve turns:

```
[+1, −1, −1, +1, −1, +1, −1, +1, +1, +1, +1, +1]
      ▲    ▲
      │    └── turn 3, genuine non-answer:  −1   (matches Day 4)
      └─────── turn 2, fluent hedge:        −1   (Day 4 said +1)
```

**The hedge eased. Same direction as the genuine non-answer.** The distinction
did not replicate.

### 3.4 What survives and what doesn't

**Survives:** the conceptual reframe. `difficulty_delta` was never *designed* to
detect correctness, and nothing here restores the Day 1 framing. Clarity and
`primary_gap` still discriminate the three answer types reliably, so the
information I care about is still recoverable from the score object.

**Does not survive:** the claim that the production model reliably *produces*
the concession signal. One document, three rows, one probe is not a
generalization, and the first held-out test contradicted the load-bearing
contrast.

**Where it converges:** with the standing finding that `gemini-3.1-flash-lite` is
unfit for judgment work. The results doc records this as extending that caveat
rather than as a separate mystery, and I think that's right — this is most likely
the same unreliability showing up in a new place. Which means the honest headline
is not "difficulty_delta is a concession detector" but **"difficulty_delta may be
a concession detector, and the model I can afford to run it on can't be trusted
to produce it consistently."**

That's a worse result and a better piece of engineering. I built the
second-document eval specifically to find out whether my findings generalized. It
found that one of them didn't. Deleting the section would have been easy and
nobody would have known.

---

## 4. Model fitness is per-task, and the honest version is uncomfortable

The project started on `gemini-2.5-flash`. The wall wasn't requests per minute
but requests per *day*: roughly 20 on the free tier, and a single six-turn
session burns 12. Two test runs exhausted a day. My Day 1 reasoning had been that
Flash-Lite's per-minute headroom was irrelevant for a human-paced session — true,
and answering the wrong axis entirely.

So I swapped the dev default to `gemini-3.1-flash-lite` (500 RPD / 15 RPM) and
ran the actual scoring prompt head-to-head rather than trusting benchmarks. The
diagnostic found flash-lite unfit for judgment: it inverted difficulty
adjustments on weak answers and inflated clarity scores on fluent-but-empty
prose. Generation is a different task class and probed clean separately.

Then I caught my own methodological hole after the doc was written. The
comparison that justified the swap ran both models against the *old* prompt — but
the rubric had been rewritten in the same change, and `gemini-2.5-flash`, the
model I was reserving for verification, had never been re-run against the new
rubric it was supposed to verify against. A diagnostic validating a model on a
prompt you've replaced validates nothing. I closed it with two fresh runs
appended as an addendum, rather than editing the original to look cleaner.

**The uncomfortable part.** The model diagnosed unfit for judgment is still doing
per-turn scoring in production, because `gemini-2.5-flash` cannot sustain two
calls per turn on a free tier. It's used exactly where the budget allows: the
relevance gate, answer suggestions, and gap clustering — two calls per session,
capping the app at roughly ten full sessions a day.

A README saying "judgment tasks run on the stronger model" would be technically
defensible and materially misleading. The rule the project follows instead:
apply the finding wherever it fits the budget, name the one place it doesn't, and
don't re-litigate it per call site. Section 3.3 is what that tradeoff costs.

---

## 5. Three findings about retrieval, none of which I went looking for

**Lane drift is a retrieval problem wearing a prompt costume.** When archetypes
drifted out of their assigned lane, the obvious diagnosis was weak lane wording.
It wasn't. Every drifting row had landed on a chunk containing nothing in its
lane — an interview transcription, an external API lookup, a List of Appendices.
Every clean row sat on an evaluation-table chunk with real material. The
archetypes weren't ignoring instructions; they were handed the wrong evidence and
answered with what was in front of them. Prompt-tuning would have burned days
moving nothing.

**One root cause, three symptoms.** The retrieval query for each archetype is a
fixed string — the archetype's focus description. Fixed string → fixed embedding
→ fixed ranking → identical chunk sequence for a given document, every session.
That single fact explains three complaints I'd been treating as unrelated:
questions repeating across sessions, front-matter chunks getting retrieved
(words like "objectives" and "findings" are contents-page vocabulary, so a
contents page is a strong semantic match), and the off-target retrieval above.
Note what *doesn't* fix it: raising `top_k` addresses none of the repetition,
because the ranking is deterministic. Only sampling or query variation does.

**The grounding check has a systematic false-negative mode.** In the
archetype-expansion probe, 5 of 22 rows failed the grounding check. I verified
all five line-by-line against the source: **zero fabrication.** Every fact real,
every attribution correct. The check matches a *contiguous* span, and each
failing reference had assembled non-contiguous text — two figure captions joined
with "and", a range expressed with "through", two cells of a single table row.

The consequence is the part that matters. Those failures concentrate in the
*comparative* archetypes — the ones whose job is to cite two things at once,
through a schema giving them one slot to do it in. **The metric penalises the
more sophisticated question types.** A per-archetype grounding pass rate, read
naively, ranks the archetypes roughly backwards. The real fix is a schema change
(`grounding_reference` → a list), not a looser threshold.

---

## 6. Process findings

**A pre-committed rule that was simply wrong.** I write decision rules in advance
and hold myself to them. One — labelled B′ in the hardening brief — was malformed
on inspection: its precondition was swallowed by its own override clause, so it
could never fire, and the situation that actually occurred wasn't in its
enumeration of cases at all. It was flagged before being executed rather than
after, and its removal was verified rather than assumed. I kept the wrong rule in
the record with its post-mortem. Pre-committing to rules is only honest if the
broken ones stay visible.

**A violation I accused myself of that never happened.** I recorded a workflow
violation against myself — a push I believed I'd made without authorization.
`git reflog` disproved it. The claim was struck with the evidence that struck it.
Being wrong in the self-critical direction still puts a false claim in the
permanent record.

**Predicted, then measured.** Per-session LLM call count was derived from the
design docs as a formula before instrumentation existed, then verified live by a
counter at the provider boundary. The first instrumented run measured a session
ending two turns early — the counter exposed an orchestration bug where one
panelist's spent follow-up budget silently blocked another's, characterized with
line-level evidence before any fix was written.

**Measured, never derived by arithmetic.** The rate limiter's ceiling doesn't
behave as a literal cap — observed peak runs one request above the configured
value. The shipped setting is the one empirically verified clean against the real
tier; setting it to the tier's stated limit reproduces a real 429.

**No LLM grading an LLM.** Every probe's quality columns are judged by me, by
hand. The difficulty-tone probe goes further: it emits a shuffled judging file
with labels stripped and a separate key, so I judge blind and unblind
mechanically. Knowing which question was requested at difficulty 4 biases the
read toward finding sharpness in it — the fix for that is structural, not
willpower.

---

## 7. Generalization: what the held-out document actually showed

The evidence base originally rested on one PDF, which is a single point of
failure for every claim here. The second-document eval ran the full machinery
against a capstone from a different discipline that the system had never been
developed on.

| Stage | Result |
|---|---|
| A — ingestion + relevance gate | Pass |
| B — question-generation probe | Pass, with four findings |
| Difficulty-tone probe (generation + grounding) | Pass, with one finding |
| Difficulty-tone probe (blind judgment) | Pass — 3/4 against a pre-committed ≥3/4 bar |
| C — full live session, mechanics | Pass |
| C — scoring trajectory | **Did not replicate** (§3.3) |

The blind judgment deserves its detail. Four pairs, labels stripped, shuffled: I
identified the higher-difficulty side correctly in three, and got the fourth
backwards — a technical-implementation pair where I picked the wrong side
entirely. The bar was pre-committed at ≥3/4 *before the data existed*, along with
what each outcome would license. It passed, and the competing hypothesis it was
designed to test was rejected.

That's the honest shape of the generalization claim: **the machinery generalized;
the scoring finding didn't.** Ingestion, gating, question generation, grounding,
and difficulty-tone distinctiveness all held on unseen material in an unfamiliar
domain. The one thing that broke is the one that depends on the cheap model's
judgment — which is exactly where the standing caveat said to expect trouble.

A third document, my own ESP32-CAM thesis, was used for deployment verification
rather than question-generation evaluation. I'm not counting it toward the
generalization claim.

---

## 8. What I haven't verified

**Retrieval has no dedicated eval, and that's the biggest hole here.** Every
retrieval finding in §5 was discovered sideways, through a generation probe.
Three checks needing no labeled data would close most of it: score spread across
the top-5 results (is `top_k=1` principled or arbitrary?), front-matter hit rate
against base rate, and archetype top-k overlap — do archetypes differ in the
*evidence* they retrieve, or only in the voice they use on identical evidence?
That last one directly tests whether multi-archetype retrieval does anything at
all. Specified, not yet run.

**Adaptive difficulty is implemented but not calibration-verified.** In the
archetype-expansion probe the difficulty-appropriateness column passed 22 of 22 —
and I am explicitly **not** citing that as evidence of calibration. A rubric that
never fails anything is indistinguishable from calibration I can't see, and I
didn't re-check the rubric during that judgment pass. Adaptive difficulty is one
of four stated differentiators of this project and that column is the only
evidence behind it, which makes the honest position: the mechanism runs, its
calibration is unmeasured. A d2-versus-d3 discrimination probe needs to happen on
its own terms.

**Question repetition across sessions is diagnosed but not measured.** The
constant-query root cause predicts it; I haven't run the same document twice and
diffed the output. Identical questions and same-topic-different-angle questions
are different problems with different fixes, and I don't know which I have.
Counterargument worth stating: the same document has the same weaknesses, so
*some* cross-session consistency is correct behaviour.

**Two tests fail.** 288 of 290 pass. Both failures are environment artifacts
rather than defects, and both are logged rather than skipped to make the suite
look green: one is a test-isolation artifact where a real `.env` re-populates an
env var the test clears, and the other reads a pre-existing session file that
lives outside the repo and so is absent on a fresh clone.

---

## 9. What I'd do next, in order

1. **The retrieval eval.** Highest value by a distance — the unevaluated half of
   the project's central claim, and three sub-checks need no labeled data.
2. **Re-run the scoring probe on `gemini-2.5-flash`.** §3.3 leaves an open
   question with a cheap answer: if the concession contrast reproduces on the
   stronger model and not the cheap one, that converts an unexplained
   non-replication into a clean, quantified model-capability finding.
3. **Widen `grounding_reference` to a list.** Fixes the false-negative mode at
   the schema instead of loosening the check. Fires the standing constraint —
   prompt version bump, existing probe rows invalidated — which is why it's
   third.
4. **A front-matter chunk filter.** No archetype should ever retrieve a List of
   Tables.
5. **The difficulty discrimination probe.** §8, item two.

---

## 10. Evidence index

- `docs/` — decision records, one per version. Retired decisions stay in;
  provenance recorded; deviations logged. Including the rule that was wrong and
  the claim that was struck.
- `scripts/probe_*.py` and their `*_results.jsonl` — raw probe output with
  human-judged columns. Never overwritten; a new file per run.
- `evidence/` — verification output for the deployment-hardening pass.
- `tests/` — pure functions only, no network.

Source PDFs are never committed. All documents are used with the permission of
their authors and identified in results headers by title and author for
traceability rather than reproduced in full.

---

Sean Silver Allata — [GitHub](https://github.com/slvr123) ·
[LinkedIn](https://www.linkedin.com/in/sean-silver-allata-9b1885395/)
