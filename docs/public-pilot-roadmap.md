# Public pilot roadmap

Date: 2026-09-23
Branch: `feat/public-pilot`
Status: Planned; implementation has not started.

## Goal and provenance

I want other people to complete a useful defense practice session without my help and leave knowing what to practice next.
Codex proposed a public-pilot sequence during our project review; I confirmed the direction on 2026-09-23 and added a priority: make panel selection much more visible and make the interface more impressive.
I am keeping Streamlit for this release and improving the complete experience before adding more features.
This roadmap is the active implementation sequence for the public pilot and supersedes conflicting next-step statements in `ROADMAP.md`.
Earlier decision documents remain the historical record; changes to their behavior are called out below.
I will implement each phase as a reviewable vertical slice on this branch, with evidence recorded before marking it complete.

## Starting evidence

- I have a multi-panelist application with adaptive turns, reports, optional local persistence, analytics, exports, and voice.
- The inspected baseline is commit `47d927b`, which matched GitHub main during the review.
- The local suite returned 332 passing tests and one failure caused by a missing, gitignored session fixture.
- The intake and recorded-session report rendered in a network-disabled Streamlit check; this was not a new live-provider or hosted end-to-end test.
- The loaded embedding model accepts 256 tokens, while 48 of 49 sample3 chunks exceed that limit.
- My existing evaluations document scoring uncertainty and retrieval-related question drift.
- Current panel selection is a multiselect labeled "Panel" within the defense-profile form.
- I have not established current hosted reliability or current provider quotas through this review.

## Planned user journey

Try a sample or upload a PDF -> confirm the research profile -> choose a panel -> review session length and audio -> practice -> receive actionable feedback.

I want the recommended path to work with minimal setup, while keeping panel selection obvious and optional customization available.
I will show clear progress through preparation, practice, and feedback without turning every small choice into another screen.

## Phase 0 - Reproducible baseline

- [ ] Replace the missing real-session test dependency with a committed synthetic old-format fixture that preserves the migration check.
- [ ] Establish a repeatable test command and CI check that need neither private documents nor live credentials.
- [ ] Record the baseline revision, test results, known limitations, and existing evaluation artifacts.
- [ ] Distinguish automated results, human judgments, and unverified claims in evaluation records.

Acceptance: a fresh checkout runs the offline suite successfully without machine-specific files or paid API calls.

## Phase 1 - Visible panel selection and stronger presentation

This is my first user-facing implementation slice.
I will reproduce the current intake flow in the browser before editing it and capture comparable before/after views.

- [ ] Give "Choose your panel" its own prominent section after profile confirmation and before the start action.
- [ ] Replace the compact multiselect as the primary control with accessible selectable cards for eligible archetypes.
- [ ] Show each role's title, existing icon, short plain-language focus, and an unmistakable selected/unselected state.
- [ ] Show "Choose 1-3 panelists" and a live selection count; explain the limit rather than silently ignoring extra selections.
- [ ] Offer a clearly labeled recommended selection and a simple way to restore it, using existing deterministic composition rules.
- [ ] Show Devil's Advocate as an always-included card and explain its purpose separately from the selectable count.
- [ ] Keep selection visible by default; keep optional names and avatar changes in secondary controls.
- [ ] Preserve valid selections across reruns and explain changes when the defense type changes the eligible roster.
- [ ] Show the final panel summary beside the primary start action and prevent starting with an invalid selection.
- [ ] Improve spacing, typography, contrast, and action hierarchy while retaining a coherent academic-defense visual identity.
- [ ] Adapt the cards to narrow screens and verify keyboard navigation, focus visibility, accessible labels, and non-color selection cues.

Acceptance: a first-time tester can find, change, and confirm the panel without prompting.
I will verify desktop and mobile-width layouts in a real browser, preserve the 1-3 domain-panelist rule plus Devil's Advocate, and test the existing customization and type-change flows.
This changes the intake presentation, not the panel's eligibility rules or orchestration.

## Phase 2 - Trustworthy retrieval and questions

- [ ] Create a small labeled retrieval baseline across the two existing evaluation documents, including relevant body sections and irrelevant front matter.
- [ ] Measure current retrieval before changing it, with explicit relevance criteria and a held-out comparison set.
- [ ] Compare tokenizer-aware chunks that fit the encoder with the current chunker, including overlap and long-paragraph handling.
- [ ] Preserve source page information through extraction, retrieval, turns, and exports with compatibility for older sessions.
- [ ] Let the user inspect the source passage and page behind a question without exposing hidden scores.
- [ ] Measure question relevance, panelist-lane accuracy, unsupported premises, and grounding-check false positives/negatives.
- [ ] Reproduce and test Devil's Advocate follow-up behavior; make retained turns explicitly respond to the preceding answer and gap where appropriate.
- [ ] Handle short or exhausted documents gracefully instead of failing during question generation.
- [ ] Add a low-friction "This question seems incorrect" action, with optional reason and explicit control over sharing document text.

Acceptance: the chosen retrieval change improves the labeled baseline without unexplained regressions on either document.
I will set evaluation thresholds before running the comparison and keep the raw outputs, judgments, model identity, and prompt/chunker versions together.
I will regenerate and verify the sample sidecar if the ingestion contract changes.

## Phase 3 - Fair, actionable feedback

- [ ] Build weak, partial, and strong answer examples for the actual questions asked, rather than reusing generic strong-answer text.
- [ ] Compare scoring with human judgments and repeat selected cases to measure variability.
- [ ] Choose any rubric or model changes from this evidence, including their latency and cost implications.
- [ ] Frame scores as practice indicators and avoid suggesting that a displayed percentage is a calibrated academic grade.
- [ ] Make the report lead with specific strengths, the most important gaps, and concrete next practice actions.
- [ ] Link feedback to the relevant exchange and source material; keep internal scores hidden during practice.
- [ ] Persist model identifiers, prompt versions, chunking/embedding identity, and cumulative call counts, including after resume.
- [ ] Mark mixed-configuration resumed sessions accurately rather than labeling the entire session with the latest prompt version.

Acceptance: the evaluation distinguishes answer quality meaningfully, known uncertainty is documented, and report claims trace back to actual exchanges.
If calibration remains weak, I will favor specific qualitative feedback over prominent numerical grades.

## Phase 4 - A complete short session

- [ ] Make the hosted demo a deliberately short session that finishes with a report.
- [ ] Design the short-session turn plan so the displayed panel matches who actually participates.
- [ ] Explain session length, panel behavior, audio, and the final report before the session starts.
- [ ] Let users end early and receive a report for completed answers; handle zero-answer exits honestly.
- [ ] Keep a visible mute/play control and make the whole experience usable with text alone.
- [ ] Preserve the sample-document and recorded-example paths as easy ways to understand the product.
- [ ] Keep personal API keys optional for trying the product and explain longer-session access clearly.

Acceptance: a first-time demo user reaches useful feedback without an API key, an unexpected quota-abort screen, or manual help.
This deliberately replaces the current demo-cap abort behavior with normal short-session completion.
The turn plan, report budget, and early-end behavior will be specified before implementation.

## Phase 5 - Recovery and responsiveness

- [ ] Preserve the typed answer through provider errors and retries.
- [ ] Retry only the failed step and prevent duplicate submission or replay of completed work.
- [ ] Provide partial transcript download during practice and after a failed step.
- [ ] Show meaningful progress during ingestion, question generation, scoring, and report construction.
- [ ] Measure cold start and time to first question; move avoidable embedding work off the sample path's critical path where evidence supports it.
- [ ] Review timeout cleanup and concurrent request pacing under overlapping sessions.
- [ ] Decide the hosted refresh/reconnect recovery contract and communicate its limits clearly.
- [ ] If hosted persistence is included, implement ownership checks, durable storage, expiry, and deletion together.
- [ ] Hide analytics entry points when no usable history exists or explain how to create that history.

Acceptance: forced timeout, provider failure, repeated submit, and reconnect scenarios preserve completed work and give the user a clear next action.
I will not promise exactly-once provider billing when a timed-out external request may already have been processed.

## Phase 6 - Public operation and privacy

- [ ] Replace restart-sensitive demo accounting with durable, concurrency-safe server-enforced limits.
- [ ] Reserve enough budget for completing a started short session and its report.
- [ ] Account for questions, scoring, relevance, reports, retries, and voice separately.
- [ ] Verify current provider availability, limits, and deployment behavior before setting public limits.
- [ ] Explain what document text is sent to Gemini and what question text is sent to the voice provider before use.
- [ ] Document retention and deletion behavior, including local-only versus hosted storage.
- [ ] Collect operational timings and error categories without raw answers, documents, or credentials by default.
- [ ] Require explicit user action before diagnostic feedback includes document excerpts.
- [ ] Review secret handling, deployment settings, and abuse controls against the actual hosted environment.

Acceptance: concurrent cap tests, restart tests, credential checks, and deletion/retention checks pass in the intended hosting environment.
Storage technology and final public usage limits remain design decisions within this phase, not assumptions already settled by this roadmap.

## Phase 7 - Small pilot and release gate

- [ ] Invite approximately five consenting testers preparing for a defense, with different documents and levels of technical experience.
- [ ] Observe whether they can choose a panel, start, finish, and explain what to practice next without guidance.
- [ ] Measure time to first question, completion rate, failed-step rate, and reported irrelevant questions.
- [ ] Collect feedback on panel discoverability, question fairness, report usefulness, audio, and mobile usability.
- [ ] Prioritize repeated confusion and reliability failures before adding features.
- [ ] Refresh screenshots, README, and the case study to match the actual pilot release.
- [ ] Run one complete hosted sample session and one permitted uploaded-document session, including report and export.

Acceptance: I have evidence that testers can complete the core journey independently, with no unresolved failures that lose work or block completion.
I will record remaining limitations and decide whether to widen access from the observed pilot results.

## Delivery order and boundaries

I will complete Phase 0, then implement the panel-selection slice first so the visible improvement arrives early.
Retrieval and scoring work follow before I treat the app's feedback as ready for wider use.
The short-session experience, recovery, and public-operation controls must all be complete before the pilot release gate.
I will keep source changes separate from existing unrelated local edits and record validation with each implementation slice.
I will extract UI sections into smaller modules where the touched code benefits, while keeping business logic independent of Streamlit.
I am deferring additional archetypes, speech input, more avatar systems, and a frontend rewrite until pilot evidence justifies them.
Creating this branch and roadmap does not deploy the app or mark any planned feature as implemented.
