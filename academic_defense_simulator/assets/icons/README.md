# Panelist icon assets

Drop curated icon images here (`.png`, `.jpg`, `.jpeg`, `.webp`).
Every file in this directory becomes a choice in the intake icon picker, listed by its filename stem.

Naming convention:

- A file named exactly after an archetype key becomes that archetype's default icon:
  `methodology_expert.png`, `literature_theory_specialist.png`,
  `technical_implementation_reviewer.png`, `ethics_practicality_reviewer.png`,
  `devils_advocate.png`.
- Any other filename is an extra choice offered for every slot.

While this directory has no images, the built-in emoji set is used as the fallback, so the app works either way.
Square images look best - they are cropped to a 42px circle on the panelist cards.
