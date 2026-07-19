# Academic Defense Simulator

RAG-backed system where AI panelists read an uploaded research document and run
adversarial, adaptive defense sessions. Differentiator: RAG generates questions, not
answers. Continuous project, not a one-week deliverable — don't over-engineer the
current version, don't make choices that box in later versions either.

Full plan: `academic-defense-simulator-plan.md`
Versioned roadmap: `docs/ROADMAP.md` — standing source of truth for version
sequencing and sub-version scope
Current locked design decisions: `docs/v0.2-scoring-model-swap-decisions.md` (most
recent; supersedes `docs/v0.1-foundations-decisions.md` and
`docs/v0.2-agent-loop-decisions.md` on overlapping points — see that file's Context
section for what's superseded)
Current build scope: `docs/briefs/` (see `docs/ROADMAP.md` for the active brief)

## Tech Stack
- Python, Pydantic throughout for structured data
- LLM: Gemini API via `google-genai` (not the deprecated `google-generativeai`) —
  isolated behind an `LLMProvider` interface, see `llm/provider.py`. Model selected via
  `GEMINI_MODEL` env var: `gemini-3.1-flash-lite` (dev default) / `gemini-2.5-flash`
  (reserved for final verification runs) — see `docs/v0.2-scoring-model-swap-decisions.md`
- Embeddings: `sentence-transformers`, `all-MiniLM-L6-v2`
- Vector store: numpy cosine similarity, no ChromaDB — MVP scale doesn't need it
- PDF ingestion: PyMuPDF
- UI: Streamlit, minimal until v0.3+

## Architecture Rules
- Separate business logic from UI, RAG from LLM logic, prompt templates from Python code
- No business-logic module imports `streamlit` — the Streamlit layer orchestrates I/O
  only. This is the seam a future FastAPI wrapper slots into; protect it in every session.
- Provider-specific API code isolated (only `gemini_provider.py` imports `google.genai`)
- No global mutable state
- Small, single-purpose functions; composition over inheritance
- Avoid premature abstraction — don't build interfaces ahead of real pressure (e.g. no
  formal vector-store interface yet; only one real implementation exists)

## Definition of Done

A task is complete only if:

- It works.
- It is understandable.
- It is type-safe.
- Existing functionality still works.
- Temporary debugging code has been removed.
- It is verified with real evidence, not summarized. Show actual output —
  command output, stdout, raw JSON, or return values — for each item, not
  just a description of what happened. If a case can't be triggered live
  (rare API errors, edge-case failures), state exactly how it was simulated
  (mock, monkeypatch, injected fault) and show the output from that forced
  run.

## Working With Sean
- Precise and opinionated over hedged — push back if something won't hold up
- Explain non-obvious architectural decisions, don't over-explain fundamentals
- Every feature is a complete vertical slice — no disconnected components built on
  the promise they'll connect later
- Decisions files are named `docs/v{version}-{slug}-decisions.md`; tactical
  Code-session briefs live in `docs/briefs/`. If a brief conflicts with an earlier
  decisions file, the more recent, more specific doc wins — but flag the conflict,
  don't silently pick one.
