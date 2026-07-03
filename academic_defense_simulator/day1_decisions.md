# Academic Defense Simulator — Day 1 Decisions

Design lock-in from Day 1 (design + study, no code). Ready to hand directly into the
Day 2 Claude Code session. See `academic-defense-simulator-plan.md` for the full
project plan and roadmap, and Project Instructions for build philosophy, coding
principles, and architecture rules (not duplicated here).

---

## 1. Defense Profile Schema

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator

class DefenseType(str, Enum):
    THESIS = "thesis"
    CAPSTONE = "capstone"
    OTHER = "other"

class OtherSubtype(str, Enum):
    ORAL_COMPS = "oral_comps"
    SCHOLARSHIP_PANEL = "scholarship_panel"
    CERTIFICATION_INTERVIEW = "certification_interview"
    GRANT_DEFENSE = "grant_defense"

class DefenseProfile(BaseModel):
    defense_type: DefenseType
    other_subtype: Optional[OtherSubtype] = None
    domain: str = Field(..., min_length=1, description="Free-text field/discipline")
    topic: str = Field(..., min_length=1, description="Short user-entered research title/summary")
    panel_size: int = Field(default=1, ge=1, le=5)
    difficulty_start: int = Field(default=2, ge=1, le=5)
    document_id: str  # UUID4, assigned at upload, keys the temp file cache

    @model_validator(mode="after")
    def check_other_subtype(self):
        if self.defense_type == DefenseType.OTHER and self.other_subtype is None:
            raise ValueError("other_subtype is required when defense_type is 'other'")
        return self
```

**Decisions:**
- `domain`/`topic` — free text, user-entered. No auto-extraction until v0.3+ (avoids an
  extra LLM call in the critical path for zero learning value at this stage).
- `panel_size` — schema allows 1–5, but v0.1/v0.2 app logic hardcodes panel selection to 1.
- `difficulty_start` — present now so the schema doesn't need a breaking change mid-week,
  but inert until v0.2 scoring exists.
- `document_id` — UUID4 generated at upload, keys a temp file cache. No content hashing
  until dedup actually matters.

---

## 2. Panelist Prompt Templates

One shared template, per-archetype config injected. Keeps 4 archetypes from drifting
out of sync when one gets tuned later.

```python
PANELIST_SYSTEM_PROMPT = """You are Dr. {panelist_name}, the {archetype_title} on a {defense_type} defense panel.

Your focus: {archetype_focus}

Context:
- Defense type: {defense_type}{other_subtype_line}
- Domain: {domain}
- Topic: {topic}

You have been given ONE excerpt from the candidate's research document. Generate exactly ONE grounded, specific question about this excerpt that a rigorous panelist in this role would ask. The question must:
- Reference something concrete from the excerpt (a method, a claim, a citation, a design choice, a number) — never a generic question that could apply to any document
- Stay in your lane: {archetype_lane}
- Match the target difficulty level: {difficulty_level}/5 (1 = foundational/clarifying, 5 = adversarial/stress-testing an assumption)

Document excerpt:
\"\"\"
{retrieved_chunk}
\"\"\"

Respond ONLY with JSON matching this schema, no other text:
{{
  "question": "the question text",
  "grounding_reference": "the specific phrase/claim/number from the excerpt this question targets",
  "difficulty_level": <int 1-5>
}}
"""

ARCHETYPE_CONFIG = {
    "methodology_expert": {
        "archetype_title": "Methodology Expert",
        "archetype_focus": "research design validity, sampling/data choices, methodological rigor, and whether the chosen approach actually answers the stated research question.",
        "archetype_lane": "do not ask about literature gaps, ethics, or implementation details unless they directly compromise methodological validity",
    },
    "literature_theory_specialist": {
        "archetype_title": "Literature & Theory Specialist",
        "archetype_focus": "gaps in cited literature, theoretical grounding, and whether claims are properly situated against existing work.",
        "archetype_lane": "do not ask about implementation mechanics or ethics unless a citation or theoretical claim is directly at stake",
    },
    "technical_implementation_reviewer": {
        "archetype_title": "Technical Implementation Reviewer",
        "archetype_focus": "how the system was actually built — tools, architecture choices, tradeoffs, and whether the implementation matches what was claimed.",
        "archetype_lane": "do not ask about literature grounding or ethics; stay on build decisions and technical tradeoffs",
    },
    "ethics_practicality_reviewer": {
        "archetype_title": "Ethics & Practicality Reviewer",
        "archetype_focus": "real-world applicability, limitations, deployment implications, and ethical considerations of the work.",
        "archetype_lane": "do not ask about methodology rigor or citation gaps unless they directly create a practical or ethical risk",
    },
}
```

**Decisions:**
- `panelist_name` kept — helps the model hold character.
- `grounding_reference` in the output isn't cosmetic — it's the cheapest available eval
  signal for retrieval quality.
- `difficulty_level` exists in v0.1 but is hardcoded to `2` (no scoring yet). Template
  needs zero changes when v0.2 scoring goes live.
- **v0.1 active panelist: Methodology Expert.** Chosen for domain-generality — every
  defense type has a methods/approach section, giving the cleanest signal for testing
  RAG retrieval before adding archetype variety.
- **Devil's Advocate intentionally not drafted.** It needs cross-panelist conversation
  context to stress-test other panelists' questions, not just a document chunk —
  structurally incompatible with this template. Belongs to v0.3 orchestration design.

---

## 3. RAG Chunking + Retrieval Strategy

- **PDF ingestion:** PyMuPDF, not pdfplumber. pdfplumber only earns its place if an
  archetype needs table reasoning later.
- **Chunking:** paragraph-aware, ~600–800 tokens/chunk, ~100 token overlap. Split on
  paragraph boundaries first, then pack — not blind character slicing.
- **Embeddings:** `sentence-transformers`, `all-MiniLM-L6-v2` — local, free, decoupled
  from LLM provider. Not OpenAI embeddings (avoids a paid vendor on a project chosen
  specifically to avoid API cost; quality difference is irrelevant at dozens-of-vectors scale).
- **Vector store:** numpy cosine similarity, no ChromaDB. MVP scale is a few dozen
  vectors — brute-force is instant.
- **Retrieval query:**

```python
def retrieve(query: str, chunks: list[Chunk], top_k: int = 1) -> list[Chunk]:
    """Embed query, cosine-match against chunk embeddings, return top_k.
    Has zero knowledge of archetypes, panelists, or personas — the caller
    decides what `query` is."""
```

  There's no user query yet for the first question, so the caller passes the active
  panelist's `archetype_focus` string as the query — the archetype's concern area IS
  the query. `top_k=1`, deterministic highest-cosine match, no randomness.
  - **v0.2 (deferred):** query must also fold in conversation state and exclude
    already-used chunks so follow-ups don't repeat a paragraph.
  - **No formal `Retriever` interface for swapping vector-store backends** (ChromaDB,
    FAISS, Pinecone, Qdrant, Weaviate) yet. Unlike the LLM provider swap — justified by
    real observed Gemini free-tier volatility plus a converged interface shape across
    LLM SDKs — there's no live pressure here, and vector DB products have genuinely
    divergent shapes. A module boundary + generic function signature already gives
    swap-readiness without guessing at an interface across five different products with
    only one real implementation to design against. Revisit at v1.0.

---

## 4. Internal Scoring Rubric

```python
from typing import Optional
from pydantic import BaseModel, Field

class AnswerScore(BaseModel):
    clarity: int = Field(..., ge=1, le=5, description="How clearly and directly the answer addressed the question")
    depth: int = Field(..., ge=1, le=5, description="Substantive reasoning shown, not just restating facts")
    grounding: int = Field(..., ge=1, le=5, description="How accurately and specifically the answer engaged with the document's actual content")
    difficulty_delta: int = Field(..., ge=-1, le=1, description="-1 = ease up, 0 = hold steady, 1 = escalate for the next question")
    primary_gap: Optional[str] = Field(None, description="The single most significant weakness or gap observed, if any — feeds the v0.3 end-of-session report. Null if the answer was strong.")
```

```python
SCORING_SYSTEM_PROMPT = """You are scoring a candidate's answer during a {defense_type} defense, in the voice of {panelist_name}, the {archetype_title}.

Question asked:
\"\"\"
{question}
\"\"\"

Candidate's answer:
\"\"\"
{answer}
\"\"\"

Relevant document excerpt this question was grounded in:
\"\"\"
{retrieved_chunk}
\"\"\"

Score the answer honestly and specifically — do not default to the middle of the scale. Then decide whether the next question should escalate, hold steady, or ease up in difficulty.

Respond ONLY with JSON matching this schema, no other text:
{{
  "clarity": <int 1-5>,
  "depth": <int 1-5>,
  "grounding": <int 1-5>,
  "difficulty_delta": <int -1, 0, or 1>,
  "primary_gap": "<the single most significant weakness observed, or null if the answer was strong>"
}}
"""
```

**Decisions:**
- `difficulty_delta` is its own model-judged field, not averaged from the three scores —
  a mediocre answer and a confidently-wrong-per-the-document answer can average
  similarly but deserve opposite difficulty responses.
- `primary_gap` does nothing in v0.2 — seeds the v0.3 Scoring Report without a schema
  change later.
- "Handling pushback" is **not** a per-turn scored field. It doesn't apply cleanly to a
  first question. Deferred to v0.3 as an aggregate insight computed from the pattern of
  `difficulty_delta`/`primary_gap` across a full session.
- v0.2 app logic: `next_difficulty = clamp(current_difficulty + difficulty_delta, 1, 5)`
  — no scoring math lives in Python.

---

## 5. Tech Stack — Resolved

| Component | Choice |
|---|---|
| LLM | Gemini API, `gemini-2.5-flash` |
| Embeddings | `sentence-transformers`, `all-MiniLM-L6-v2` |
| Vector store | numpy cosine similarity |
| PDF ingestion | PyMuPDF |
| UI | Streamlit (minimal until v0.3+) |
| Deployment | Streamlit Cloud or equivalent |

**Gemini model — `gemini-2.5-flash`, not Flash-Lite or Pro:**
- Pro is capped too tightly on the free tier (~5 RPM / 50 RPD) to build against.
- Flash-Lite's extra RPM headroom is irrelevant — this is a human-paced, one-on-one
  interactive loop, not a high-throughput batch job. The task (scoring answer depth/
  grounding, generating a pointed question) is judgment-heavy, which favors Flash's
  quality over Flash-Lite's speed.
- Avoided newer 3.x preview models — preview/experimental models get tighter, less
  stable free-tier limits, and Google has cut free-tier quotas without notice before.
  Since the LLM provider is already abstracted, upgrading later is a config change, not
  a rewrite.

**LLM provider abstraction:** required — Gemini's observed free-tier volatility plus a
converged interface shape across LLM SDKs (messages in, text/JSON out) makes this a
low-risk, high-value abstraction to build now, unlike the vector-store interface above.

