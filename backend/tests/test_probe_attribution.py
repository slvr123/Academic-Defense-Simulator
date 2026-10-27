"""M7 J4 re-run machinery, offline: the probe's prompt-hash cache, the cross-run call
ledger and cap, the backoff on 503/504, and how literature-review items are shared
between documents. No Gemini call is made."""

from __future__ import annotations

import pytest

import scripts.probe_attribution as probe
from academic_defense_simulator.llm.provider import (
    CallCounter,
    LLMProvider,
    LLMProviderError,
    LLMQuotaExhaustedError,
    LLMServiceUnavailableError,
)
from academic_defense_simulator.models.panelist_output import PanelistQuestion

ANSWER = PanelistQuestion(question="Why that board?", grounding_reference="board", difficulty_level=3)


class ScriptedGemini(LLMProvider):
    """Counts attempts as GeminiProvider does (two per failed call: it retries once)
    and raises the queued errors first."""

    def __init__(self, calls: CallCounter, *errors: Exception) -> None:
        self.calls, self.errors, self.prompts = calls, list(errors), []

    def generate_structured(self, prompt, response_model):
        self.prompts.append(prompt)
        if self.errors:
            self.calls.record("probe")
            self.calls.record("probe")
            raise self.errors.pop(0)
        self.calls.record("probe")
        return ANSWER

    def generate_text(self, prompt):
        raise AssertionError("not used")


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "CACHE", tmp_path / "cache.jsonl")
    monkeypatch.setattr(probe, "LEDGER", tmp_path / "ledger.json")
    sleeps = []
    monkeypatch.setattr(probe.time, "sleep", sleeps.append)
    return sleeps


def _provider(max_calls=150, *errors):
    calls = CallCounter()
    inner = ScriptedGemini(calls, *errors)
    return probe.CachedProvider(inner, calls, max_calls), inner


def test_a_byte_identical_prompt_is_answered_from_the_cache_across_runs(files):
    first, inner = _provider()
    assert first.generate_structured("prompt A", PanelistQuestion) == ANSWER
    assert first.generate_structured("prompt A", PanelistQuestion) == ANSWER
    assert (inner.prompts, first.hits, first.spent) == (["prompt A"], 1, 1)

    second, inner2 = _provider()  # a new run reads the cache and the ledger
    assert second.generate_structured("prompt A", PanelistQuestion) == ANSWER
    assert second.generate_structured("prompt A ", PanelistQuestion) == ANSWER  # one byte differs: a real call
    assert inner2.prompts == ["prompt A "]
    assert (second.hits, second.spent) == (1, 2)


def test_overload_and_timeouts_back_off_and_count_every_attempt(files):
    errors = (LLMServiceUnavailableError("503", model=probe.MODEL), LLMProviderError("504"))
    provider, inner = _provider(150, *errors)
    assert provider.generate_structured("prompt", PanelistQuestion) == ANSWER
    assert len(inner.prompts) == 3
    assert provider.spent == 5  # two failed calls of two attempts, then one
    assert [s for s in files if s >= 30] == [30.0, 60.0]


def test_a_429_stops_at_once(files):
    provider, inner = _provider(150, LLMQuotaExhaustedError("429", model=probe.MODEL))
    with pytest.raises(LLMQuotaExhaustedError):
        provider.generate_structured("prompt", PanelistQuestion)
    assert len(inner.prompts) == 1 and not [s for s in files if s >= 30]


def test_the_cap_counts_earlier_runs_and_stops_before_a_call(files):
    probe.LEDGER.write_text('{"calls": 148}\n', encoding="utf-8")
    provider, inner = _provider(150)
    assert provider.generate_structured("prompt", PanelistQuestion) == ANSWER  # 148 + 2 <= 150
    with pytest.raises(SystemExit, match="past --max-calls 150"):
        provider.generate_structured("another prompt", PanelistQuestion)  # 149 + 2 > 150
    assert inner.prompts == ["prompt"]


def test_calls_are_paced_nine_seconds_apart(files, monkeypatch):
    now = iter([100.0, 100.0, 101.0, 101.0])
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(now))
    provider, _ = _provider()
    provider.generate_structured("one", PanelistQuestion)
    provider.generate_structured("two", PanelistQuestion)
    assert files[-1] == pytest.approx(8.0)  # started 1 s after the last call ended


@pytest.mark.parametrize(
    ("available", "counts"),
    [([14, 0], [10, 0]), ([14, 4], [10, 0]), ([0, 12], [0, 10]), ([7, 6], [5, 5]), ([14, 9], [5, 5])],
)
def test_review_items_come_only_from_documents_with_five_tagged_chunks(available, counts):
    assert probe.review_counts(available) == counts


@pytest.mark.parametrize("available", [[4, 0], [3, 4]])
def test_no_qualifying_document_stops(available):
    with pytest.raises(SystemExit, match="no document has 5"):
        probe.review_counts(available)


def test_one_qualifying_document_with_fewer_than_ten_stops():
    with pytest.raises(SystemExit, match="items from"):
        probe.review_counts([7, 0])


@pytest.mark.parametrize(
    ("picks", "forced", "expected"),
    [
        ([7, 8, 10, 11, 13, 14, 16, 17, 19, 20], 9, [7, 9, 10, 11, 13, 14, 16, 17, 19, 20]),  # tie: the earlier goes
        ([7, 8, 10, 11], 12, [7, 8, 10, 12]),
        ([7, 9, 10], 9, [7, 9, 10]),  # already picked
        ([7, 8, 10], None, [7, 8, 10]),
        ([], 9, []),  # a document giving no literature items
    ],
)
def test_the_forced_chunk_replaces_the_nearest_pick(picks, forced, expected):
    assert probe.with_forced(picks, list(range(7, 21)), forced) == expected


def test_a_forced_chunk_that_isnt_tagged_stops():
    with pytest.raises(SystemExit, match="isn't a tagged"):
        probe.with_forced([7, 8], [7, 8, 10], 9)
