# Academic Defense Simulator

**Most RAG demos retrieve documents to answer your questions. This one reads
your research paper and uses it to interrogate you.**

Upload a thesis, capstone, or research document and face a live, adaptive
cross-examination from a panel of AI examiners — each grounded in your actual
document, each with a distinct role, and each quietly adjusting difficulty
based on how well you're holding up.

> **Status:** in active development. v0.3 is deployed and fully functional —
> multi-panelist sessions, adaptive difficulty, and end-of-session reports all
> work today. See [Roadmap](#roadmap) for what's ahead.

**[Try the live demo →](https://academic-defense-simulator.streamlit.app/)**

[HERO SCREENSHOT — full-panel live session, 4 panelists, mid-exchange]

---

## Why this is interesting

- **RAG inverted** — retrieval feeds *question generation*, not answering.
  Every question a panelist asks is grounded in a specific passage retrieved
  from your document, with the grounding programmatically verified per turn.
- **Adaptive difficulty you can't see** — every answer is scored internally
  (clarity, depth, grounding) via structured output; the scores steer question
  difficulty without ever being shown mid-session. The thermostat is hidden
  because a defense you can read isn't practice.
- **A panel, not a chatbot** — distinct examiner archetypes (methodology,
  literature, technical, ethics) hold character across the full session, with
  turn-taking, follow-up chains when a panelist smells weakness, and a
  Devil's Advocate who cross-references earlier answers to contest your
  strongest claim.
- **Eval-driven development** — model behavior was measured, not assumed:
  documented findings include a model downgrade decision reversed on evidence,
  per-session LLM call budgets predicted from design docs and then verified
  against live instrumentation, and prompt versioning stamped into every
  exported transcript.

## How it works

```
PDF ──► chunk (PyMuPDF, paragraph-aware) ──► relevance gate ──► embed locally
                                                                (MiniLM)
        ┌───────────────────────────────────────────────────────────┘
        ▼
  Defense profile (type, domain, panel) ──► persona generation
        ▼
  ┌─ Agent loop ────────────────────────────────────────────────┐
  │ retrieve chunk ► panelist asks grounded question ► you answer│
  │ ► internal scoring ► difficulty adjusts ► follow-up or next  │
  │   panelist (weakness = they keep the floor)                  │
  └──────────────────────────────────────────────────────────────┘
        ▼
  Scoring report — narrative, difficulty trajectory, per-panelist
  averages, pressure moments (did you recover, hold, or deteriorate?)
```

Key implementation choices, and why:

- **Structured state, not chat-history replay.** Each LLM call renders a fresh
  prompt from a typed session model rather than replaying a growing
  transcript — persona framing is re-injected every call and can't dilute
  over distance.
- **Grounding is enforced, not hoped for.** Every generated question carries a
  reference that is programmatically checked against the retrieved chunk
  (exact then fuzzy match). At high difficulty, failures trigger a
  retry-then-flag path — flagged turns are visible in the exported transcript.
- **Local embeddings, brute-force retrieval.** sentence-transformers +
  numpy cosine similarity. At a few dozen vectors per document, a vector
  database is complexity without payoff — this is a deliberate scale decision,
  recorded, with the upgrade path known.
- **Provider-isolated LLM layer.** All Gemini-specific code sits behind one
  boundary; every call passes through a single choke point that logs
  lifecycle and counts calls — which is how a real orchestration bug was
  caught (see below).

## Engineering process highlights

The decision record in [`docs/`](docs/) keeps retired decisions alongside
current ones — the reversals are documented, not erased.

- **Measured, not assumed:** per-session LLM call count was predicted from
  design docs (16 calls for a standard session), then verified with a counter
  at the provider boundary. The first instrumented run measured a session
  that terminated two turns early — the counter exposed an orchestration bug
  where one panelist's spent follow-ups silently blocked another's, which was
  characterized with line-level evidence before any fix was written.
- **Model fitness tested per task:** the cheaper dev-default model
  (gemini-3.1-flash-lite) was diagnosed unfit for judgment tasks — it inverted
  difficulty adjustments on weak answers and inflated clarity scores — so
  scoring-critical calls run on [VERIFY-model-name] regardless of dev
  defaults. Probe results are committed as permanent artifacts.
- **Prompts are versioned.** Every exported transcript is stamped with the
  PROMPT_VERSION that produced it, because a prompt wording change silently
  invalidates earlier verification — learned empirically, then enforced
  mechanically.
- **Failure modes taxonomized:** high-difficulty fabrication was split into
  two distinct classes (invented statistics vs. accurate-but-ungrounded
  paraphrase) with different fixes, rather than treated as one bug.

## Stack

Python · Gemini API (`google-genai`) · sentence-transformers
(`all-MiniLM-L6-v2`, local) · numpy retrieval · PyMuPDF · Pydantic ·
Streamlit · pytest (131 tests)

Business logic is Streamlit-free by rule — the UI is a thin layer over typed
models, which is the planned migration seam for a future FastAPI + React
frontend.

## Run it locally

```bash
git clone https://github.com/slvr123/Academic-Defense-Simulator.git
cd Academic-Defense-Simulator
pip install -r requirements.txt
# .env — see .env.example; requires a Gemini API key
streamlit run academic_defense_simulator/streamlit_app.py
```

## Roadmap

- **v0.3 (current, deployed):** multi-panelist orchestration, adaptive
  difficulty, Devil's Advocate, scoring report, relevance gate, call
  instrumentation
- **v0.4 (next):** session persistence, deeper evals, public API-key design
- **v1.0:** analytics, deployment hardening, full case study

Built by **Sean Silver Allata** — [GitHub](https://github.com/slvr123) ·
[LinkedIn](https://www.linkedin.com/in/sean-silver-allata-9b1885395/)
BS Computer Science, Technological Institute of the Philippines.
