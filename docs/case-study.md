# Case Study: Academic Defense Simulator

**What I set out to build, what the evidence made me change, and the finding my own eval walked back.**

This is the long version; the [README](../README.md) is the short one.
Every number below comes from a probe, an eval or a run committed in this repo, and the section says where.
Where a claim rests on something I haven't measured, I say so instead of leaving it implied.

The project has two halves.
v1 (sections 2 to 5) was a Streamlit app where I built the engine and learned to measure it.
v2 (sections 6 to 11) wrapped that engine in FastAPI and React, put it on free hosting, and spent most of its milestones measuring and fixing what the panel actually asks.

---

## 1. The premise

The RAG pattern I'd learned was: retrieve passages, answer a question.
I wanted to turn it around.
Upload a research document, and a panel of AI examiners retrieves from it to question *you*, every question anchored to a specific passage of your own work.

This changes what "good retrieval" means.
In a question-answering system, a chunk is good if it contains the answer.
Here, a chunk is good if it contains something worth challenging: a claim with a soft justification, a method with an unstated assumption, a number with no derivation.
That's a different objective, and I didn't appreciate how different until the retrieval layer started failing in ways that only made sense under it.

Second goal, stated honestly: this is how I'm learning applied AI engineering.
The eval discipline below is the point at least as much as the product is.

---

## 2. What's built

A defense profile (thesis, capstone, or another kind of oral defense, plus the panel) drives persona generation.
The PDF is parsed with PyMuPDF, navigation pages and the reference list are dropped, the rest is chunked paragraph-aware, and each chunk is embedded locally with `all-MiniLM-L6-v2`.
Retrieval is numpy cosine similarity over a few dozen vectors: brute force, because at this scale a vector database is complexity with no payoff.

The agent loop retrieves a chunk, has a panelist write a question grounded in it, takes your answer, scores it with structured output, and lets that score steer the next question.
Answers with gaps draw follow-ups, so a panelist keeps the floor while you're struggling.
A Terror Panelist (v1 called it the Devil's Advocate) speaks last and contests the strongest claim you've made.
The debrief has your scores, the panel's view, and per-answer suggestions grounded in the same passages the questions came from.

Three seams held the whole way, and each paid for itself later.

**Provider isolation.** All Gemini-specific code sits behind one interface.
I built it on Day 1 against a vague "we might swap providers" argument.
It paid off when the model split happened: two providers pinned to two models, constructed separately, nothing else changed.

**No `streamlit` in business logic.** Verified by grep, not assumed.
In v1 I called this "the seam a FastAPI wrapper would slot into".
In v2 that's exactly what happened: `backend/` imports v1's engine and runs it, and nothing in `backend/` imports `streamlit`.

**Structured state, not chat-history replay.** Every LLM call renders a fresh prompt from a typed session model instead of replaying a growing transcript.
The reason was persona drift.
The unplanned payoff came at v0.4b: crash-resume turned out to be *loading state* rather than reconstructing a conversation, because no context ever lived only in memory.

### Moving to free hosting (v2.0 M1 and M2)

I made the FastAPI and React migration conditional on one thing: the backend had to fit Render's free tier, 512 MB of RAM.
The embedder was the risk, because sentence-transformers pulls in torch.
I set the gates before measuring: peak memory at most 400 MB, cold start at most 90 s, and the ONNX port's vectors within 0.999 cosine of the originals.

- **Spike 1, ONNX instead of torch:** NO-GO.
  It peaked at 494.9 MB on Render, against about 320 MB on my Windows machine.
  Local memory was no predictor of the server's.
- **Spike 2, a separate pre-registered experiment:** smaller embedding batches, a cap on glibc's malloc arenas, and one embedding at a time.
  This one was GO.
- **M1, the real backend with my largest PDF** (224 pages, 26.43 MB): round 1 failed at 414.2 MB.
  Round 2 added an explicit memory release between parsing and embedding, and peaked at 346.6 MB.
  I can't prove the release was the cause, because round 1 had no phase readings, but the numbers are consistent with it.
- **M2, a second upload while the first was still embedding:** 333.8 MB, still under the gate.

The daily quota lives in Postgres on Neon, because the free tier shares one Gemini key across every visitor.
A slot is reserved with a single `UPDATE` that only succeeds while the count is under the cap, so two visitors can't both take the last one.
A defense that fails before its first question refunds its slot.

---

## 3. The finding, and the eval that walked it back

This is the most important section here, and it doesn't end where I expected it to.

### 3.1 The original design

I designed the internal scoring signal `difficulty_delta` as a correctness detector: right answers ease off, wrong answers escalate.
That was written into the Day 1 decisions doc, and I believed it for three days.

### 3.2 The probe that changed it

Three-way probe.
Same question, same grounding, three deliberately different answers:

| Answer | clarity | depth | grounding | `difficulty_delta` |
|---|---|---|---|---|
| Genuine non-answer: "not sure, I'd have to come back to that" | 1 | 1 | 1 | **−1** |
| Fluent hedge: topic-adjacent, no commitment | 1 | 1 | 1 | **+1** |
| Confident, specific, contradicts the document | 2 | 1 | 1 | **+1** |

My first read was that this failed: the hedge and the confidently wrong answer got the same treatment, and my pass rule said they deserved opposite ones.

The second read was the finding.
The signal wasn't measuring correct against wrong but *engaged against lost*: did the candidate stay in the fight, or concede the floor?
On that axis the table is right.
The hedge and the wrong-but-committed answer are both flawed but engaged, and get pressed; the non-answer is the only concession, and the only one that eases.
The vague-versus-wrong distinction I was afraid of losing survives in `clarity` and `primary_gap`, which tell all three apart correctly.

I flagged the obvious objection at the time and I'll keep it here: "I called it a failure, then reinterpreted it as a success" is exactly what a rationalization looks like.
My defence was that the rubric rewrite redefining the axis was locked *before* the probe ran, so the redefinition wasn't invented to save the result.
I still think that holds.
It was also close enough to the line that every probe since has written down its interpretation rule before the data exists.

### 3.3 The eval that didn't reproduce it

Three versions later I ran the full machinery against a document the system had never been developed on: a wearable sound-source-localization capstone from electronics engineering, a different domain and structure entirely.
Stage C drove a full live session with a **pre-scripted** sequence of answer quality, so the trajectory could be interpreted instead of being an anecdote.
Two of those turns replicated the Day 4 probe directly: turn 2 a fluent hedge, turn 3 a genuine non-answer.

Day 4 said the hedge escalates (+1) and the non-answer eases (−1).
That contrast is the entire basis for reading the signal as a concession detector.

The observed sequence across all twelve turns:

```
[+1, −1, −1, +1, −1, +1, −1, +1, +1, +1, +1, +1]
      ▲    ▲
      │    └── turn 3, genuine non-answer:  −1   (matches Day 4)
      └─────── turn 2, fluent hedge:        −1   (Day 4 said +1)
```

**The hedge eased, in the same direction as the genuine non-answer.**
The distinction did not replicate.

### 3.4 What survives and what doesn't

**Survives:** the reframe.
`difficulty_delta` was never *designed* to detect correctness, and nothing here restores the Day 1 framing.
Clarity and `primary_gap` still tell the three answer types apart, so the information I care about is still in the score object.

**Doesn't survive:** the claim that the production model reliably *produces* the concession signal.
One document, three rows and one probe is not a generalization, and the first held-out test contradicted the contrast it rests on.

**Where it converges:** with the standing finding that `gemini-3.1-flash-lite` is unfit for judgment work (section 4).
This is most likely the same unreliability showing up in a new place.
So the honest headline isn't "difficulty_delta is a concession detector" but **"difficulty_delta may be a concession detector, and the model I can afford to run it on can't be trusted to produce it consistently."**

That's a worse result and better engineering.
I built the second-document eval specifically to find out whether my findings generalized, and it found that one of them didn't.
Deleting the section would have been easy, and nobody would have known.

---

## 4. Model fitness is per task, and the honest version is uncomfortable

The project started on `gemini-2.5-flash`.
The wall wasn't requests per minute but requests per *day*: about 20 on the free tier, and a single six-turn session burned 12.
My Day 1 reasoning had been that Flash-Lite's per-minute headroom didn't matter for a human-paced session: true, and an answer to the wrong question.

So I moved the dev default to `gemini-3.1-flash-lite` (500 a day, 15 a minute) and ran the actual scoring prompt head to head instead of trusting benchmarks.
Flash-lite came out unfit for judgment: it inverted difficulty adjustments on weak answers and inflated clarity on fluent but empty prose.
Question generation is a different kind of task, and it probed clean on its own.

Then I found a hole in my own method after the doc was written.
The comparison that justified the swap ran both models on the *old* prompt, but the rubric had been rewritten in the same change, and `gemini-2.5-flash`, the model I was reserving for verification, had never been run on the new rubric.
A diagnostic that validates a model on a prompt you've replaced validates nothing.
I closed it with two fresh runs, appended as an addendum instead of editing the original to look cleaner.

**The uncomfortable part.** The model I measured as unfit for judgment still scores every answer in production, because the free tier can't sustain `gemini-2.5-flash` on every turn.
In v2, `gemini-2.5-flash` writes one thing: the debrief's per-answer suggestions, one call per defense.
The daily cap is set by flash-lite instead: a full panel makes up to 43 flash-lite calls, against 500 a day, which gives 10 defenses a day after a margin of one.

A README saying "judgment runs on the stronger model" would be technically defensible and materially misleading.
The rule I follow instead: apply the finding wherever the budget allows, name the place it doesn't, and don't re-argue it at every call site.
Section 3.3 is what that tradeoff costs.

A visitor with their own Gemini key skips the shared cap (v2.0 M17).
The key is checked once, held only in server memory on that one defense, and never logged.
I checked that with a sentinel key, a fake key with a unique string, which appeared 0 times across 549 log lines and every response body.

---

## 5. Three findings about retrieval I didn't go looking for (v1)

**Lane drift was a retrieval problem in a prompt costume.**
When panelists drifted out of their lanes, the obvious diagnosis was weak lane wording.
It wasn't.
Every drifting row had landed on a chunk with nothing in its lane: an interview transcription, an external API lookup, a List of Appendices.
Every clean row sat on a chunk with real material.
The panelists weren't ignoring instructions; they were handed the wrong evidence and asked about what was in front of them.
Prompt tuning would have burned days and moved nothing.

**One root cause, three symptoms.**
Each archetype's retrieval query was a fixed string, its focus description.
Fixed string, fixed embedding, fixed ranking: the same chunk sequence for a given document, every session.
That explained three complaints I'd been treating as unrelated: questions repeating across sessions, front-matter chunks being retrieved (words like "objectives" and "findings" are table-of-contents vocabulary), and the off-target retrieval above.
Raising `top_k` fixes none of the repetition, because the ranking is deterministic; only sampling or varying the query does.
v2 fixed both halves (section 6).

**The grounding check has a systematic false-negative mode.**
In the archetype-expansion probe, 5 of 22 rows failed the grounding check.
I checked all five line by line against the source: **zero fabrication.**
The check matches one *contiguous* span, and each failing reference had joined text that isn't contiguous: two figure captions joined with "and", a range written with "through", two cells of one table row.
Those failures concentrate in the archetypes whose job is to compare two things, through a schema that gives them one slot to cite them in.
**The metric penalises the more sophisticated question types**, so a per-archetype grounding pass rate, read naively, ranks the archetypes roughly backwards.
The fix is a schema change (`grounding_reference` becoming a list), not a looser threshold.
It's still open.

---

## 6. Retrieval, measured (v2.0 M4 to M6)

v1's case study called retrieval "the biggest hole here": every finding in section 5 had been discovered sideways, through generation probes.
M4 was the eval it asked for, with three checks that need no labelled data and pass rules set before running them.

**Do archetypes retrieve different evidence, or the same passages in a different voice?**
Different.
Across the two evaluation papers, the median pair of archetypes shares 1 of its top 5 passages, against a random baseline that depends on document size.
On one document it was only partly true (the median pair shares 2).

**The real defect was somewhere else.**
On one paper, 6 of 8 archetypes' single best match was front matter, and 4 of them landed on a page that was entirely table of contents.
Every recommended-panel session on that paper would have asked the candidate at least one question about the abstract or the contents page.

The fixes came in order, each re-measured with the same eval:
- **M5** drops navigation pages (contents, lists of figures and tables) before chunking. No archetype picks front matter first on either paper.
- That exposed the next one: two archetypes now matched the **reference list** best. **M6** cuts the reference list too.
- With the contents pages gone, most first picks won by a hair: on 62.5% of them, the best passage beat the runner-up by less than 0.02. So **M6** also made new topics pick at random among the passages within 0.02 of the best, seeded by session and turn so any defense replays exactly. That breaks the fixed-ranking repetition from section 5.

**One thing the eval found that I haven't fixed:** MiniLM reads at most 256 tokens, and almost every chunk is longer.
On the eval papers the embedder sees about half to two thirds of the average chunk.
The text the panelist reads is complete; the vector that picked it isn't.

---

## 7. The panel was asking about other people's work (v2.0 M7 to M9)

This is the v2 finding I'd point at first.

On 2026-10-27, on the held-out capstone from section 3.3, a panelist asked about the hardware in a study the paper *reviews*, as if it were the team's own.
The team had built on different hardware entirely, and the Terror Panelist then built its own question on the same mistake.
A literature review is mostly other people's work, described in the student's words, and the panel had no way to tell.

### Measuring it

I tagged literature-review chunks at ingest and built a probe: questions generated on those chunks, judged as M (treats cited work as the student's), C or OK, with a control arm on chunks of the student's own work.

| | No note | Note, round 1 | Note, round 2 | Control |
|---|---|---|---|---|
| My scan | 8 of 20 | 2 of 20 | 7 of 28 | 0 of 12 |
| Claude's careful check | 11 of 20 | 6 of 20 | 9 of 28 | 0 of 12 |

The "note" was an instruction attached to the chunk: this excerpt describes other researchers' work, so ask how the candidate used it.
It roughly halved the error, and never got near the bar I'd set before the data: at most 1 wrong of 28.
Two wordings performed the same.

Two honest details in that table.
My own labels were a quick scan, and the careful check found more errors in every arm except the control.
And the careful check wasn't blind: Claude had drafted the note and seen the arm assignment.

### Fixing it structurally, and judging it independently

M9 stopped telling the model and changed what it's given.
Literature-review passages are routed by panelist role, and the question was whether even the Literature & Theory Specialist, the one panelist whose lane *is* the literature, could be trusted with them.

That verdict needed a judge I trusted more than my scan or a non-blind check.
The judge was a fresh agent on a different model.
It saw only a sheet of item numbers, pages, questions and passages, with the rubric verbatim, and no other tool.
It labelled 4 M and 1 unsure out of 16, so 5 counted against a bar of 1.

So the setting that shipped, `lit_review_access = "none"`, withholds literature-review passages from every panelist.
The failure can't happen any more, because the evidence that caused it never reaches the prompt.

**The cost is real and I state it in the README:** the panel doesn't question your literature review at all.
The Literature & Theory Specialist is in no preset, and a Custom panel that seats it opens out of chapter order (section 8).
A Literature Specialist with real access to the literature is on the v2.1 list.

---

## 8. Opening in the right chapter (v2.0 M12 and M14)

A real defense tends to walk through the paper: problem first, then method, then results.
Ours opened wherever retrieval happened to land.

**M12** ordered the opening round by each panelist's home chapter (Problem & Objectives is Chapter 1, Methodology is Chapter 3, and so on).
The rule, set before measuring: a round passes if its cited chapters never go backwards, and the change needs 8 of 10.
It got **5 of 10**.
I shipped the ordering anyway, since it does no harm and the next fix builds on it, and recorded the miss.
All five failures were on the same sample paper.

**Why it failed.** Before building anything, M14 tested the hypothesis.
The only Chapter 1 chunk in the sample paper ranked **6th of 13** for the Problem & Objectives query; Chapter 4 and 5 chunks ranked above it.
Results chapters are full of words like "objective" and "problem", because they report against them.
**Embeddings match vocabulary, not position in the argument.**

**M14** restricts each panelist's *first* question to its home chapter, using chapters detected at ingest.
It sits on top of M9's routing and never unblocks anything.
Result: **10 of 10** rounds walked forward, with the filter applied on all 30 opening questions and zero fallbacks.

Two scope limits, both in the README.
Without readable chapter headings, nothing is filtered.
And the claim holds for the preset panels only: a Custom panel with the Literature Specialist has a home chapter it's barred from, so its opener falls back elsewhere (a live run went Chapter 1, 5, 3).

---

## 9. One closing line per hand-off (v2.0 M15)

When a panelist hands off, it now says one line of its own.
The line is written in parallel with the next question, so a hand-off usually adds no wait; after a one-second grace, a fixed neutral line stands in.

The risk was that a closing line leaks the score: "thank you for clarifying" after a strong answer, "that covers my concerns" after a weak one.
That would break the sealed-score design.
So the neutrality rule came first: at least 27 of 30 lines neutral, and at most 2 that match the answer's band.

- **E1, the original prompt: fail, by one line.** 27 of 30 were neutral, but all three others matched their band, and the rule allowed 2. The verdict hinged on reading "my concerns" strictly; I kept the strict reading.
- **The one allowed rerun**, with the rule rewritten: neutral, but formulaic. Only **4 of 10** sessions avoided repeating a closing line's first three words, even with "vary the phrasing naturally" in the prompt. Twelve of 30 lines opened "Thank you, I have", punctuation aside.
- **The amendment** put the session's earlier closing lines into the prompt, verbatim, with an instruction not to reuse their openings. Neutral, and **10 of 10** sessions varied.

It cost one defense a day: three extra calls per defense took the cap from 11 to 10.
A known limit remains: a session's first closing line still almost always opens "Thank you", because there's nothing earlier to steer away from.

---

## 10. Taglish mode (v2.0 M16)

Many Philippine defense panels switch between English and Filipino mid-sentence.
Taglish mode makes the panel's questions, follow-ups, Terror Panelist challenges and closing lines do the same.
Scoring, the debrief and the suggestions stay in English.

**Touching v1's prompts safely.** The language switch needed one slot in v1's prompt templates, my first change to v1 since v2 began.
Before changing anything, a golden test captured the exact prompts v1 builds for five cases that between them hit every conditional slot, and it now proves the English prompts are byte-for-byte unchanged.

**Scoring parity.** If Taglish answers scored differently from the same answers in English, the mode would be unfair.
Ten answers, each written twice with the same content, each version scored three times by the production scorer.
The rule: a mean difference within ±0.75 of 15 points, and no single pair over 2.0.
Result: mean **+0.50**, largest pair **+2.00**, exactly at the limit, so it passed.
I don't overclaim it: that pair's English version had the widest spread in the table, so part of the +2.00 is the scorer's own noise, and the Taglish versions ran a few words longer.
The answers were written by Claude, not by real students.

**A method lesson I paid for.** Amendment 2 moved the language line in the opener's prompt, and openers containing Filipino went from 2 of 6 to 5 of 6.
Amendment 3 added "use Taglish from your very first sentence" and failed: 5 of 10 turn-1 openers.
v1's rule that every question opens with a lead-in clause makes them start "Regarding ...", in English.
I ran Amendment 3 without a baseline, so I can't tell whether its sentence helped at all.
**Measure a baseline before changing anything.**

---

## 11. The pattern across v2

**Changing what the model is given beat telling it to behave differently, every time I measured both.**

| Milestone | The instruction alone | The structural change |
|---|---|---|
| M7 to M9, attribution | A "cited work" note: 11 of 20 wrong became 6 of 20 | Withholding those passages: the error can't occur |
| M12 and M14, chapter order | Ordering the speakers: 5 of 10 rounds | Filtering the first question's evidence: 10 of 10 |
| M15, closing lines | "Vary the phrasing": 4 of 10 sessions | Showing the earlier lines: 10 of 10 |

M16 is the counter-example in the other direction: a stronger language instruction couldn't beat v1's structural English lead-in rule.
The structure won there too, just not on my side.

The second pattern is about the copy, not the model.
M17 wrote a "How it works" section for the start screen, and every sentence had to trace to code or a results doc.
My draft said the questions get "harder as you do well".
The rubric does the opposite: it escalates when the answer is engaged but flawed, and eases only when the candidate is completely lost.
The sentence was cut.
So was "most RAG apps search your documents to answer your questions", because nothing in this repo shows what other apps do.

---

## 12. Process findings

**A pre-committed rule that was simply wrong.** One rule, labelled B′ in the hardening brief, was malformed: its precondition was swallowed by its own override clause, so it could never fire, and the situation that actually happened wasn't among its cases.
It was flagged before being executed, not after, and I kept it in the record with its post-mortem.
Pre-committing to rules is only honest if the broken ones stay visible.
My pre-committed rules now always include a "none of the above: halt" branch.

**A violation I accused myself of that never happened.** I recorded a push I believed I'd made without authorization.
`git reflog` disproved it, and the claim was struck with the evidence that struck it.
Being wrong in the self-critical direction still puts a false claim in the permanent record.

**Shipping under the bar, on the record.** M12's ordering missed its pre-set 8 of 10 and shipped anyway, because it harmed nothing and M14 built on it.
That's allowed only when the decision and the miss are both written down.

**Predicted, then measured.** Per-session LLM calls were derived as a formula from the design docs before instrumentation existed, then counted live at the provider boundary.
The first instrumented run ended two turns early: one panelist's spent follow-up budget was silently blocking another's.
v2's daily cap is computed the same way: calls per defense against each model's daily limit.

**Measured, never derived by arithmetic.** The rate limiter's observed peak runs one request above its configured ceiling, so the shipped setting is the one verified clean against the real tier.
Test counts in every results doc come from real `pytest` and Vitest output.

**Who judges.** In v1 I judged every probe's quality columns by hand, blind where it mattered: the difficulty-tone probe emitted a shuffled judging file with labels stripped and a separate key.
In v2 the volume outgrew my time, so I evaluated and graded with an LLM: my labels and Claude's careful check side by side, which is how section 7 shows my quick scan undercounting.
For the verdicts that decided a design, the judge was a separate agent on a different model, isolated to the sheet and the rubric.
That's a change in method, and I'd rather name it than keep claiming the v1 rule.

**A fact audit before the launch writing.** M13 checked the old README and this case study claim by claim against the code.
Most of v1's operational claims had gone stale with v2, and two were false outright: the relevance gate (v2's backend skips it) and "source PDFs are never committed" (the public sample capstone is).

---

## 13. Generalization: what the held-out document showed (v1)

The v1 evidence originally rested on one PDF, a single point of failure for every claim.
The second-document eval ran the full machinery against a capstone from a different discipline.

| Stage | Result |
|---|---|
| A: ingestion and the relevance gate | Pass |
| B: question-generation probe | Pass, with four findings |
| Difficulty-tone probe (generation and grounding) | Pass, with one finding |
| Difficulty-tone probe (blind judgment) | Pass: 3 of 4 against a pre-committed bar of 3 of 4 |
| C: full live session, mechanics | Pass |
| C: scoring trajectory | **Did not replicate** (section 3.3) |

The blind judgment: four pairs, labels stripped, shuffled.
I picked the higher-difficulty side correctly in three and got the fourth backwards.
The bar was set at 3 of 4 *before the data existed*, along with what each outcome would license.

**The machinery generalized; the scoring finding didn't.**
v2's retrieval eval re-used that document and added a second, larger one (section 6); the chapter-order, closing-line and Taglish checks ran on the public sample capstone.

---

## 14. What I haven't verified

**Adaptive difficulty is implemented, not calibration-verified.**
The archetype-expansion decisions doc records every new archetype as difficulty-appropriate, but the committed judging column was never filled in.
There's no data behind it, and I don't cite it.
A difficulty-discrimination probe still needs to happen on its own terms.

**The scoring probe hasn't been re-run on `gemini-2.5-flash`.**
If the concession contrast reproduces on the stronger model and not on the cheap one, section 3.3 becomes a clean model-capability finding instead of an open question.

**Question repetition across sessions was diagnosed, then mitigated, but never measured.**
The near-tie sampling breaks the fixed ranking.
I haven't run the same paper twice and diffed the questions.

**Smaller things the evals surfaced, all on the v2.1 list:**
- The Terror Panelist once built a challenge on a premise the paper doesn't support: it claimed the paper promises "actionable agricultural advice".
- Incoming acknowledgments ("clear enough") may leak score tone, and haven't had M15's neutrality test.
- The suggestions call returns 503s under load and the debrief loses its suggestions; how often is unmeasured.

**Tests:** 632 backend and 293 frontend pass.
v1's suite has 409 passing and 1 failing: that test reads a session file that lives outside the repo, so it's absent on a fresh clone.
It's logged in the ROADMAP instead of skipped, so the suite stays honestly green rather than cosmetically green.

---

## 15. What I'd do next, in order

1. **Re-run the scoring probe on `gemini-2.5-flash`.** It's cheap, and it settles section 3.
2. **Widen `grounding_reference` to a list.** It fixes the false-negative mode at the schema and stops the metric penalising comparison questions.
3. **A Literature Specialist with real access.** M9 fixed attribution by removing the topic; the real fix is a panelist that can question a literature review correctly.
4. **The difficulty-discrimination probe.**
5. **Faster voice.** Panel voices take about 7 to 15 seconds per question, the slowest part of the product.

---

## 16. Evidence index

- `docs/`: one decisions doc per milestone, written before the work, and a results doc with real command output after it. Retired decisions stay in, with provenance and deviations logged, including the rule that was wrong and the claim that was struck.
- `evals/`: raw eval output for v2 (retrieval, attribution, chapter order, closing lines, Taglish parity).
- `scripts/probe_*.py` and their `*_results.jsonl`: v1's raw probe output with human-judged columns. Never overwritten; a new file per run.
- `evidence/`: verification output for v1's deployment hardening.
- `tests/` and `backend/tests/`: the unit tests mock the Gemini client.

The only source PDF committed is the public AniCheck sample capstone, which runs the sample-paper button and the recorded demo.
The other evaluation documents are used with their authors' permission and are not committed; results docs identify them for traceability instead of reproducing them in full.

---

Sean Silver Allata · [GitHub](https://github.com/slvr123) · [LinkedIn](https://www.linkedin.com/in/sean-silver-allata-9b1885395/)
