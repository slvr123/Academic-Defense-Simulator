"""What ingest read and left out, for the preparing screen (v2.0 M10 N6).

Page ranges come from the M5/M6/M7 filters' own positions, computed once at ingest;
the passage count is the panel's, so it matches what the panel can actually cite under
M9's routing. Counts and page numbers only, never page text.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.page_filter import FilteredChunks
from backend.routing import LitReviewAccess, may_draw_literature
from backend.schemas import IngestSummary, PageRange
from backend.sessions import Session


@dataclass(frozen=True)
class SkippedPages:
    """1-based page ranges, as a PDF viewer numbers them."""

    navigation: list[PageRange]  # contents and list pages (M5)
    references: PageRange | None  # the reference list (M6 H1)
    literature_review: PageRange | None  # the section M7 J2 found, whether or not anything was tagged


def skipped_pages(filtered: FilteredChunks, page_count: int) -> SkippedPages:
    references = filtered.references
    review = filtered.literature_review
    return SkippedPages(
        navigation=_runs(filtered.removed_pages),
        references=None if references is None else _span(references.start, references.end, page_count),
        literature_review=None if review is None else _span(review.start, review.end, page_count),
    )


def _span(start: tuple[int, int], end: tuple[int, int] | None, page_count: int) -> PageRange:
    """The pages a filter range touches. Its 0-based (page, line) `end` heading is kept, so
    an end on a page's first line leaves that page out; no end runs to the last page."""
    if end is None:
        last_page = page_count - 1
    else:
        end_page, end_line = end
        last_page = end_page if end_line > 0 else end_page - 1
    return PageRange(start=start[0] + 1, end=max(start[0], last_page) + 1)


def _runs(pages: list[int]) -> list[PageRange]:
    """Ascending 0-based pages as 1-based runs of consecutive pages."""
    runs: list[PageRange] = []
    for page in pages:
        if runs and runs[-1].end == page:  # the previous run ends on page - 1, 1-based
            runs[-1] = PageRange(start=runs[-1].start, end=page + 1)
        else:
            runs.append(PageRange(start=page + 1, end=page + 1))
    return runs


def ingest_summary(session: Session, access: LitReviewAccess) -> IngestSummary | None:
    """None until the session is ready. Literature-review passages count as citable only
    if a panelist on this panel may draw them (M9 L2); otherwise they're held back."""
    if session.chunk_count is None or session.skipped is None or session.page_count is None:
        return None
    tagged = sum(c.attribution is not None for c in session.chunks)
    anyone_reads_review = any(may_draw_literature(k, access) for k in session.panel.domain_archetypes())
    held_back = 0 if anyone_reads_review else tagged
    return IngestSummary(
        page_count=session.page_count,
        citable_passages=session.chunk_count - held_back,
        navigation_pages=session.skipped.navigation,
        reference_list=session.skipped.references,
        literature_review=session.skipped.literature_review if held_back else None,
        held_back_passages=held_back,
    )
