"""Document relevance gate prompt (v0.3g Brief). Kept separate from
extraction_prompts.py/panelist_prompts.py — this judges the document itself, before
extraction or any panelist ever sees it."""

RELEVANCE_PROMPT = """The following are sampled excerpts from an uploaded document — taken from its
beginning and middle, not the whole document.

\"\"\"
{sampled_text}
\"\"\"

Identify what kind of document this actually is, and judge whether it is plausible
material for an academic or professional defense session — a thesis, capstone project,
research paper, grant proposal, certification portfolio, or similar work someone would
present and be questioned on. Unusual but legitimate defense material (e.g. a grant
proposal or certification portfolio) should still pass; only reject document kinds that
have no plausible defense context at all (a resume, an invoice, a novel excerpt, a
manual, correspondence, etc.).

Respond ONLY with JSON matching this schema, no other text:
{{"is_defense_material": true or false, "document_kind": "a short label for what this document actually is, e.g. 'research paper', 'resume', 'invoice', 'novel excerpt'", "reason": "one sentence explaining your judgment, written for the person who uploaded it"}}
"""
