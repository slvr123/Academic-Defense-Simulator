# Academic Defense Simulator — Day 2 Build Brief: v0.1 "The Skeleton"

Scope for this Code session. Everything here should be resolvable without design
decisions mid-session — if something's ambiguous, it's flagged at the bottom for you
to decide before you open Code, not during.

Reference: `day1_decisions.md` (schemas, prompts, RAG strategy — locked, paste verbatim).

---

## Scope

Upload document → chunk + embed → retrieve one chunk → one panelist (Methodology
Expert) asks one grounded question → user answers in terminal → session ends.

**Explicitly out of scope for v0.1:**
- Multi-turn / follow-up questions
- Scoring (`AnswerScore`) — model is designed, not wired up. The answer is captured and
  printed, nothing more.
- Multi-panelist orchestration — only Methodology Expert runs
- Streamlit — terminal I/O only per the UI-by-version table
- Dynamic persona generation — archetype config is static, injected verbatim from
  `ARCHETYPE_CONFIG`

---

## Module Structure

```
academic_defense_simulator/
├── main.py                    # CLI orchestration — the only place that "wires" things together
├── config.py                  # loads GEMINI_API_KEY from .env
├── models/
│   ├── defense_profile.py     # DefenseProfile, DefenseType, OtherSubtype (from day1_decisions.md, verbatim)
│   └── panelist_output.py     # PanelistQuestion — NEW, see below
├── rag/
│   ├── chunking.py            # PDF -> paragraph-aware chunks (PyMuPDF)
│   ├── embeddings.py          # sentence-transformers wrapper
│   └── retrieval.py           # retrieve(query, chunks, top_k) — from day1_decisions.md, verbatim
├── llm/
│   ├── provider.py            # LLMProvider abstract interface
│   └── gemini_provider.py     # Gemini implementation
└── prompts/
    └── panelist_prompts.py    # PANELIST_SYSTEM_PROMPT, ARCHETYPE_CONFIG — from day1_decisions.md, verbatim
```

Mirrors the architecture rules already in Project Instructions: RAG has zero knowledge
of LLM/prompts, provider-specific code isolated to `gemini_provider.py`, prompt
templates live outside Python logic.

---

## New model — not in Day 1 decisions

Day 1 covered the **inputs** to the panelist prompt. It didn't define the **output**
schema as a standalone Pydantic model (the JSON shape was only shown inline in the
prompt template). Needed now to validate/parse the Gemini response:

```python
# models/panelist_output.py
from pydantic import BaseModel, Field

class PanelistQuestion(BaseModel):
    question: str
    grounding_reference: str
    difficulty_level: int = Field(..., ge=1, le=5)
```

---

## LLM Provider — confirmed current Gemini API shape

Package is `google-genai` (not `google-generativeai` — that one's deprecated). Structured
output takes a Pydantic model directly as `response_schema`.

```python
# llm/provider.py
from abc import ABC, abstractmethod
from typing import TypeVar, Type
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class LLMProvider(ABC):
    @abstractmethod
    def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        """Send a fully-rendered prompt, return a validated instance of response_model.
        Implementations own all provider-specific request/response shape handling."""
        ...
```

```python
# llm/gemini_provider.py
from google import genai
from google.genai import types
from typing import TypeVar, Type
from pydantic import BaseModel
from llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate_structured(self, prompt: str, response_model: Type[T]) -> T:
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_model,
            ),
        )
        return response_model.model_validate_json(response.text)
```

Note: `response.parsed` also returns a parsed object directly, but that's Gemini-SDK
convenience behavior. Going through `response_model.model_validate_json(response.text)`
explicitly keeps the parsing logic provider-agnostic — an OpenAI or Anthropic provider
swapped in later won't have `.parsed`, but every provider gives you text you can validate.

---

## `main.py` flow (explicit, in order)

1. CLI prompt: PDF path
2. CLI prompt: `defense_type`, `domain`, `topic` (raw `input()`, no validation polish —
   that's not this slice's job)
3. `document_id = str(uuid4())`
4. Build `DefenseProfile`
5. Extract text + chunk PDF (`rag/chunking.py`)
6. Embed all chunks (`rag/embeddings.py`)
7. `query = ARCHETYPE_CONFIG["methodology_expert"]["archetype_focus"]`
8. `retrieved_chunk = retrieve(query, chunks, top_k=1)[0]`
9. Fill `PANELIST_SYSTEM_PROMPT` (panelist_name hardcoded — see open decisions;
   `difficulty_level=2` per Day 1)
10. `provider.generate_structured(prompt, PanelistQuestion)`
11. Print the question to terminal
12. `input()` for the answer, print `"Answer recorded. Session ended."` — no scoring,
    no persistence beyond that print

---

## Dependencies

```
pymupdf
sentence-transformers
google-genai
pydantic
numpy
python-dotenv
```

`.env` holds `GEMINI_API_KEY`, loaded in `config.py` via `python-dotenv`. `.env` stays
out of version control.

---

## Definition of Done (this slice)

- Full flow runs end-to-end on a real PDF: one grounded question produced, one typed
  answer accepted, clean termination
- `PanelistQuestion.grounding_reference` actually corresponds to something in the
  retrieved chunk (spot-check by eye — this is your retrieval-quality signal per Day 1)
- Type-safe throughout, Pydantic for all structured data
- No global mutable state
- `gemini_provider.py` is the only file that imports `google.genai`
- `rag/` has zero imports from `llm/` or `prompts/`
- No leftover debug prints or hardcoded test paths in the committed version

---

## Flagged open decisions (confirm before opening Code, not during)

1. **Error handling depth.** Recommend bare-minimum for v0.1 — let it fail loudly with a
   readable message (bad path, unreadable PDF, malformed API response). Full error
   handling is explicitly scoped to Day 5, don't pull it forward.
2. **`panelist_name` placeholder.** Dynamic naming is v0.3. Pick one static name now
   (e.g. `"Dr. Reyes"`) and move on — not worth deliberating.
3. **Profile intake via `input()` vs. a hardcoded test profile.** Recommend `input()` —
   costs nothing extra and you'll want it the moment you test a second PDF.
