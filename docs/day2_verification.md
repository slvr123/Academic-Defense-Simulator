# Academic Defense Simulator — Day 2 Verification: v0.1 "The Skeleton"

This is evidence, not a summary. Every number below comes from actual raw stdout of
`scripts/inspect_rag.py` and `main.py`, not from Claude Code's own report of its work —
"wired" is not the same as "tested," and this file exists to keep that distinction real.

---

## Environment

- `requirements.txt` generated from the dependency list in `day2_build_brief.md`
- `.venv` built, all packages installed cleanly: `pymupdf`, `sentence-transformers`,
  `google-genai`, `pydantic`, `numpy`, `python-dotenv`
- `.env` / `.env.example` created; real `GEMINI_API_KEY` filled in locally, never committed

---

## RAG Pipeline — verified via `scripts/inspect_rag.py`

Test document: a real 224-page academic paper (Library Management System for DAZSMA —
software engineering capstone).

- **Chunking:** 93 chunks produced, paragraph-aware, sizes ranging ~64–1162 tokens per
  chunk — consistent with the ~600–800 token target from `day1_decisions.md`
- **Retrieval query:** `methodology_expert` archetype's `archetype_focus` string, used
  verbatim as specified — no user query yet, as designed for v0.1
- **Top-5 cosine similarity scores:**
  1. chunk [52] — 0.3899
  2. chunk [70] — 0.2926
  3. chunk [44] — 0.2902
  4. chunk [8] — 0.2543
  5. chunk [73] — 0.2479
- **Margin:** #1 to #2 is ~0.097, a ~25% relative gap — a confident match, not a near-tie
- **Winning chunk (52):** the paper's purposive sampling section — 20 respondents split
  10 IT / 10 non-IT, Likert scale evaluation criteria. A genuinely strong methodological
  target, not a weak or generic match.
- **Rendered prompt confirmed correct:** the filled `PANELIST_SYSTEM_PROMPT` reads
  `"You are Dr. Reyes"` — `panelist_name` is set to `"Reyes"`, the template supplies
  `"Dr."`. The double-Dr. bug is genuinely fixed, verified directly in the rendered
  output, not claimed secondhand.

---

## End-to-End Flow — verified via `main.py`

- Full 12-step flow (intake → profile → chunk → embed → retrieve → prompt → generate →
  print → answer) ran successfully on the same test PDF
- Gemini returned a valid `PanelistQuestion`:
  - `question`: challenges whether a 20-respondent sample is adequate to support a
    "holistic evaluation" claim spanning five criteria (functionality, usability,
    reliability, security, effectiveness)
  - `grounding_reference`: a real, verbatim quote from the sampling section — not
    fabricated or generic
  - `difficulty_level`: 2, as expected (hardcoded per Day 1, unchanged until v0.2)

---

## Known Non-Issues (investigated, confirmed benign)

- A handful of chunk previews appeared garbled mid-word in one paste into chat —
  traced to a copy/paste artifact in the conversation itself, not real PyMuPDF
  extraction corruption. Confirmed by the fact that a static hardcoded prompt string
  showed the same corruption pattern in the same paste, which PyMuPDF could not have
  caused.
- An HF Hub authentication warning interleaved with stdout mid-run during embedding
  model load — cosmetic stderr/stdout interleaving, unrelated to correctness.
- Stray page-number artifacts embedded in extracted chunk text (e.g. `111`, `112`) —
  normal PyMuPDF behavior on a paginated PDF; harmless noise the LLM ignores.

---

## Verdict

**v0.1 is done.** The core mechanic — RAG retrieval feeding question generation, not
answer generation — is confirmed working end-to-end on a real 224-page paper, with a
confidently-separated retrieval match and a specific, grounded, non-generic question
as output. Not just internally consistent code — actually tested against real input.
