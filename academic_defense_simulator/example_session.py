"""The recorded example session behind the read-only example stage (v1.0.1
Decisions 5 and 6).

Business logic only — no `streamlit` import (standing import boundary).

The fixture is a `PersistedSession` exactly as `persistence.save_session`
writes one, recorded from a genuine local session against the sample document
and then redacted before commit. It deliberately does *not* go through
`persistence.load_session`: that module reads the gitignored `sessions/`
directory, which does not exist on a deployed instance (v0.4b Decision 1). This
file is a committed repository artefact and resolves package-relative, the same
way the sample sidecar does, so it is present wherever the app is.

Why the redaction is asserted here rather than trusted (Decision 6): v1.0's
close-out found a 3,827-character verbatim document chunk inside a committed
session JSON. `document_chunks` is the field that leaked. Re-recording the
fixture is a routine operation, so the invariant is enforced at load time in
one testable place rather than left to whoever runs the recording next
remembering to redact.
"""

from __future__ import annotations

import logging
from pathlib import Path

from academic_defense_simulator.models.session import PersistedSession

logger = logging.getLogger(__name__)

EXAMPLE_SESSION_PATH = Path(__file__).parent / "sample" / "example_session.json"


class ExampleSessionUnavailable(RuntimeError):
    """The fixture is missing, unparseable, or fails the redaction invariant.

    Raised rather than returning `None` so a caller cannot render a
    half-populated example view by accident. The Streamlit layer catches this
    and degrades to a message plus the route back to intake — a broken fixture
    must never take down the intake screen, which is the only path a visitor
    has to a real session.
    """


def load_example_session(path: Path = EXAMPLE_SESSION_PATH) -> PersistedSession:
    """Load and validate the committed fixture.

    Raises `ExampleSessionUnavailable` on any failure, including a fixture that
    still carries document text in `document_chunks`.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExampleSessionUnavailable(f"could not read {path}: {exc}") from exc

    try:
        persisted = PersistedSession.model_validate_json(raw)
    except ValueError as exc:
        raise ExampleSessionUnavailable(f"could not parse {path}: {exc}") from exc

    if persisted.document_chunks:
        raise ExampleSessionUnavailable(
            f"{path.name} carries {len(persisted.document_chunks)} unredacted "
            "document chunk(s); the committed fixture must have document_chunks "
            "redacted to an empty list (v1.0.1 Decision 6)"
        )

    if persisted.session.report is None:
        raise ExampleSessionUnavailable(
            f"{path.name} has no report; the example stage renders a completed "
            "session, so a fixture without one is not usable (Decision 5)"
        )

    return persisted
