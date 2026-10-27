"""An HTTPException that also carries a machine-readable `code` in its body.

FastAPI's own handler writes only {"detail": ...}. This one adds "code", so a
client can tell two 429s apart without parsing the message (M3 decisions, D24),
and /tts's and /answers' coded failures (M3b E3, E13). A per-IP 429 also carries
its `limit` (M6 H6). Every other error keeps FastAPI's body unchanged.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from backend.schemas import ErrorBody, ErrorCode


class CodedHTTPException(HTTPException):
    def __init__(
        self, status_code: int, detail: str, code: ErrorCode, headers: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> None:
        super().__init__(status_code, detail, headers=headers)
        self.code: ErrorCode = code
        self.limit = limit


async def coded_http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, CodedHTTPException)
    body = ErrorBody(detail=exc.detail, code=exc.code, limit=exc.limit)
    # exclude_none: a body without a limit is exactly what it was before M6.
    return JSONResponse(body.model_dump(exclude_none=True), status_code=exc.status_code, headers=exc.headers)
