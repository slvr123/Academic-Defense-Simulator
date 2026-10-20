"""GeminiProvider construction + key-validation tests (v0.4a Brief step 7). No
real network calls — the google-genai SDK's Client is mocked at the
`google.genai.Client` attribute, the same target regardless of where a caller's
local `from google import genai` import resolves it from.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from academic_defense_simulator.llm import gemini_provider as provider_module
from academic_defense_simulator.llm.gemini_provider import GeminiProvider, validate_gemini_key


def test_constructor_passes_the_given_key_to_the_client():
    with patch("google.genai.Client") as mock_client_cls:
        GeminiProvider(api_key="sk-test-distinct-key-123", model="gemini-2.5-flash")
    assert mock_client_cls.call_count == 1
    _, kwargs = mock_client_cls.call_args
    assert kwargs["api_key"] == "sk-test-distinct-key-123"


def test_two_providers_constructed_with_different_keys_each_get_their_own():
    """v0.4a Decision 4's security property at the construction boundary: two
    GeminiProvider instances built with different keys must each pass THEIR OWN
    key to their own Client — never share one client/key across instances. This
    is the unit-level half of the DoD's two-keys-two-sessions evidence; the live
    half is the browser/log evidence in the verification notes."""
    with patch("google.genai.Client") as mock_client_cls:
        GeminiProvider(api_key="key-for-session-a")
        GeminiProvider(api_key="key-for-session-b")
    assert mock_client_cls.call_count == 2
    keys_used = [call.kwargs["api_key"] for call in mock_client_cls.call_args_list]
    assert keys_used == ["key-for-session-a", "key-for-session-b"]


def test_validate_gemini_key_true_on_successful_list():
    mock_client = MagicMock()
    mock_client.models.list.return_value = [MagicMock()]
    with patch("google.genai.Client", return_value=mock_client):
        assert validate_gemini_key("some-key") is True


def test_validate_gemini_key_false_on_api_error():
    from google.genai import errors as genai_errors

    mock_client = MagicMock()
    mock_client.models.list.side_effect = genai_errors.ClientError(
        400, {"error": {"message": "API key not valid", "status": "INVALID_ARGUMENT"}}
    )
    with patch("google.genai.Client", return_value=mock_client):
        assert validate_gemini_key("bad-key") is False


def test_validate_gemini_key_false_on_network_error():
    import httpx

    mock_client = MagicMock()
    mock_client.models.list.side_effect = httpx.ConnectError("no route")
    with patch("google.genai.Client", return_value=mock_client):
        assert validate_gemini_key("some-key") is False


# v1.0a B'': sliding-window RPM limiter tests. Each test uses a unique fake model
# name as its rate-limiter key so tests never share (and don't need to reset)
# module-level _call_timestamps/_RPM_CEILING state.


def test_throttle_sleeps_zero_on_a_fresh_window(monkeypatch):
    model = "test-model-fresh-window"
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: 1000.0)
    sleep_calls = []
    monkeypatch.setattr(provider_module.time, "sleep", lambda s: sleep_calls.append(s))

    provider_module._throttle_for_rpm(model)

    assert sleep_calls == []


def test_throttle_allows_a_burst_up_to_the_ceiling_without_sleeping(monkeypatch):
    model = "test-model-burst-ok"
    monkeypatch.setitem(provider_module._RPM_CEILING, model, 3)
    now = [1000.0]
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: now[0])
    sleep_calls = []
    monkeypatch.setattr(provider_module.time, "sleep", lambda s: sleep_calls.append(s))

    for _ in range(3):
        provider_module._throttle_for_rpm(model)
        now[0] += 0.01  # back-to-back calls, negligible real time between them

    assert sleep_calls == []
    assert len(provider_module._call_timestamps[model]) == 3


def test_throttle_sleeps_the_exact_deficit_once_the_window_is_full(monkeypatch):
    model = "test-model-burst-throttles"
    monkeypatch.setitem(provider_module._RPM_CEILING, model, 2)
    now = [2000.0]
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: now[0])
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        now[0] += seconds  # simulate real time passing during the sleep

    monkeypatch.setattr(provider_module.time, "sleep", fake_sleep)

    provider_module._throttle_for_rpm(model)  # call 1 at t=2000.00
    now[0] += 0.01
    provider_module._throttle_for_rpm(model)  # call 2 at t=2000.01 -- window now full (2/2)
    now[0] += 0.01
    provider_module._throttle_for_rpm(model)  # call 3 at t=2000.02 -- must wait for call 1 to age out

    assert len(sleep_calls) == 1
    expected_deficit = provider_module._RPM_WINDOW_SECONDS - 0.02
    assert sleep_calls[0] == pytest.approx(expected_deficit, abs=0.01)


def test_throttle_unknown_model_uses_the_default_ceiling(monkeypatch):
    model = "test-model-totally-unrecognized"
    assert model not in provider_module._RPM_CEILING
    now = [3000.0]
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: now[0])
    sleep_calls = []
    monkeypatch.setattr(provider_module.time, "sleep", lambda s: sleep_calls.append(s))

    for _ in range(provider_module._DEFAULT_RPM_CEILING):
        provider_module._throttle_for_rpm(model)  # exactly at the default ceiling, still under

    assert sleep_calls == []

    provider_module._throttle_for_rpm(model)  # one more tips it over

    assert len(sleep_calls) == 1


def test_call_once_invokes_the_rpm_throttle_before_every_attempt():
    """Wiring check: _call_once (the real network chokepoint) must call the
    throttle on every attempt, not just from the retry path -- the whole point of
    B'' is that the ordinary one-call case is covered too, not only grounding
    retries (evidence/v1.0a-pathclear-rpm-verification.txt)."""
    from academic_defense_simulator.models.panelist_output import PanelistQuestion

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(
            text='{"question": "q", "grounding_reference": "g", "difficulty_level": 1}'
        )
        mock_client_cls.return_value = mock_client
        provider = GeminiProvider(api_key="k", model="gemini-2.5-flash")

    with patch.object(provider_module, "_throttle_for_rpm") as mock_throttle:
        provider._call_once("prompt", PanelistQuestion)

    mock_throttle.assert_called_once_with("gemini-2.5-flash", None)  # None: v1's _RPM_CEILING table


# M2 change C: an optional per-provider RPM ceiling, for the backend's per-model settings.


def test_an_explicit_ceiling_overrides_the_table(monkeypatch):
    model = "test-model-explicit-ceiling"
    monkeypatch.setitem(provider_module._RPM_CEILING, model, 10)
    now = [4000.0]
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: now[0])
    sleep_calls = []
    monkeypatch.setattr(provider_module.time, "sleep", lambda s: sleep_calls.append(s))

    for _ in range(3):
        provider_module._throttle_for_rpm(model, 3)
    assert sleep_calls == []
    provider_module._throttle_for_rpm(model, 3)  # the 4th inside a minute waits, though the table says 10
    assert len(sleep_calls) == 1


def test_provider_passes_its_ceiling_to_the_throttle():
    from academic_defense_simulator.models.panelist_output import PanelistQuestion

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(
            text='{"question": "q", "grounding_reference": "g", "difficulty_level": 1}'
        )
        mock_client_cls.return_value = mock_client
        provider = GeminiProvider(api_key="k", model="gemini-2.5-flash", rpm_ceiling=3)

    with patch.object(provider_module, "_throttle_for_rpm") as mock_throttle:
        provider._call_once("prompt", PanelistQuestion)
        mock_client.models.generate_content.return_value = MagicMock(text="narrative")
        provider._call_once_text("prompt")

    assert [c.args for c in mock_throttle.call_args_list] == [("gemini-2.5-flash", 3)] * 2


# M2 change B: a 429 raises LLMQuotaExhaustedError, a subclass, with the model on it.


def _provider_whose_calls_429(model):
    from google.genai import errors as genai_errors

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = genai_errors.ClientError(
        429, {"error": {"code": 429, "message": "Resource has been exhausted", "status": "RESOURCE_EXHAUSTED"}}
    )
    with patch("google.genai.Client", return_value=mock_client):
        provider = GeminiProvider(api_key="k", model=model)
    return provider, mock_client


@pytest.mark.parametrize("call", ["structured", "text"])
def test_429_raises_quota_exhausted_with_the_model_and_no_retry(call, monkeypatch):
    from pydantic import BaseModel

    from academic_defense_simulator.llm.provider import LLMProviderError, LLMQuotaExhaustedError

    class Out(BaseModel):
        x: int

    monkeypatch.setattr(provider_module.time, "sleep", lambda s: None)
    provider, client = _provider_whose_calls_429(f"test-model-429-{call}")
    with pytest.raises(LLMQuotaExhaustedError) as info:
        provider.generate_structured("p", Out) if call == "structured" else provider.generate_text("p")
    assert info.value.model == f"test-model-429-{call}"
    assert isinstance(info.value, LLMProviderError)
    assert "real quota exhaustion, not a pacing bug" in str(info.value)  # v1's message, unchanged
    assert client.models.generate_content.call_count == 1  # still no retry on a 429


def test_existing_llmprovidererror_handlers_still_catch_a_429():
    """v1 call sites catch LLMProviderError; the subclass must not escape them.
    generate_panel's retry-then-fallback is one such handler."""
    from academic_defense_simulator.llm.provider import LLMQuotaExhaustedError
    from academic_defense_simulator.models.defense_profile import DefenseProfile, DefenseType
    from academic_defense_simulator.panel import PANEL_COMPOSITION, generate_panel

    class QuotaProvider:
        def generate_structured(self, prompt, response_model):
            raise LLMQuotaExhaustedError("429", model="m")

    keys = PANEL_COMPOSITION["thesis"][:3]
    profile = DefenseProfile(
        defense_type=DefenseType.THESIS, domain="d", topic="t", selected_archetypes=keys, document_id="doc"
    )
    panel, fallback_used = generate_panel(profile, keys, QuotaProvider())
    assert fallback_used and len(panel) == 3
