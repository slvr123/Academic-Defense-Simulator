"""GeminiProvider construction + key-validation tests (v0.4a Brief step 7). No
real network calls — the google-genai SDK's Client is mocked at the
`google.genai.Client` attribute, the same target regardless of where a caller's
local `from google import genai` import resolves it from.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
