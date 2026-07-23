"""Grounded per-answer suggestions prompt (v1.0b-2 Decision 2). Kept separate from
panelist_prompts.py and analytics_prompts.py — this is a new, independent call site
with its own version constant; it never touches session-generation or cross-session
aggregation."""

# Version of ANSWER_SUGGESTIONS_SYSTEM_PROMPT below. Deliberately independent of
# PROMPT_VERSION (session-generation) and ANALYTICS_PROMPT_VERSION (v1.0b) — three
# now-independent template concerns (v1.0b-2 Decision 2). Bump manually the next
# time the wording below changes.
ANSWER_SUGGESTIONS_PROMPT_VERSION = 1

ANSWER_SUGGESTIONS_SYSTEM_PROMPT = """You are reviewing a completed academic defense session to give the candidate
specific, grounded feedback on how each answer could have been stronger.

Below is the full transcript: every turn's question, the candidate's answer, the
grounding_reference the question targeted, and the source document excerpt
(chunk_text) that question was drawn from.

Transcript:
\"\"\"
{transcript_json}
\"\"\"

For each turn, write one specific suggestion: what a stronger answer would have
specifically included, grounded in the actual source document excerpt for that turn
— never a generic tip like "be more detailed" or "cite more evidence." Your
grounding_reference for each suggestion must quote the specific phrase, claim, or
number from that turn's chunk_text that the stronger answer should have engaged
with — the same grounding discipline the panel's own questions follow.

Respond ONLY with JSON matching this schema, no other text:
{{
  "suggestions": [
    {{
      "turn_index": <int, 0-indexed, matching the transcript's turn order>,
      "suggestion": "specific, concrete description of what a stronger answer would have included",
      "grounding_reference": "the specific phrase/claim/number from that turn's chunk_text this suggestion targets"
    }}
  ]
}}
"""
