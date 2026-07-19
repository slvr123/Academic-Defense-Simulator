# Panelist icon assets

Drop curated icon images here (`.png`, `.jpg`, `.jpeg`, `.webp`).
Every file in this directory becomes a choice in the intake icon picker (a popover grid), labeled by its filename stem.

Default resolution per archetype, in priority order:

1. A file named exactly after an archetype key is that archetype's default:
   `methodology_expert.png`, `literature_theory_specialist.png`,
   `technical_implementation_reviewer.png`, `ethics_practicality_reviewer.png`,
   `devils_advocate.png`.
2. Otherwise the sorted files are spread across the archetypes deterministically, so each archetype gets a distinct default while enough images exist.
3. If this directory is empty, a built-in emoji set is the fallback - emoji no longer appear in the picker once images exist.

Square images look best - they are cropped to a 42px circle on the panelist cards.
