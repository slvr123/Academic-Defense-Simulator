"""Domain/topic extraction prompt (0.3a Decision 4). Kept separate from
panelist_prompts.py — extraction is an ingestion-time step, not panelist behavior."""

EXTRACTION_PROMPT = """The following is the opening excerpt of an academic research document.

\"\"\"
{document_head}
\"\"\"

Identify:
- "domain": the academic field or discipline this work belongs to (2–5 words)
- "topic": the research title or a faithful short summary of it (at most 15 words) — prefer the document's own title verbatim if present

Respond ONLY with JSON matching this schema, no other text:
{{"domain": "...", "topic": "..."}}
"""
