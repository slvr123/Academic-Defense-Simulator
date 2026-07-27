"""Restart the Streamlit app cleanly (v1.0.1 Appendix C3).

Kills any running server by argv match, waits until the process is actually
gone, enables the local file watcher per C2, and starts a fresh instance.

Why this exists rather than a habit. Streamlit binds a port that another
process already holds without warning, and the original binder keeps serving —
so a successful-looking start is not evidence that the server you started is
the one answering. On 2026-07-27 a server that had outlived a rename served
stale modules for 31 hours and produced an `AttributeError` that survived every
obvious fix, because `fileWatcherType = "none"` means a running process never
picks up a source edit. Killing by process rather than trusting the port
removes the ambiguity.

Run: python scripts/restart_app.py [<extra streamlit args>]

Any argument this script does not recognise is forwarded to `streamlit run`,
so `python scripts/restart_app.py --server.port 8501` works as expected.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    # line_buffering matters here, not just encoding: this script blocks in
    # subprocess.run for the life of the server, and a block-buffered stdout
    # (which is what you get when the output is redirected to a file rather
    # than a terminal) holds the kill report until the server finally exits.
    # The whole point of C3 is seeing *which* process was killed before the new
    # one starts, so that output has to leave the buffer immediately.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = REPO_ROOT / "academic_defense_simulator" / "streamlit_app.py"

# v1.0.1 Appendix C2: config.toml keeps "none" as the deploy default (the v0.3
# hardening RSS saving is about the server, where memory is the binding
# constraint). Locally memory is not the constraint and the watcher is what
# makes edit-then-re-run work at all, so the launcher supplies it through the
# environment — Streamlit reads config from env vars, and config.toml itself
# cannot be made conditional.
LOCAL_WATCHER_TYPE = "auto"
WATCHER_ENV_VAR = "STREAMLIT_SERVER_FILE_WATCHER_TYPE"

_KILL_TIMEOUT_SECONDS = 10


def _is_app_server(cmdline: list[str]) -> bool:
    """Exact argv shape of a server launch: `python -m streamlit run <script>`.

    Deliberately a positional match rather than a substring search for
    "streamlit" anywhere in the command. A substring match also matches this
    script, the shell that launched it, and any editor or terminal whose title
    happens to carry the word — during development that mistake killed the
    caller's own shell mid-run.
    """
    return len(cmdline) >= 4 and cmdline[1] == "-m" and cmdline[2] == "streamlit" and cmdline[3] == "run"


def _own_process_tree() -> set[int]:
    """This process and its ancestors, so the sweep can never target itself."""
    import psutil

    pids = {os.getpid()}
    proc = psutil.Process(os.getpid())
    while proc.parent() is not None:
        proc = proc.parent()
        pids.add(proc.pid)
    return pids


def kill_running_servers() -> list[tuple[int, str]]:
    """Returns the (pid, started) pairs that were killed and confirmed gone."""
    import psutil

    protected = _own_process_tree()
    targets = []
    for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
        if proc.info["pid"] in protected:
            continue
        try:
            if _is_app_server(proc.info.get("cmdline") or []):
                started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(proc.info["create_time"]))
                targets.append((proc, proc.info["pid"], started))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not targets:
        print("No running Streamlit server found.")
        return []

    for proc, pid, started in targets:
        print(f"  killing pid {pid} (started {started})")
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass

    # Confirming the process is gone is the point of the script — a kill that
    # returned without the process actually exiting would leave the old server
    # holding the port and silently answering requests.
    gone, alive = psutil.wait_procs([t[0] for t in targets], timeout=_KILL_TIMEOUT_SECONDS)
    if alive:
        still = ", ".join(str(p.pid) for p in alive)
        raise SystemExit(
            f"Refusing to start: pid(s) {still} did not exit within "
            f"{_KILL_TIMEOUT_SECONDS}s. A surviving server would keep serving the port."
        )

    killed = [(pid, started) for _, pid, started in targets]
    print(f"  confirmed dead: {[pid for pid, _ in killed]}")
    return killed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restart the Streamlit app cleanly.")
    parser.add_argument(
        "--no-watcher",
        action="store_true",
        help=f'Leave {WATCHER_ENV_VAR} unset, so config.toml\'s "none" applies (deploy parity).',
    )
    parser.add_argument(
        "--kill-only", action="store_true", help="Kill any running server and exit without starting one."
    )
    return parser


def main() -> None:
    # parse_known_args, not parse_args: everything this script does not recognise
    # is passed straight through to `streamlit run`. Streamlit's own flags are
    # dotted (`--server.port 8501`), which argparse would otherwise reject as
    # unrecognised options rather than collect as positionals.
    args, passthrough = build_parser().parse_known_args()

    print("Stopping any running Streamlit server...")
    kill_running_servers()

    if args.kill_only:
        return

    env = dict(os.environ)
    if args.no_watcher:
        env.pop(WATCHER_ENV_VAR, None)
        print(f'\n{WATCHER_ENV_VAR} unset — config.toml applies ("none", deploy parity)')
    else:
        env[WATCHER_ENV_VAR] = LOCAL_WATCHER_TYPE
        print(f"\n{WATCHER_ENV_VAR}={LOCAL_WATCHER_TYPE} (local watcher, Appendix C2)")

    command = [sys.executable, "-m", "streamlit", "run", str(APP_PATH), *passthrough]
    print("Starting:", " ".join(command))
    subprocess.run(command, cwd=str(REPO_ROOT), env=env, check=False)


if __name__ == "__main__":
    main()
