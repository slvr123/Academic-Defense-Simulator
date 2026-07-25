"""Panel composition and persona generation.

Composition (`compose_panel`) is a pure, zero-LLM function: deterministic mapping from
`(defense_type, other_subtype)` to a priority-ordered archetype list, filtered to the
user's `selected_archetypes`. See `docs/v0.3a-persona-generation-decisions.md` Decision 1
and `docs/v0.3e-panel-composition-decisions.md` Decision 4 (direct selection replaced
the old panel_size truncation).

`generate_panel` is the one-upfront-call persona generation step (Decision 2): a single
structured-output call producing the whole panel's names/framing, with Python-side
roster validation and a retry-then-static-fallback failure path — a deliberate departure
from 5a's fail-clean rule (a generic persona degrades aesthetics only, not the transcript).
"""

from __future__ import annotations

import logging
from pathlib import Path

from academic_defense_simulator.llm.provider import LLMProvider, LLMProviderError
from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
from academic_defense_simulator.models.panelist import GeneratedPanelist, Panelist, PanelGeneration
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG, PERSONA_GENERATION_PROMPT

logger = logging.getLogger(__name__)

# v0.3j Decision 3 — curated icon set + per-archetype defaults. Lives here rather than
# in prompts/panelist_prompts.py (where ARCHETYPE_CONFIG sits) because icons are roster
# material, not prompt text — nothing in this block ever reaches an LLM call.
PANELIST_ICON_CHOICES = [
    "🎓", "🔬", "📚", "🛠️", "⚖️", "⚔️", "🧠", "📊", "🔍", "🧪", "🏛️", "✒️",
    "🎯", "📈", "💼",
]

ARCHETYPE_DEFAULT_ICONS: dict[str, str] = {
    "methodology_expert": "🔬",
    "literature_theory_specialist": "📚",
    "technical_implementation_reviewer": "🛠️",
    "ethics_practicality_reviewer": "⚖️",
    "devils_advocate": "⚔️",
    # v1.1a Decision 7 — appended, not inserted, so the existing five keys' index
    # positions (and therefore their modulo-distributed default image stems in
    # archetype_default_icon) are unchanged.
    "problem_objectives_reviewer": "🎯",
    "statistical_analysis_reviewer": "📊",
    "results_conclusions_reviewer": "📈",
    "industry_practice_reviewer": "💼",
}

# v0.3j amendment (2026-07-20): curated image icons. Sean drops icon files into
# assets/icons/ and each one becomes a picker choice; a file named exactly after an
# archetype key (e.g. methodology_expert.png) becomes that archetype's default. The
# emoji list above stays as the fallback while the directory is empty, and as the
# export/sidebar-safe identifier space. An icon value is therefore either an emoji
# glyph or an image file's stem — `image_icon_path` disambiguates.
ICON_ASSETS_DIR = Path(__file__).parent / "assets" / "icons"
_ICON_FILE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _image_icon_files(assets_dir: Path = ICON_ASSETS_DIR) -> dict[str, Path]:
    """{stem: path} for every icon image in the assets directory, sorted by stem.
    Rescanned on call — the directory is tiny and this keeps newly dropped files
    live without a restart."""
    if not assets_dir.is_dir():
        return {}
    files = [p for p in sorted(assets_dir.iterdir()) if p.suffix.lower() in _ICON_FILE_SUFFIXES]
    return {p.stem: p for p in files}


def image_icon_path(icon: str, assets_dir: Path = ICON_ASSETS_DIR) -> Path | None:
    """The image file behind an icon identifier, or None when the identifier is an
    emoji glyph (or a stale stem whose file was removed — emoji fallback then)."""
    return _image_icon_files(assets_dir).get(icon)


def list_icon_choices(assets_dir: Path = ICON_ASSETS_DIR) -> list[str]:
    """Picker options: curated image stems only, once any exist (v0.3j amendment 2:
    emoji retired from the picker at Sean's request). The emoji set remains solely
    as the empty-directory fallback so a fresh clone without assets still works."""
    stems = list(_image_icon_files(assets_dir))
    return stems if stems else PANELIST_ICON_CHOICES


def archetype_default_icon(archetype_key: str, assets_dir: Path = ICON_ASSETS_DIR) -> str:
    """Default resolution, in priority order: an image named exactly after the
    archetype key; else a deterministic spread of the available images across the
    archetypes (stable archetype order × sorted stems, so every archetype gets a
    distinct default while enough images exist); else the emoji default."""
    stems = list(_image_icon_files(assets_dir))
    if archetype_key in stems:
        return archetype_key
    if stems:
        archetype_order = list(ARCHETYPE_DEFAULT_ICONS)
        return stems[archetype_order.index(archetype_key) % len(stems)]
    return ARCHETYPE_DEFAULT_ICONS[archetype_key]

# Devil's Advocate is an orchestration feature, not a fifth composition-table entry
# (docs/v0.3b-multi-panelist-orchestration-decisions.md Decision 2) — it is appended to
# every session's roster by `compose_full_roster`, always last, never selectable and
# never listed in PANEL_COMPOSITION (docs/v0.3e-panel-composition-decisions.md Decision 5).
DEVILS_ADVOCATE_KEY = "devils_advocate"

FALLBACK_PANELISTS: dict[str, GeneratedPanelist] = {
    "methodology_expert": GeneratedPanelist(
        archetype_key="methodology_expert",
        panelist_name="Dela Cruz",
        persona_framing="You are a rigorous methodologist known for pressing candidates on whether their chosen approach actually answers their stated research question.",
    ),
    "literature_theory_specialist": GeneratedPanelist(
        archetype_key="literature_theory_specialist",
        panelist_name="Garcia",
        persona_framing="You are a widely read theorist known for pressing candidates on gaps between their claims and the literature they cite.",
    ),
    "technical_implementation_reviewer": GeneratedPanelist(
        archetype_key="technical_implementation_reviewer",
        panelist_name="Gregorio",
        persona_framing="You are a hands-on builder known for pressing candidates on whether the implementation matches what was claimed and why each tool was chosen.",
    ),
    "ethics_practicality_reviewer": GeneratedPanelist(
        archetype_key="ethics_practicality_reviewer",
        panelist_name="Bautista",
        persona_framing="You are a pragmatic reviewer known for pressing candidates on real-world applicability, limitations, and the implications of deploying their work.",
    ),
    "devils_advocate": GeneratedPanelist(
        archetype_key="devils_advocate",
        panelist_name="Santos",
        persona_framing="You are the panel's devil's advocate, known for singling out the strongest claim made so far and contesting it hardest — you attack the argument, never the candidate personally.",
    ),
}

PANEL_COMPOSITION: dict[str, list[str]] = {
    "thesis": [
        "methodology_expert",
        "literature_theory_specialist",
        "problem_objectives_reviewer",
        "results_conclusions_reviewer",
        "statistical_analysis_reviewer",
        "ethics_practicality_reviewer",
        "technical_implementation_reviewer",
    ],
    "capstone": [
        "technical_implementation_reviewer",
        "methodology_expert",
        "results_conclusions_reviewer",
        "industry_practice_reviewer",
        "problem_objectives_reviewer",
        "ethics_practicality_reviewer",
        "statistical_analysis_reviewer",
        "literature_theory_specialist",
    ],
    "other/oral_comps": [
        "literature_theory_specialist",
        "methodology_expert",
        "problem_objectives_reviewer",
        "statistical_analysis_reviewer",
        "ethics_practicality_reviewer",
    ],
    "other/scholarship_panel": [
        "problem_objectives_reviewer",
        "ethics_practicality_reviewer",
        "methodology_expert",
        "results_conclusions_reviewer",
        "literature_theory_specialist",
    ],
    "other/grant_defense": [
        "problem_objectives_reviewer",
        "ethics_practicality_reviewer",
        "methodology_expert",
        "industry_practice_reviewer",
        "literature_theory_specialist",
    ],
    "other/certification_interview": [
        "technical_implementation_reviewer",
        "industry_practice_reviewer",
        "methodology_expert",
        "ethics_practicality_reviewer",
    ],
}


def _composition_key(profile: DefenseProfile) -> str:
    if profile.defense_type == DefenseType.OTHER:
        assert profile.other_subtype is not None  # enforced by DefenseProfile's validator
        return f"other/{profile.other_subtype.value}"
    return profile.defense_type.value


def compose_panel(profile: DefenseProfile) -> list[str]:
    """Return the user's selected archetype keys, in PANEL_COMPOSITION's priority
    order for this defense type — not click order. Pure function, no LLM.
    Validation (subset check, max 3, no duplicates) already happened at the schema
    level (DefenseProfile.check_selected_archetypes); this function trusts it and
    does not re-check (v0.3e Decision 4)."""
    key = _composition_key(profile)
    roster = PANEL_COMPOSITION[key]
    return [a for a in roster if a in profile.selected_archetypes]


def compose_full_roster(profile: DefenseProfile) -> list[str]:
    """`compose_panel`'s domain roster with Devil's Advocate appended, always last
    (Decision 3: DA sits last in the round). Pure function, no LLM. This is the roster
    callers should use to assemble an actual session panel — `compose_panel` stays
    domain-only so its existing tests stay scoped to domain-selection semantics."""
    return compose_panel(profile) + [DEVILS_ADVOCATE_KEY]


def _render_archetype_roster(archetype_keys: list[str]) -> str:
    return "\n".join(
        f"- {key}: {ARCHETYPE_CONFIG[key]['archetype_title']} — {ARCHETYPE_CONFIG[key]['archetype_focus']}"
        for key in archetype_keys
    )


def apply_customizations(
    panelists: list[GeneratedPanelist], profile: DefenseProfile
) -> list[Panelist]:
    """Seat the roster (v0.3j Decision 2): pure Python, runs after persona generation
    (or fallback) returns. Name override where a non-blank `display_name` exists for the
    archetype; icon resolution (customization → archetype default) for every panelist
    unconditionally. Every downstream consumer reads from the returned `Panelist`
    objects, so the custom name propagates everywhere with no further changes."""
    by_key = {c.archetype_key: c for c in profile.panel_customizations}
    roster: list[Panelist] = []
    for panelist in panelists:
        customization = by_key.get(panelist.archetype_key)
        name = panelist.panelist_name
        icon = archetype_default_icon(panelist.archetype_key)
        if customization is not None:
            if customization.display_name is not None and customization.display_name.strip():
                name = customization.display_name.strip()
            if customization.icon is not None:
                icon = customization.icon
        roster.append(
            Panelist(
                archetype_key=panelist.archetype_key,
                panelist_name=name,
                persona_framing=panelist.persona_framing,
                icon=icon,
            )
        )
    return roster


def _validate_and_reorder(
    panelists: list[GeneratedPanelist], archetype_keys: list[str]
) -> list[GeneratedPanelist] | None:
    """Python-side roster validation (Decision 2) — never trust the model's own keys.

    Same keys, same count as `archetype_keys`, no unknowns, no duplicate surnames. An
    order mismatch alone is not a failure — reorder to roster order and pass through.
    Any other violation returns None, signalling the caller to retry/fall back.
    """
    if len(panelists) != len(archetype_keys):
        return None

    by_key: dict[str, GeneratedPanelist] = {}
    for panelist in panelists:
        if panelist.archetype_key not in archetype_keys or panelist.archetype_key in by_key:
            return None
        by_key[panelist.archetype_key] = panelist

    surnames = [p.panelist_name.strip().lower() for p in panelists]
    if len(set(surnames)) != len(surnames):
        return None

    return [by_key[key] for key in archetype_keys]


def generate_panel(
    profile: DefenseProfile, archetype_keys: list[str], provider: LLMProvider
) -> tuple[list[Panelist], bool]:
    """One structured-output call generating the whole panel's personas. Returns
    (panel, fallback_used). Retries once on any failure (provider error or Python-side
    roster validation failure), then falls back to FALLBACK_PANELISTS sliced to the
    requested roster — never fails the session on persona failure. Both exits pass
    through `apply_customizations` (v0.3j) — a user's custom name/icon survives even
    the fallback path."""
    other_subtype_line = f" / {profile.other_subtype.value}" if profile.other_subtype is not None else ""
    prompt = PERSONA_GENERATION_PROMPT.format(
        defense_type=profile.defense_type.value,
        other_subtype_line=other_subtype_line,
        domain=profile.domain,
        topic=profile.topic,
        archetype_roster=_render_archetype_roster(archetype_keys),
    )

    for attempt in (1, 2):
        try:
            generation = provider.generate_structured(prompt, PanelGeneration)
        except LLMProviderError as exc:
            logger.warning("Persona generation call failed on attempt %d: %s", attempt, exc)
            continue

        panel = _validate_and_reorder(generation.panelists, archetype_keys)
        if panel is not None:
            return apply_customizations(panel, profile), False
        logger.warning(
            "Persona generation returned a malformed roster on attempt %d: %r",
            attempt,
            generation.panelists,
        )

    logger.warning(
        "Persona generation failed after retry — falling back to the static panel for roster %s",
        archetype_keys,
    )
    fallback_panel = [FALLBACK_PANELISTS[key] for key in archetype_keys]
    return apply_customizations(fallback_panel, profile), True
