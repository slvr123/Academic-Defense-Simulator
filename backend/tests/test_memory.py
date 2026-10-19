"""memory.py's release helpers: safe no-ops where glibc's malloc_trim doesn't exist."""

from __future__ import annotations

import ctypes
import gc

import pytest

from backend import memory


@pytest.fixture(autouse=True)
def _fresh_libc_lookup():
    memory._libc_malloc_trim.cache_clear()
    yield
    memory._libc_malloc_trim.cache_clear()


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_malloc_trim_is_a_no_op_off_linux(monkeypatch, platform):
    loaded = []
    monkeypatch.setattr(memory.sys, "platform", platform)
    monkeypatch.setattr(memory.ctypes, "CDLL", lambda name: loaded.append(name))
    assert memory.malloc_trim() is False
    assert loaded == []  # never even tries to load a C library


def test_malloc_trim_is_a_no_op_on_linux_without_glibc(monkeypatch):
    def missing(name):
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(memory.sys, "platform", "linux")
    monkeypatch.setattr(memory.ctypes, "CDLL", missing)  # e.g. Alpine's musl
    assert memory.malloc_trim() is False


def test_malloc_trim_calls_glibc_with_zero_on_linux(monkeypatch):
    calls = []

    class FakeLibc:
        def malloc_trim(self, pad):
            calls.append(pad)
            return 1

    monkeypatch.setattr(memory.sys, "platform", "linux")
    monkeypatch.setattr(memory.ctypes, "CDLL", lambda name: FakeLibc() if name == "libc.so.6" else None)
    assert memory.malloc_trim() is True
    assert calls == [0]


def test_release_memory_collects_then_reports_rss(monkeypatch):
    collected = []
    monkeypatch.setattr(memory.gc, "collect", lambda: collected.append(True) or 0)
    before, after, trimmed = memory.release_memory()
    assert collected == [True]
    assert before > 0 and after > 0
    assert trimmed is (memory._libc_malloc_trim() is not None)


def test_snapshot_reads_rss_and_high_water():
    reading = memory.snapshot("parsed")
    assert reading.phase == "parsed" and reading.rss_mb > 0
    assert reading.hwm_mb is None or reading.hwm_mb >= reading.rss_mb - 1  # hwm can't trail rss
