"""The panel's language (v2.0 M16 Decision 3). backend/language.py fills it in; this
module holds only the words.

The same line goes into v1's three question prompts (through their `{language_line}`
slot, M16 Decision 2) and into the closing-line prompt. English sessions pass nothing,
so their prompts are exactly as before.

Amendment 3 (M16 results, section 13) added the sentence about the very first sentence:
the turn-1 opener, which has nothing to acknowledge, was coming out all English.
"""

TAGLISH_LINE = (
    "Speak in Taglish: the natural, conversational mix of Filipino and English that Filipino "
    "thesis and capstone panels actually use. Use Taglish from your very first sentence, "
    "including the opening question of the defense. Keep technical terms, the paper's own terms, "
    "statistical terms, numbers and citations in English. Don't translate or paraphrase quotes "
    "from the excerpt; quote them as written. Use respectful register (po and opo are fine but "
    "not in every sentence). Don't use deep or formal Tagalog that a panel wouldn't say aloud. "
    "Your output field names and format stay exactly as instructed below."
)

# The fixed closing line (M15 Decision 4) in a Taglish session: the fallback, and what
# ADS_CLOSING_LINES=fixed says. The English one is backend/closing.py's FALLBACK_LINE.
TAGLISH_FALLBACK_LINE = "Okay, thank you. Iyan lang muna sa akin."
