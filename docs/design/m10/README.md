# M10 design canvas — reading notes

These files are a copy of my "ADS M10 Polish" design canvas (2026-10-28). They are **layout references**, not code to ship. They won't render on their own: the canvas runtime (`support.js`) isn't included. Read the markup for structure, spacing and hierarchy.

Where these mockups and `docs/v2.0-m10-ux-polish-decisions.md` disagree, **the decisions doc wins**:

- **Colours:** use the `bench` token set from each file's `renderVals` (it matches M8 K6). Ignore `redpen` and `archive`, and the files' default of `redpen` (N1).
- **Avatars:** the mockups use initials as placeholders. Ship v1's avatar PNGs (N2).
- **Numbers:** all page counts, passage counts, page ranges and names are sample values. Show real data only (N6).
- **Preparing screen:** the per-panelist "Reading Chapter 3 · …" lines are mockup-only. Ship the role's "looks for" line instead (N6).
- **Start screen:** the preset list shown in the mockup is superseded by the N7 table. The Literature & Theory Specialist is not in any preset.
- **Fonts:** Hanken Grotesk, Schibsted Grotesk and JetBrains Mono belong to the parked schemes. Bench uses Newsreader, Instrument Sans and IBM Plex Mono only.

| File | Screen |
|---|---|
| `Start.dc.html` | Start: hero, no-PDF paths, form (N3, N5, N7) |
| `Preparing.dc.html` | "The panel is reading your paper" (N6) |
| `Main.dc.html` | Session, asking state: header and panel grid (N2) |
| `Reveal.dc.html` | Answer recorded, source card, PDF side panel (N4) |
| `Session-Bench.dc.html`, `Session-Archive.dc.html` | Wrappers that render `Main` in one scheme. Only Bench matters. |
