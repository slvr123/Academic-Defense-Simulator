"""Session persistence to local disk (v0.4b) — business logic only, no Streamlit
imports (standing import boundary). One JSON file per session under `sessions/`
(gitignored, repo root). This module has no opinion about `ADS_PERSISTENCE_ENABLED`
— the flag lives in `config.py`, and the Streamlit layer simply never calls into
this module when it's off, so no directory is created and no file is written or read.

Writes are atomic (Decision 3): serialize to `{session_id}.json.tmp`, then
`os.replace()` onto the real path — a crash mid-write can never leave a half-written
file where a good one used to be. Save failures are the caller's problem to catch
(this module raises `OSError` naturally on disk trouble); load failures never
propagate past `list_sessions` — a corrupt or version-mismatched file is skipped
with a reason, not a crash (Decision 3, Decision 7)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from academic_defense_simulator.models.session import CURRENT_SCHEMA_VERSION, PersistedSession, SessionStage

DEFAULT_SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


def _session_path(session_id: str, directory: Path) -> Path:
    return directory / f"{session_id}.json"


def save_session(persisted: PersistedSession, directory: Path = DEFAULT_SESSIONS_DIR) -> None:
    """Atomic temp-then-replace write. Raises `OSError` on disk trouble (unwritable
    directory, out of space, etc.) — the caller decides how to degrade (v0.4b
    Decision 3: save failure is non-fatal to the live session, but that's a
    Streamlit-layer concern, not this module's)."""
    directory.mkdir(parents=True, exist_ok=True)
    final_path = _session_path(persisted.session_id, directory)
    tmp_path = final_path.with_name(final_path.name + ".tmp")
    tmp_path.write_text(persisted.model_dump_json(), encoding="utf-8")
    os.replace(tmp_path, final_path)


def load_session(session_id: str, directory: Path = DEFAULT_SESSIONS_DIR) -> PersistedSession:
    """Direct load by id — used by the resume button, after the file has already
    passed `list_sessions`' validation once. Raises on a missing/corrupt file; there
    is no skip-with-reason path here because the resume list is the only place a
    corrupt file is ever discovered, and it's already filtered out by then."""
    path = _session_path(session_id, directory)
    return PersistedSession.model_validate_json(path.read_text(encoding="utf-8"))


def delete_session(session_id: str, directory: Path = DEFAULT_SESSIONS_DIR) -> None:
    _session_path(session_id, directory).unlink(missing_ok=True)


@dataclass(frozen=True)
class SessionSkip:
    """A file in the sessions directory that could not be loaded, and why. Surfaced
    to the caller as data, never raised (Decision 3: load failure is non-fatal)."""

    path: Path
    reason: str


def _classify_unreadable(raw: str, path: Path) -> SessionSkip:
    """Distinguishes a version mismatch (Decision 7 — expected, named plainly) from
    genuine structural corruption, for whichever file just failed
    `PersistedSession.model_validate_json`."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return SessionSkip(path=path, reason=f"Corrupt session file (invalid JSON): {exc}")

    version = data.get("schema_version") if isinstance(data, dict) else None
    if version != CURRENT_SCHEMA_VERSION:
        return SessionSkip(
            path=path,
            reason=f"Saved with an incompatible version (schema_version={version!r})",
        )
    return SessionSkip(path=path, reason="Corrupt or malformed session file")


def list_sessions(directory: Path = DEFAULT_SESSIONS_DIR) -> tuple[list[PersistedSession], list[SessionSkip]]:
    """In-progress sessions only (Decision 4's filter lives here, not the UI) plus
    every file that had to be skipped and why. An absent directory is simply zero
    sessions, not an error — this is what "flag off, nothing exists yet" looks like
    on a fresh clone, and also what a fresh clone with the flag on looks like before
    the first save ever fires."""
    if not directory.is_dir():
        return [], []

    sessions: list[PersistedSession] = []
    skips: list[SessionSkip] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            skips.append(SessionSkip(path=path, reason=f"Couldn't read file: {exc}"))
            continue

        try:
            persisted = PersistedSession.model_validate_json(raw)
        except ValidationError:
            skips.append(_classify_unreadable(raw, path))
            continue

        if persisted.stage == SessionStage.IN_PROGRESS:
            sessions.append(persisted)
    return sessions, skips
