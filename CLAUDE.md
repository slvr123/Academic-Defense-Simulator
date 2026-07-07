# Academic Defense Simulator

RAG-backed system where AI panelists read an uploaded research document and run
adversarial, adaptive defense sessions. Differentiator: RAG generates questions, not
answers. Continuous project, not a one-week deliverable — don't over-engineer the
current version, don't make choices that box in later versions either.

Full plan: `docs/academic-defense-simulator-plan.md`
Current locked design decisions: `docs/day1_decisions.md`
Current build scope: `docs/day6_decisions.md` (update this pointer each time a new day's
brief is committed — it's the single source of truth for "what am I building right now")

All Day-N decision/build/verification docs, eval results, and ad-hoc design docs live in
`docs/`. Only `CLAUDE.md` itself stays at repo root.

## Tech Stack
- Python, Pydantic throughout for structured data
- LLM: Gemini API, `gemini-3.1-flash-lite` default (env-swappable via `GEMINI_MODEL`; see
  `docs/day4_decisions.md`), via `google-genai` (not the deprecated `google-generativeai`) —
  isolated behind an `LLMProvider` interface, see `llm/provider.py`
- Embeddings: `sentence-transformers`, `all-MiniLM-L6-v2`
- Vector store: numpy cosine similarity, no ChromaDB — MVP scale doesn't need it
- PDF ingestion: PyMuPDF
- UI: Streamlit, minimal until v0.3+

## Architecture Rules
- Separate business logic from UI, RAG from LLM logic, prompt templates from Python code
- Provider-specific API code isolated (only `gemini_provider.py` imports `google.genai`)
- No global mutable state
- Small, single-purpose functions; composition over inheritance
- Avoid premature abstraction — don't build interfaces ahead of real pressure (e.g. no
  formal vector-store interface yet; only one real implementation exists)

## Definition of Done
A task is complete only if: it works, it's understandable, it's type-safe, existing
functionality still works, and temp debugging code is removed.

## Working With Sean
- Precise and opinionated over hedged — push back if something won't hold up
- Explain non-obvious architectural decisions, don't over-explain fundamentals
- Every feature is a complete vertical slice — no disconnected components built on
  the promise they'll connect later
- If a build-day brief conflicts with something in `docs/day1_decisions.md`, the more
  recent, more specific doc wins — but flag the conflict, don't silently pick one
