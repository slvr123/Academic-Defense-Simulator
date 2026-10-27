"""v2.0 M7 J2 tagging audit: per document, every chunk in or touching the literature
range, with its pages, whether it cites a study, and its tag; plus any tagged chunk
outside the range (there should be none). Page numbers and yes/no only, never text.

Usage (repo root, backend/.venv): python scripts/audit_attribution_tags.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.page_filter import cites_a_study  # noqa: E402
from backend.pdf_ingest import parse_pdf  # noqa: E402

DOCUMENTS = [
    ROOT / "sample3.pdf",
    ROOT / "Group2_Library Management System for DAZSMA Documentation (1).pdf",
    ROOT / "academic_defense_simulator" / "sample" / "sample-capstone-anicheck.pdf",
]


def audit(path: Path) -> None:
    filtered = parse_pdf(path)
    review = filtered.literature_review
    tags = Counter(a or "untagged" for a in filtered.attributions)
    print(f"== {path.name}: {len(filtered.chunks)} chunks; tags {dict(tags)}")
    if review is None:
        print("   no literature range (no heading naming the literature, or no end): nothing tagged")
    else:
        print(f"   literature range: page {review.start[0] + 1} line {review.start[1]} "
              f"to page {review.end[0] + 1} line {review.end[1]} (exclusive)")
    first_page = review.start[0] if review else None
    last_page = review.end[0] if review else None
    print("   chunk  pages      cites  tag")
    for index, (chunk, tag) in enumerate(zip(filtered.chunks, filtered.attributions)):
        touches = review is not None and chunk.page_end >= first_page and chunk.page_start <= last_page
        if touches or tag is not None:
            pages = f"{chunk.page_start + 1}-{chunk.page_end + 1}"
            print(f"   {index:5}  {pages:9}  {'yes' if cites_a_study(chunk.text) else 'no':5}  {tag or '-'}")


def main() -> None:
    for path in DOCUMENTS:
        audit(path)


if __name__ == "__main__":
    main()
