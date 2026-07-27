# Sample Document

`sample-capstone-anicheck.pdf` is a **synthetic capstone document** written for
this project. It is not a real student's work.

The institution (Bulwagan Institute of Technology), the authors, the barangay,
the dataset, and every reported figure are fictional. No real person,
institution, or study is depicted.

## Why synthetic

The app needs a demo document that anyone can try without supplying their own
research paper. A real third-party paper would require a redistribution
licence; a real student capstone would require consent beyond what was obtained
for this project's evaluation documents. Authoring one removes both problems
and allows the document to be shaped for the demo's purpose.

## Design intent

The document carries deliberate, realistic methodological weaknesses so that
each panelist archetype has genuine material to interrogate. A flawless
document would make the panel appear toothless. These weaknesses are
intentional and should not be "fixed."

## Provenance

- Authored: 2026-07-27
- Extent: 16 pages, 29,606 extracted characters, no embedded images
- SHA-256: `6d7ff649bc48a9f8cfd37d82533fee4ccaef1a086b143bc749f8bc78e7c9a761`
- Licence: original project content; ships under this repository's licence

## Derived artifacts

`chunks.json`, `embeddings.npy`, and `manifest.json` are generated from this PDF
by `scripts/build_sample_sidecar.py`. **Do not edit the PDF without regenerating
them** — the manifest carries the SHA-256 above and the staleness guard in
`academic_defense_simulator/sample_document.py` will reject a mismatch and fall
back to ingesting the PDF live.

Regenerate with:

```
python scripts/build_sample_sidecar.py
```

## Location

This directory is `academic_defense_simulator/sample/`, inside the package, not
at the repo root. It ships with the app: the sample loader resolves these files
package-relative, so they resolve identically on a deployed instance.

`example_session.json` also lives here — the recorded example session (v1.0.1
Decision 5), rendered read-only by the example stage. It is recorded against
this same document (Decision 1), and its `document_chunks` is redacted to an
empty list before commit, asserted by
`scripts/inventory_committed_document_text.py`.
