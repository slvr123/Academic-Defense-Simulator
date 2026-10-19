"""Run the M1 Render memory measurement end to end and write the results doc.

Usage (from the repo root, with backend/requirements-dev.txt installed):

    python scripts/measure_ingest.py path/to/thesis.pdf --base-url https://<svc>.onrender.com

The service must run with ADS_DEBUG_MEMORY=1 (render.yaml sets it on the M1 branch).
Each run waits `--gap-minutes` first (not before run 1) so the free instance spins
down, times the first /health as the cold start, uploads the PDF, polls until ready
or failed, and reads /debug/memory before and after. A changed pid or process start
time, or the session going missing, means the process restarted mid-run: on Render
free that is almost always the out-of-memory kill.

At the end it prints a summary table, judges the gates, and rewrites the measured
section of docs/v2.0-m1-memory-results.md (between the `measured:` markers).

`--concurrent` measures the case M1 left open: an upload arriving while another
document is embedding. Each run uploads the PDF, waits until it is embedding,
uploads `--second-pdf` (default: the same PDF), and waits for both. Its results go
to docs/v2.0-m2-concurrency-results.md (between the `concurrent:` markers).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DOC = REPO_ROOT / "docs" / "v2.0-m1-memory-results.md"
DOC_START = "<!-- measured:start -->"
DOC_END = "<!-- measured:end -->"
CONCURRENT_DOC = REPO_ROOT / "docs" / "v2.0-m2-concurrency-results.md"
CONCURRENT_START = "<!-- concurrent:start -->"
CONCURRENT_END = "<!-- concurrent:end -->"

# Gates I set for M1 on 2026-10-19.
MAX_HWM_MB = 400.0
MAX_COLD_START_S = 90.0
MAX_UPLOAD_TO_READY_S = 120.0

Sleep = Callable[[float], None]
Clock = Callable[[], float]


@dataclass(frozen=True)
class Identity:
    """Who answered /debug/memory: the same pid and start time means the same process."""

    pid: int
    started_at: float

    @classmethod
    def of(cls, debug: dict) -> Identity:
        return cls(pid=debug["pid"], started_at=debug["process_started_at"])


@dataclass
class RunResult:
    run: int
    waited_min: float
    cold_start_s: float | None = None
    fresh_process: bool | None = None  # started after the previous run ended (None for run 1)
    status: str = "not started"
    upload_to_ready_s: float | None = None
    chunks: int | None = None
    hwm_before_mb: float | None = None
    hwm_after_mb: float | None = None
    job: dict | None = None
    restarted: bool = False
    note: str = ""
    before: Identity | None = field(default=None, repr=False)
    after: Identity | None = field(default=None, repr=False)


def restart_detected(before: Identity, after: Identity | None, session_lost: bool) -> bool:
    """True if the process that took the upload isn't the one that answered afterwards.
    `after` is None when /debug/memory never answered again."""
    return session_lost or after is None or after != before


def wait_for_health(
    client: httpx.Client, timeout_s: float, sleep: Sleep = time.sleep, clock: Clock = time.perf_counter
) -> float:
    """Seconds until /health answers 200 with the model loaded. Render's proxy may
    answer 502/503 or drop the connection while the instance boots; those retry."""
    started = clock()
    while True:
        try:
            response = client.get("/health")
            if response.status_code == 200 and response.json().get("model_loaded"):
                return clock() - started
        except (httpx.TransportError, ValueError):
            pass
        if clock() - started > timeout_s:
            raise TimeoutError(f"/health not ready after {timeout_s:.0f}s")
        sleep(1.0)


def read_debug(client: httpx.Client, attempts: int = 10, sleep: Sleep = time.sleep) -> dict | None:
    for attempt in range(attempts):
        try:
            response = client.get("/debug/memory")
            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                raise SystemExit("/debug/memory is 404: the service needs ADS_DEBUG_MEMORY=1.")
        except httpx.TransportError:
            pass
        if attempt < attempts - 1:
            sleep(2.0)
    return None


def post_upload(
    client: httpx.Client, pdf: Path, panel: str | None, log: Callable[[str], None] = print
) -> tuple[str | None, dict]:
    """POST /sessions. Returns (session_id, {}) on 202, else (None, what came back)."""
    data = {"panel": panel} if panel is not None else {}
    try:
        with pdf.open("rb") as handle:
            response = client.post("/sessions", files={"file": (pdf.name, handle, "application/pdf")}, data=data)
    except httpx.TransportError as exc:
        log(f"  upload failed: {exc!r}")
        return None, {"error": repr(exc)}
    if response.status_code != 202:
        log(f"  upload: {response.status_code} {response.text}")
        return None, {"http": response.status_code, "body": response.text}
    return response.json()["session_id"], {}


def poll_session(client: httpx.Client, session_id: str) -> tuple[str, dict]:
    """One GET /sessions/{id}. Returns (status, state): the session's own status,
    "lost" on 404, or "unreachable" on a transport error or another non-200
    (instance restarting, or a proxy hiccup), which callers keep polling through."""
    try:
        response = client.get(f"/sessions/{session_id}")
    except httpx.TransportError:
        return "unreachable", {}
    if response.status_code == 404:
        return "lost", {}
    if response.status_code != 200:
        return "unreachable", {}
    state = response.json()
    return state["status"], state


DONE = ("ready", "failed", "lost")


def upload_and_wait(
    client: httpx.Client,
    pdf: Path,
    panel: str | None,
    poll_s: float,
    timeout_s: float,
    sleep: Sleep = time.sleep,
    clock: Clock = time.perf_counter,
    log: Callable[[str], None] = print,
) -> tuple[str, float | None, dict, str | None]:
    """Returns (status, upload_to_done_s, last session state, session_id).
    status is ready, failed, lost (the session 404'd), rejected or timeout."""
    started = clock()
    session_id, rejection = post_upload(client, pdf, panel, log)
    if session_id is None:
        return "rejected", None, rejection, None

    last_line = ""
    state: dict = {}
    while clock() - started < timeout_s:
        status, polled = poll_session(client, session_id)
        if status == "lost":
            log(f"  {clock() - started:8.2f}s  session gone (404)")
            return "lost", None, state, session_id
        if polled:
            state = polled
            progress = state.get("progress")
            line = status + (f" progress={progress:.2f}" if progress is not None else "")
            if line != last_line:
                log(f"  {clock() - started:8.2f}s  {line}")
                last_line = line
            if status in ("ready", "failed"):
                return status, clock() - started, state, session_id
        sleep(poll_s)
    return "timeout", None, state, session_id


def measure_run(
    client: httpx.Client,
    run: int,
    pdf: Path,
    *,
    gap_minutes: float,
    previous: RunResult | None,
    panel: str | None = None,
    poll_s: float = 0.5,
    timeout_s: float = 1800,
    health_timeout_s: float = 600,
    sleep: Sleep = time.sleep,
    clock: Clock = time.perf_counter,
    log: Callable[[str], None] = print,
) -> RunResult:
    waited = gap_minutes if previous is not None else 0.0
    result = RunResult(run=run, waited_min=waited)
    if waited:
        log(f"run {run}: waiting {waited:g} min for the instance to spin down")
        sleep(waited * 60)
    log(f"run {run}: GET /health")
    result.cold_start_s = wait_for_health(client, health_timeout_s, sleep=sleep, clock=clock)
    log(f"  /health 200 after {result.cold_start_s:.2f}s")

    before = read_debug(client, sleep=sleep)
    if before is None:
        result.status, result.note = "no debug", "/debug/memory never answered before the upload"
        return result
    result.before = Identity.of(before)
    result.hwm_before_mb = before["hwm_mb"]
    if previous is not None and previous.after is not None:
        result.fresh_process = result.before != previous.after

    status, elapsed, state, session_id = upload_and_wait(
        client, pdf, panel, poll_s, timeout_s, sleep=sleep, clock=clock, log=log
    )
    result.status = status
    result.upload_to_ready_s = elapsed if status == "ready" else None
    result.chunks = state.get("chunk_count")

    after = read_debug(client, sleep=sleep)
    result.after = Identity.of(after) if after else None
    result.restarted = status != "rejected" and restart_detected(result.before, result.after, status == "lost")
    if result.restarted:
        result.note = "process restarted, likely OOM"
    elif after is not None:
        result.hwm_after_mb = after["hwm_mb"]
        job = after.get("last_job")
        result.job = job if job and job["session_id"] == session_id else None
    if result.restarted:
        pass  # the restart is the finding; any session state after it is the new process's
    elif status == "failed":
        result.note = state.get("error") or "failed"
    elif status in ("rejected", "timeout"):
        result.note = result.note or f"upload {status}: {json.dumps(state)[:200]}"

    if session_id is not None and status != "lost":
        try:
            client.delete(f"/sessions/{session_id}")
        except httpx.TransportError:
            pass
    return result


@dataclass
class ConcurrentRunResult:
    """One --concurrent run: the first upload is embedding when the second arrives."""

    run: int
    waited_min: float
    cold_start_s: float | None = None
    fresh_process: bool | None = None
    first_status: str = "not started"
    second_status: str = "not started"
    overlapped: bool = False  # the first job was still embedding after the second upload was accepted
    first_upload_to_ready_s: float | None = None
    second_upload_s: float | None = None  # the second POST /sessions, received while the first embeds
    second_upload_to_ready_s: float | None = None
    first_chunks: int | None = None
    second_chunks: int | None = None
    hwm_before_mb: float | None = None
    hwm_after_second_upload_mb: float | None = None
    hwm_after_mb: float | None = None
    first_job: dict | None = None
    second_job: dict | None = None
    restarted: bool = False
    note: str = ""
    before: Identity | None = field(default=None, repr=False)
    after: Identity | None = field(default=None, repr=False)


def _matching_job(debug: dict | None, session_id: str | None) -> dict | None:
    job = (debug or {}).get("last_job")
    return job if job and session_id is not None and job["session_id"] == session_id else None


def measure_concurrent_run(
    client: httpx.Client,
    run: int,
    first_pdf: Path,
    second_pdf: Path,
    *,
    gap_minutes: float,
    previous: ConcurrentRunResult | None,
    panel: str | None = None,
    poll_s: float = 0.5,
    timeout_s: float = 1800,
    health_timeout_s: float = 600,
    sleep: Sleep = time.sleep,
    clock: Clock = time.perf_counter,
    log: Callable[[str], None] = print,
) -> ConcurrentRunResult:
    waited = gap_minutes if previous is not None else 0.0
    result = ConcurrentRunResult(run=run, waited_min=waited)
    if waited:
        log(f"run {run}: waiting {waited:g} min for the instance to spin down")
        sleep(waited * 60)
    log(f"run {run}: GET /health")
    result.cold_start_s = wait_for_health(client, health_timeout_s, sleep=sleep, clock=clock)
    log(f"  /health 200 after {result.cold_start_s:.2f}s")

    before = read_debug(client, sleep=sleep)
    if before is None:
        result.first_status, result.note = "no debug", "/debug/memory never answered before the upload"
        return result
    result.before = Identity.of(before)
    result.hwm_before_mb = before["hwm_mb"]
    if previous is not None and previous.after is not None:
        result.fresh_process = result.before != previous.after

    started = clock()
    first_id, rejection = post_upload(client, first_pdf, panel, log)
    if first_id is None:
        result.first_status, result.note = "rejected", f"first upload rejected: {json.dumps(rejection)[:200]}"
        return result
    log(f"  {clock() - started:8.2f}s  first upload accepted; waiting for it to start embedding")
    status, first_state = "queued", {}
    while status not in ("embedding", *DONE) and clock() - started < timeout_s:
        status, polled = poll_session(client, first_id)
        first_state = polled or first_state
        if status not in ("embedding", *DONE):
            sleep(poll_s)

    ids = {"first": first_id}
    states: dict[str, dict] = {"first": first_state, "second": {}}
    statuses = {"first": status, "second": "not started"}
    done_at: dict[str, float] = {"first": clock()} if status in DONE else {}
    second_started: float | None = None
    if status == "embedding":
        second_started = clock()
        second_id, rejection = post_upload(client, second_pdf, panel, log)
        result.second_upload_s = clock() - second_started
        if second_id is None:
            statuses["second"] = "rejected"
            result.note = f"second upload rejected: {json.dumps(rejection)[:200]}"
        else:
            ids["second"] = second_id
            statuses["second"] = "queued"
            during = read_debug(client, sleep=sleep)
            result.hwm_after_second_upload_mb = during["hwm_mb"] if during else None
            first_now, _ = poll_session(client, first_id)
            result.overlapped = first_now == "embedding"
            log(
                f"  {clock() - started:8.2f}s  second upload accepted in {result.second_upload_s:.2f}s; "
                f"first is {first_now}"
            )
    else:
        result.note = f"first upload was {status} before the second could be sent"

    while any(statuses[k] not in DONE for k in ids) and clock() - started < timeout_s:
        for key, session_id in ids.items():
            if statuses[key] in DONE:
                continue
            status, state = poll_session(client, session_id)
            if status == "unreachable":
                continue
            states[key] = state or states[key]
            if status != statuses[key]:
                log(f"  {clock() - started:8.2f}s  {key}: {status}")
            statuses[key] = status
            if status in DONE:
                done_at[key] = clock()
                if key == "first" and status == "ready":
                    # The second job only starts now, so last_job is still the first one's.
                    result.first_job = _matching_job(read_debug(client, sleep=sleep), first_id)
        if any(statuses[k] not in DONE for k in ids):
            sleep(poll_s)
    for key in ids:
        if statuses[key] not in DONE:
            statuses[key] = "timeout"

    after = read_debug(client, sleep=sleep)
    result.after = Identity.of(after) if after else None
    lost = any(statuses[k] == "lost" for k in ids)
    result.restarted = restart_detected(result.before, result.after, lost)
    result.first_status, result.second_status = statuses["first"], statuses["second"]
    result.first_chunks = states["first"].get("chunk_count")
    result.second_chunks = states["second"].get("chunk_count")
    if statuses["first"] == "ready":
        result.first_upload_to_ready_s = done_at["first"] - started
    if statuses["second"] == "ready" and second_started is not None:
        result.second_upload_to_ready_s = done_at["second"] - second_started
    if result.restarted:
        result.note = "process restarted, likely OOM"
    elif after is not None:
        result.hwm_after_mb = after["hwm_mb"]
        result.second_job = _matching_job(after, ids.get("second"))
        failed = [f"{k}: {states[k].get('error') or statuses[k]}" for k in ids if statuses[k] != "ready"]
        result.note = "; ".join(filter(None, [result.note, *failed]))

    for key, session_id in ids.items():
        if statuses[key] != "lost":
            try:
                client.delete(f"/sessions/{session_id}")
            except httpx.TransportError:
                pass
    return result


def _cold_start_findings(runs: list) -> tuple[list[str], list[str], list]:
    """(failures, gaps, runs that followed a gap), shared by both modes' gates."""
    failures: list[str] = []
    gaps: list[str] = []
    cold = [r for r in runs if r.waited_min > 0]
    for r in cold:
        if r.fresh_process is False:
            gaps.append(f"run {r.run}: same process as run {r.run - 1}, so the instance never spun down")
        elif r.cold_start_s is not None and r.cold_start_s > MAX_COLD_START_S:
            failures.append(f"run {r.run}: cold start {r.cold_start_s:.1f} s > {MAX_COLD_START_S:g} s")
    if not cold:
        gaps.append("no run followed a spin-down gap, so cold start wasn't measured")
    return failures, gaps, cold


def evaluate_concurrent(runs: list[ConcurrentRunResult]) -> tuple[str, list[str]]:
    """M1's 400 MB VmHWM gate, on a process that received the second upload while
    embedding the first. INCONCLUSIVE when a run didn't actually overlap the two."""
    failures, gaps, _ = _cold_start_findings(runs)
    for r in runs:
        if r.restarted:
            failures.append(f"run {r.run}: process restarted, likely OOM")
        elif r.first_status != "ready" or r.second_status not in ("ready", "not started"):
            failures.append(f"run {r.run}: first {r.first_status}, second {r.second_status} ({r.note})")
        elif not r.overlapped:  # includes a second upload that was never sent
            gaps.append(f"run {r.run}: the first job finished before the second upload landed, so nothing overlapped")
        elif r.hwm_after_mb is None:
            gaps.append(f"run {r.run}: no VmHWM reading")
        elif r.hwm_after_mb > MAX_HWM_MB:
            failures.append(f"run {r.run}: VmHWM {r.hwm_after_mb:.1f} MB > {MAX_HWM_MB:g} MB")
    if failures:
        return "NO-GO", failures + gaps
    if gaps:
        return "INCONCLUSIVE", gaps
    return "GO", [
        f"all {len(runs)} runs overlapped, both uploads ready, no restart; highest VmHWM "
        f"{max(r.hwm_after_mb for r in runs):.1f} MB (<= {MAX_HWM_MB:g})"
    ]


def concurrent_table(runs: list[ConcurrentRunResult]) -> str:
    header = (
        "| Run | waited (min) | cold start (s) | fresh process | first / second | overlapped "
        "| first upload-to-ready (s) | second upload (s) | second upload-to-ready (s) | chunks "
        "| VmHWM before (MB) | VmHWM after second upload (MB) | VmHWM after both (MB) | restarted |"
    )
    lines = [header, "|" + "---|" * (header.count("|") - 1)]
    for r in runs:
        fresh = "-" if r.fresh_process is None else ("yes" if r.fresh_process else "NO")
        chunks = " / ".join("-" if c is None else str(c) for c in (r.first_chunks, r.second_chunks))
        lines.append(
            f"| {r.run} | {r.waited_min:g} | {_fmt(r.cold_start_s, 2)} | {fresh} "
            f"| {r.first_status} / {r.second_status} | {'yes' if r.overlapped else 'NO'} "
            f"| {_fmt(r.first_upload_to_ready_s, 2)} | {_fmt(r.second_upload_s, 2)} "
            f"| {_fmt(r.second_upload_to_ready_s, 2)} | {chunks} | {_fmt(r.hwm_before_mb)} "
            f"| {_fmt(r.hwm_after_second_upload_mb)} | {_fmt(r.hwm_after_mb)} "
            f"| {'YES, likely OOM' if r.restarted else 'no'} |"
        )
    return "\n".join(lines)


def concurrent_markdown(
    runs: list[ConcurrentRunResult], verdict: str, reasons: list[str], *, first_pdf: Path, second_pdf: Path,
    base_url: str, debug: dict | None, finished: datetime,
) -> str:
    commit = (debug or {}).get("commit") or "unknown"
    first_jobs = [RunResult(run=r.run, waited_min=r.waited_min, job=r.first_job) for r in runs]
    second_jobs = [RunResult(run=r.run, waited_min=r.waited_min, job=r.second_job) for r in runs]
    lines = [
        CONCURRENT_START,
        "<!-- Written by scripts/measure_ingest.py --concurrent; rerunning it replaces this section. -->",
        "",
        f"Measured {finished:%Y-%m-%d %H:%M} UTC against `{base_url}`, deployed commit `{commit}`.",
        f"First PDF: `{first_pdf.name}`; second PDF: `{second_pdf.name}`, uploaded once the first was embedding.",
        "",
        concurrent_table(runs),
        "",
        "Memory at each phase boundary of the first job (MB):",
        "",
        phase_table(first_jobs),
        "",
        "Memory at each phase boundary of the second job (MB):",
        "",
        phase_table(second_jobs),
        "",
        "\"VmHWM after second upload\" is read right after the second upload is accepted, while the first is still embedding.",
        "\"Overlapped\" means the first job was still embedding at that moment.",
        "",
        f"Verdict: **{verdict}**",
        "",
        *[f"- {reason}" for reason in reasons],
        CONCURRENT_END,
    ]
    return "\n".join(lines)


def evaluate(runs: list[RunResult]) -> tuple[str, list[str]]:
    """GO only if every run passes every gate. INCONCLUSIVE if the runs couldn't
    measure what the gates ask (no cold start, or the instance never spun down)."""
    failures, gaps, cold = _cold_start_findings(runs)
    for r in runs:
        if r.restarted:
            failures.append(f"run {r.run}: process restarted, likely OOM")
        elif r.status != "ready":
            failures.append(f"run {r.run}: ingest {r.status} ({r.note})")
        else:
            if r.hwm_after_mb is None:
                gaps.append(f"run {r.run}: no VmHWM reading")
            elif r.hwm_after_mb > MAX_HWM_MB:
                failures.append(f"run {r.run}: VmHWM {r.hwm_after_mb:.1f} MB > {MAX_HWM_MB:g} MB")
            if r.upload_to_ready_s is not None and r.upload_to_ready_s > MAX_UPLOAD_TO_READY_S:
                failures.append(
                    f"run {r.run}: upload-to-ready {r.upload_to_ready_s:.1f} s > {MAX_UPLOAD_TO_READY_S:g} s"
                )

    if failures:
        return "NO-GO", failures + gaps
    if gaps:
        return "INCONCLUSIVE", gaps
    return "GO", [
        f"all {len(runs)} runs ready with no restart; highest VmHWM "
        f"{max(r.hwm_after_mb for r in runs):.1f} MB (<= {MAX_HWM_MB:g}), slowest upload-to-ready "
        f"{max(r.upload_to_ready_s for r in runs):.1f} s (<= {MAX_UPLOAD_TO_READY_S:g}), slowest cold start "
        f"{max(r.cold_start_s for r in cold):.1f} s (<= {MAX_COLD_START_S:g})"
    ]


def _fmt(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def summary_table(runs: list[RunResult]) -> str:
    header = (
        "| Run | waited (min) | cold start (s) | fresh process | status | upload-to-ready (s) | chunks "
        "| parse_s | embed_s | job peak RSS (MB) | VmHWM before upload (MB) | VmHWM after (MB) | restarted |"
    )
    lines = [header, "|" + "---|" * (header.count("|") - 1)]
    for r in runs:
        job = r.job or {}
        fresh = "-" if r.fresh_process is None else ("yes" if r.fresh_process else "NO")
        lines.append(
            f"| {r.run} | {r.waited_min:g} | {_fmt(r.cold_start_s, 2)} | {fresh} | {r.status} "
            f"| {_fmt(r.upload_to_ready_s, 2)} | {r.chunks if r.chunks is not None else '-'} "
            f"| {_fmt(job.get('parse_s'), 2)} | {_fmt(job.get('embed_s'), 2)} | {_fmt(job.get('job_peak_rss_mb'))} "
            f"| {_fmt(r.hwm_before_mb)} | {_fmt(r.hwm_after_mb)} "
            f"| {'YES, likely OOM' if r.restarted else 'no'} |"
        )
    return "\n".join(lines)


PHASES = ("spooled", "parsed", "released", "embedded", "job_end")


def phase_table(runs: list[RunResult]) -> str:
    """RSS / VmHWM (MB) at each ingest phase boundary. VmHWM only rises, so the
    first phase where it reaches its final value is the phase that set the peak."""
    header = "| Run | " + " | ".join(f"{p} rss / hwm" for p in PHASES) + " | peak set in |"
    lines = [header, "|" + "---|" * (len(PHASES) + 2)]
    for r in runs:
        by_phase = {p["phase"]: p for p in (r.job or {}).get("phases", [])}
        cells = [
            f"{_fmt(by_phase[p]['rss_mb'])} / {_fmt(by_phase[p]['hwm_mb'])}" if p in by_phase else "-"
            for p in PHASES
        ]
        lines.append(f"| {r.run} | " + " | ".join(cells) + f" | {peak_phase(by_phase)} |")
    return "\n".join(lines)


def peak_phase(by_phase: dict[str, dict]) -> str:
    """The phase whose boundary first shows the job's final VmHWM, i.e. the phase
    that ended at the peak. "spooled" covers startup, model load and the upload."""
    readings = [(p, by_phase[p]["hwm_mb"]) for p in PHASES if p in by_phase and by_phase[p]["hwm_mb"] is not None]
    if not readings:
        return "-"
    final = readings[-1][1]
    return next(phase for phase, hwm in readings if hwm >= final)


def results_markdown(
    runs: list[RunResult], verdict: str, reasons: list[str], *, pdf: Path, size_mb: float, pages: int,
    base_url: str, debug: dict | None, finished: datetime,
) -> str:
    commit = (debug or {}).get("commit") or "unknown"
    limits = (
        f"`ADS_MAX_UPLOAD_MB` = {debug['max_upload_mb']:g}, `ADS_MAX_PAGES` = {debug['max_pages']}"
        if debug else "unknown"
    )
    lines = [
        DOC_START,
        "<!-- Written by scripts/measure_ingest.py; rerunning it replaces this section. -->",
        "",
        f"Measured {finished:%Y-%m-%d %H:%M} UTC against `{base_url}`, deployed commit `{commit}`.",
        f"PDF: `{pdf.name}` ({size_mb:.2f} MB, {pages} pages).",
        f"Limits on the service: {limits}.",
        "",
        summary_table(runs),
        "",
        "Memory at each ingest phase boundary (MB):",
        "",
        phase_table(runs),
        "",
        "Phases: `spooled` = upload on disk and pages counted (includes startup and model load); "
        "`parsed` = PDF parsed and chunked; `released` = after gc.collect() and malloc_trim(0); "
        "`embedded` = all chunks embedded; `job_end` = after the final gc.collect() and malloc_trim(0).",
        "",
        "Cold start is the first `/health` 200 after the gap; run 1 had no gap, so its time isn't a cold start.",
        "\"Fresh process\" means the pid or start time differs from the previous run's, so the instance did spin down.",
        "",
        f"Verdict: **{verdict}**",
        "",
        *[f"- {reason}" for reason in reasons],
        DOC_END,
    ]
    return "\n".join(lines)


def write_results(doc: Path, block: str, start_marker: str = DOC_START, end_marker: str = DOC_END) -> None:
    text = doc.read_text(encoding="utf-8")
    start, end = text.index(start_marker), text.index(end_marker) + len(end_marker)
    doc.write_text(text[:start] + block + text[end:], encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--gap-minutes", type=float, default=16, help="idle wait before runs 2..N")
    parser.add_argument("--concurrent", action="store_true", help="upload a second PDF while the first embeds")
    parser.add_argument("--second-pdf", type=Path, default=None, help="--concurrent only; default: the same PDF")
    parser.add_argument("--panel", default=None, help="panel JSON; omitted = defaults")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--timeout-seconds", type=float, default=1800, help="per upload")
    parser.add_argument("--results-doc", type=Path, default=None, help="default depends on the mode")
    parser.add_argument("--no-doc", action="store_true", help="print only; don't touch the results doc")
    args = parser.parse_args(argv)
    if args.second_pdf is not None and not args.concurrent:
        parser.error("--second-pdf needs --concurrent")
    second_pdf = args.second_pdf or args.pdf

    for pdf in dict.fromkeys([args.pdf, second_pdf] if args.concurrent else [args.pdf]):
        with fitz.open(pdf) as document:
            print(f"file: {pdf.name}  {pdf.stat().st_size / (1024 * 1024):.2f} MB  {document.page_count} pages")
    with fitz.open(args.pdf) as document:
        pages = document.page_count
    size_mb = args.pdf.stat().st_size / (1024 * 1024)
    total_wait = args.gap_minutes * (args.runs - 1)
    mode = "concurrent " if args.concurrent else ""
    print(f"{args.runs} {mode}run(s), {args.gap_minutes:g} min apart: about {total_wait:g} min of waiting")

    runs: list = []
    last_debug: dict | None = None
    # Generous per-request timeout: a cold Render instance can hold the first request a while.
    with httpx.Client(base_url=args.base_url, timeout=httpx.Timeout(300)) as client:
        for run in range(1, args.runs + 1):
            common = dict(
                gap_minutes=args.gap_minutes, previous=runs[-1] if runs else None,
                panel=args.panel, poll_s=args.poll_seconds, timeout_s=args.timeout_seconds,
            )
            if args.concurrent:
                result = measure_concurrent_run(client, run, args.pdf, second_pdf, **common)
            else:
                result = measure_run(client, run, args.pdf, **common)
            runs.append(result)
            print(json.dumps({k: v for k, v in asdict(result).items() if k not in ("before", "after")}))
            last_debug = read_debug(client) or last_debug

    finished = datetime.now(timezone.utc)
    print()
    if args.concurrent:
        verdict, reasons = evaluate_concurrent(runs)
        print(concurrent_table(runs))
        block = concurrent_markdown(
            runs, verdict, reasons, first_pdf=args.pdf, second_pdf=second_pdf, base_url=args.base_url,
            debug=last_debug, finished=finished,
        )
        doc, markers = args.results_doc or CONCURRENT_DOC, (CONCURRENT_START, CONCURRENT_END)
    else:
        verdict, reasons = evaluate(runs)
        print(summary_table(runs))
        print()
        print(phase_table(runs))
        block = results_markdown(
            runs, verdict, reasons, pdf=args.pdf, size_mb=size_mb, pages=pages, base_url=args.base_url,
            debug=last_debug, finished=finished,
        )
        doc, markers = args.results_doc or RESULTS_DOC, (DOC_START, DOC_END)
    print(f"\nVerdict: {verdict}")
    for reason in reasons:
        print(f"- {reason}")
    if not args.no_doc:
        write_results(doc, block, *markers)
        print(f"\nwrote the results into {doc}")
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
