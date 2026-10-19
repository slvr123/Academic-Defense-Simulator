"""Process memory readings for the Render free-tier measurement (from the hosting spike).

`high_water_mb()` is the number the go/no-go reads: on Linux it is the kernel's
VmHWM, the most resident memory this process has ever had, so it can't miss a
spike between samples. `PeakSampler` adds a per-job peak, sampled from RSS.
`snapshot()` is one RSS + VmHWM reading at an ingest phase boundary, and
`release_memory()` hands freed memory back to the OS between phases.
"""

from __future__ import annotations

import ctypes
import functools
import gc
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

_MB = 1024 * 1024


def rss_mb() -> float:
    return psutil.Process().memory_info().rss / _MB


def high_water_mb() -> float | None:
    if sys.platform.startswith("linux"):
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
    peak = getattr(psutil.Process().memory_info(), "peak_wset", None)  # Windows
    return peak / _MB if peak is not None else None


@dataclass(frozen=True)
class PhaseMemory:
    phase: str
    rss_mb: float
    hwm_mb: float | None


def snapshot(phase: str) -> PhaseMemory:
    hwm = high_water_mb()
    return PhaseMemory(phase=phase, rss_mb=round(rss_mb(), 1), hwm_mb=None if hwm is None else round(hwm, 1))


@functools.cache
def _libc_malloc_trim() -> Callable[[int], int] | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        return ctypes.CDLL("libc.so.6").malloc_trim  # glibc; absent on musl
    except (OSError, AttributeError):
        return None


def malloc_trim() -> bool:
    """Ask glibc to return free heap pages to the OS. False (and nothing done)
    where there is no glibc, e.g. on Windows and macOS."""
    trim = _libc_malloc_trim()
    if trim is None:
        return False
    trim(0)
    return True


def release_memory() -> tuple[float, float, bool]:
    """gc.collect(), then malloc_trim(0). Returns (rss before, rss after, trimmed).
    A full collection holds the GIL, about 60 ms in the production process, so
    the ingest job calls this twice per document, not per chunk."""
    before = rss_mb()
    gc.collect()
    trimmed = malloc_trim()
    return before, rss_mb(), trimmed


def process_started_at() -> float:
    """Epoch seconds. With the pid, tells a restarted process apart from the same one."""
    return psutil.Process().create_time()


def describe() -> str:
    """`rss=…MB hwm=…MB` for log lines."""
    hwm = high_water_mb()
    return f"rss={rss_mb():.1f}MB hwm={'n/a' if hwm is None else f'{hwm:.1f}MB'}"


class PeakSampler:
    """Polls RSS on a daemon thread between `start()` and `stop()`."""

    def __init__(self, interval_s: float = 0.02) -> None:
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rss-sampler", daemon=True)
        self.peak_mb = 0.0

    def start(self) -> PeakSampler:
        self.peak_mb = rss_mb()
        self._thread.start()
        return self

    def stop(self) -> float:
        self._stop.set()
        self._thread.join()
        self.peak_mb = max(self.peak_mb, rss_mb())
        return self.peak_mb

    def _run(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.peak_mb = max(self.peak_mb, rss_mb())
