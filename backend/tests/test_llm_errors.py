"""Real Gemini error bodies through the real GeminiProvider, to the backend's
busy rule. The two 429 fixtures were captured with scripts/capture_429.py on
2026-10-20 against a dev project:

- per_minute_flash_lite_burst.json: the 19th request in 26 s on flash-lite; a
  probe 65 s later succeeded.
- per_day_2_5_flash_daily.json: the 21st request of the day on 2.5-flash, paced
  under its RPM; a probe 65 s later 429'd again.

Both must come out "busy", the same as each other. Google's client is mocked,
so nothing goes over the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel

from academic_defense_simulator.llm import gemini_provider as provider_module
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import (
    LLMProviderError,
    LLMQuotaExhaustedError,
    LLMServiceUnavailableError,
)
from backend.llm_errors import is_panel_busy

FIXTURES = Path(__file__).parent / "fixtures" / "gemini_429"


class Out(BaseModel):
    x: int


def _captured(name: str) -> dict:
    """The first 429 record in a capture file."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["records"][0]


def _provider(model: str, errors: list[Exception]) -> tuple[GeminiProvider, MagicMock]:
    client = MagicMock()
    client.models.generate_content.side_effect = errors
    with patch("google.genai.Client", return_value=client):
        return GeminiProvider(api_key="k", model=model), client


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(provider_module.time, "sleep", lambda _s: None)  # retry backoff and RPM pacing


@pytest.mark.parametrize(
    "fixture, model, quota_id",
    [
        ("per_minute_flash_lite_burst.json", "gemini-3.1-flash-lite", "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"),
        ("per_day_2_5_flash_daily.json", "gemini-2.5-flash", "GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
    ],
)
@pytest.mark.parametrize("call", ["structured", "text"])
def test_each_captured_429_is_busy(fixture, model, quota_id, call):
    record = _captured(fixture)
    [violation] = next(d for d in record["details"]["error"]["details"] if "violations" in d)["violations"]
    assert violation["quotaId"] == quota_id  # what a classifier would read; the busy rule doesn't
    provider, client = _provider(model, [genai_errors.ClientError(record["code"], record["details"])])

    with pytest.raises(LLMQuotaExhaustedError) as info:
        provider.generate_structured("p", Out) if call == "structured" else provider.generate_text("p")

    assert info.value.model == model
    assert is_panel_busy(info.value)
    assert client.models.generate_content.call_count == 1  # no retry on a 429


def _unavailable() -> genai_errors.ServerError:
    return genai_errors.ServerError(
        503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "The model is overloaded. Please try again later."}}
    )


@pytest.mark.parametrize("call", ["structured", "text"])
def test_503_on_the_retry_too_is_busy(call):
    provider, client = _provider("gemini-3.1-flash-lite", [_unavailable(), _unavailable()])
    with pytest.raises(LLMServiceUnavailableError) as info:
        provider.generate_structured("p", Out) if call == "structured" else provider.generate_text("p")
    assert is_panel_busy(info.value) and info.value.model == "gemini-3.1-flash-lite"
    assert "retried once and failed again" in str(info.value)  # v1's message, unchanged
    assert client.models.generate_content.call_count == 2


def test_a_503_the_retry_clears_is_not_an_error():
    provider, _ = _provider("gemini-3.1-flash-lite", [_unavailable(), MagicMock(text='{"x": 1}')])
    assert provider.generate_structured("p", Out) == Out(x=1)


@pytest.mark.parametrize(
    "retry_error",
    [
        genai_errors.ServerError(500, {"error": {"code": 500, "status": "INTERNAL", "message": "Internal error"}}),
        genai_errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad"}}),
    ],
)
def test_other_failures_are_not_busy(retry_error):
    provider, _ = _provider("gemini-3.1-flash-lite", [_unavailable(), retry_error])
    with pytest.raises(LLMProviderError) as info:
        provider.generate_structured("p", Out)
    assert type(info.value) is LLMProviderError
    assert not is_panel_busy(info.value)
