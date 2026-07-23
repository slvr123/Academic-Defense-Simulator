"""Cross-session gap-clustering prompt (v1.0b Decision 2). Kept separate from
panelist_prompts.py and every other prompt module — this call never touches a live
session, so it doesn't share PROMPT_VERSION or any other version constant with the
session-generation templates."""

# Version of GAP_CLUSTERING_SYSTEM_PROMPT below. Deliberately independent of
# PROMPT_VERSION (session-generation) and ANSWER_SUGGESTIONS_PROMPT_VERSION
# (v1.0b-2) — three separate template concerns, three separate counters (v1.0b
# Decision 2). Bump this manually the next time the wording below changes; that
# bump is also the cache-invalidation signal for `analytics.gap_theme_cache_key`
# (v1.0b Decision 3).
ANALYTICS_PROMPT_VERSION = 1

GAP_CLUSTERING_SYSTEM_PROMPT = """The following is a list of "primary gap" observations — each one the single most
significant weakness a panelist identified in a candidate's answer, across multiple
academic defense practice sessions. Some of these describe the same underlying
weakness in different words; others are genuinely distinct.

Gaps:
\"\"\"
{gaps_list}
\"\"\"

Group these into recurring themes by underlying meaning, not surface wording. Each
theme should represent one real, distinct weakness pattern that shows up more than
once (a gap that appears only once can still form its own theme if it doesn't
meaningfully overlap with any other). Every gap in the list must be assigned to
exactly one theme. Use the gap's exact original text, verbatim, in supporting_gaps.

Respond ONLY with JSON matching this schema, no other text:
{{
  "themes": [
    {{
      "theme_label": "short human-readable name for this recurring weakness, e.g. 'Quantitative justification'",
      "supporting_gaps": ["<verbatim gap text>", "..."],
      "occurrence_count": <int, equal to len(supporting_gaps)>
    }}
  ]
}}
"""
