"""Upload checks and PDF parsing for the ingest pipeline.

Checks that run before a job is enqueued (size, PDF-ness, page count) raise
`UploadRejected` with the HTTP status the route returns. Parsing and chunking
reuse v1's `rag/chunking.py`, Miss 3 page-number stripping included.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from academic_defense_simulator.rag.chunking import (
    DocumentIngestionError,
    InsufficientTextError,
    PageChunk,
    chunk_pdf_with_pages,
)
from backend.schemas import IngestErrorCode

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
_COPY_BLOCK = 1 << 20


class UploadRejected(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def reset_upload_dir(upload_dir: Path) -> None:
    """Startup: spooled files from a previous process belong to sessions that no longer exist."""
    shutil.rmtree(upload_dir, ignore_errors=True)
    upload_dir.mkdir(parents=True, exist_ok=True)


def spool_upload(source: BinaryIO, upload_dir: Path, max_bytes: int) -> Path:
    """Copy the uploaded file to its own file in `upload_dir`, rejecting it with 413
    as soon as it passes `max_bytes` and with 422 if it doesn't start like a PDF."""
    target = upload_dir / f"{uuid.uuid4()}.pdf"
    written = 0
    try:
        with target.open("wb") as out:
            head = source.read(len(PDF_MAGIC))
            if head != PDF_MAGIC:
                raise UploadRejected(422, "That file isn't a PDF. Upload a .pdf document.")
            out.write(head)
            written = len(head)
            for block in iter(lambda: source.read(_COPY_BLOCK), b""):
                written += len(block)
                if written > max_bytes:
                    raise UploadRejected(413, _too_big_message(max_bytes))
                out.write(block)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def check_pages(path: Path, max_pages: int) -> int:
    """Open the spooled file and return its page count, rejecting unreadable,
    password-protected or over-long PDFs."""
    import fitz  # PyMuPDF

    # Rejected outside the except block, unchained: a live PyMuPDF traceback keeps
    # the half-opened file locked on Windows, so the caller couldn't delete it.
    document = None
    try:
        document = fitz.open(str(path))
    except Exception as exc:
        # str(), not exc: a log record holding the exception would keep it alive too.
        logger.info("upload rejected, PyMuPDF could not open it: %s", str(exc))
    if document is None:
        raise UploadRejected(422, "That file couldn't be opened as a PDF. It may be damaged.")
    try:
        if document.needs_pass:
            raise UploadRejected(422, "That PDF is password-protected. Upload an unlocked copy.")
        page_count = document.page_count
    finally:
        document.close()
    if page_count > max_pages:
        raise UploadRejected(
            413, f"That PDF has {page_count} pages; the limit is {max_pages}."
        )
    return page_count


class IngestFailure(Exception):
    """A job failed for a reason the user should see; `str()` is the readable error,
    `code` the machine-readable one where a client branches on it."""

    def __init__(self, message: str, code: IngestErrorCode | None = None) -> None:
        super().__init__(message)
        self.code = code


def parse_pdf(path: Path) -> list[PageChunk]:
    try:
        chunks = chunk_pdf_with_pages(path)
    except InsufficientTextError as exc:
        raise IngestFailure(_scanned_message(exc.total_chars), code="scanned_pdf") from exc
    except DocumentIngestionError as exc:
        raise IngestFailure(str(exc)) from exc
    if not chunks:
        raise IngestFailure(_scanned_message(0), code="scanned_pdf")
    return chunks


def _scanned_message(total_chars: int) -> str:
    return (
        f"This PDF looks scanned: it has almost no selectable text ({total_chars} characters). "
        "Scanned PDFs aren't supported yet. Upload a PDF whose text you can select and copy."
    )


def _too_big_message(max_bytes: int) -> str:
    return f"That PDF is larger than the {max_bytes / (1024 * 1024):g} MB limit."
