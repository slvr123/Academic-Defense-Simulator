"""M17: a visitor's own Gemini key (docs/v2.0-m17-decisions.md). /keys/check, the
key held on the session and used to build the providers, own-key starts that never
touch the quota, the suggestions fallback, and the two mid-defense error codes."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel, SecretStr

from academic_defense_simulator.llm import gemini_provider as provider_module
from academic_defense_simulator.llm.gemini_provider import GeminiProvider
from academic_defense_simulator.llm.provider import (
    CallCounter,
    LLMProviderError,
    LLMQuotaExhaustedError,
    LLMServiceUnavailableError,
)
from backend.defense_routes import PANEL_ERROR_MESSAGE, gemini_providers_factory
from backend.llm_errors import PANEL_BUSY_MESSAGE
from backend.own_key import (
    INVALID_KEY_MESSAGE,
    KEY_HEADER,
    OWN_KEY_LIMITED_MESSAGE,
    OWN_KEY_REJECTED_MESSAGE,
    SuggestionsWithFallback,
)
from backend.tests.conftest import make_pdf, make_settings, wait_for

SENTINEL = "AIzaM17-SENTINEL-own-key-5b0e"
SERVER_KEY = "AIzaM17-server-key-not-real"


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "Library System.pdf", pages=16)


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(provider_module.time, "sleep", lambda _s: None)  # v1's retry backoff and RPM pacing


@pytest.fixture
def own(key_checker):
    key_checker.accepted.add(SENTINEL)
    return {KEY_HEADER: SENTINEL}


def _upload(client, pdf, headers=None):
    with pdf.open("rb") as handle:
        return client.post("/sessions", files={"file": (pdf.name, handle, "application/pdf")}, headers=headers or {})


def _ready(client, pdf, headers=None) -> str:
    response = _upload(client, pdf, headers)
    assert response.status_code == 202, response.text
    session_id = response.json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    return session_id


def _answer(client, session_id, text="Because the evaluation used thirty respondents."):
    return client.post(f"/sessions/{session_id}/answers", json={"text": text})


def _api_error(code: int, status: str, message: str) -> genai_errors.APIError:
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, {"error": {"code": code, "message": message, "status": status}})


def _provider_error(api_error: genai_errors.APIError) -> LLMProviderError:
    """What v1's GeminiProvider raises after its retry: a plain error, `from` the APIError."""
    try:
        raise LLMProviderError(f"Gemini API error, retried once and failed again. (retry: {api_error!r})") from api_error
    except LLMProviderError as exc:
        return exc


# --- /keys/check ---


def test_keys_check_accepts_a_key_google_accepts(client_factory, key_checker, own):
    with client_factory() as client:
        response = client.post("/keys/check", headers=own)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert key_checker.calls == 1


@pytest.mark.parametrize("headers", [{KEY_HEADER: "AIza-wrong"}, {KEY_HEADER: "   "}, {}])
def test_keys_check_rejects_a_bad_blank_or_missing_key(client_factory, key_checker, headers):
    with client_factory() as client:
        response = client.post("/keys/check", headers=headers)
    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_KEY_MESSAGE, "code": "invalid_key"}


def test_the_key_header_passes_cors_preflight(client_factory):
    origin = "http://localhost:5173"
    with client_factory(cors_origins=origin) as client:
        preflight = client.options(
            "/sessions",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "x-gemini-key"},
        )
    assert preflight.status_code == 200
    assert "x-gemini-key" in preflight.headers["access-control-allow-headers"].lower()


# --- the key on the session, and the providers built with it ---


def test_the_upload_checks_the_key_again_and_holds_it_on_the_session(client_factory, key_checker, own, long_pdf):
    with client_factory() as client:
        session_id = _ready(client, long_pdf, own)
        session = client.app.state.store.get(session_id)
        assert isinstance(session.gemini_key, SecretStr)
        assert session.gemini_key.get_secret_value() == SENTINEL
        assert SENTINEL not in repr(session)
        client.delete(f"/sessions/{session_id}")
    assert key_checker.calls == 1  # the upload's own check
    assert session.gemini_key is None  # the key dies with the session


def test_an_upload_with_a_refused_key_is_400_and_makes_no_session(client_factory, key_checker, long_pdf):
    with client_factory() as client:
        response = _upload(client, long_pdf, {KEY_HEADER: "AIza-wrong"})
        live = len(client.app.state.store)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_key"
    assert live == 0


def test_an_expired_session_drops_its_key(client_factory, own, long_pdf):
    with client_factory() as client:
        store = client.app.state.store
        session_id = _ready(client, long_pdf, own)
        session = store.get(session_id)
        store._ttl_seconds = -1  # every finished session is now past its TTL
        store.sweep()
    assert session.gemini_key is None


def test_an_own_key_defense_builds_its_providers_with_that_key(client_factory, gemini, own, long_pdf):
    with client_factory() as client:
        own_id = _ready(client, long_pdf, own)
        assert client.post(f"/sessions/{own_id}/start").status_code == 200
        server_id = _ready(client, long_pdf)
        assert client.post(f"/sessions/{server_id}/start").status_code == 200
    assert gemini.own_keys == [SENTINEL, None]


def test_the_sentinel_key_reaches_every_gemini_client_config():
    """The real factory with genai.Client mocked: the own key, not the server's, reaches
    every client, and only an own-key defense wraps suggestions in the fallback."""
    settings = make_settings(GEMINI_API_KEY=SERVER_KEY)
    with patch("google.genai.Client") as client_cls:
        own = gemini_providers_factory(settings)(CallCounter(), SecretStr(SENTINEL))
        own_keys = [call.kwargs["api_key"] for call in client_cls.call_args_list]
        client_cls.reset_mock()
        server = gemini_providers_factory(settings)(CallCounter())
        server_keys = [call.kwargs["api_key"] for call in client_cls.call_args_list]
    assert own_keys == [SENTINEL, SENTINEL, SENTINEL]  # defense, suggestions, the suggestions fallback
    assert server_keys == [SERVER_KEY, SERVER_KEY]
    assert isinstance(own.suggestions, SuggestionsWithFallback)
    assert isinstance(server.suggestions, GeminiProvider)
    assert SENTINEL not in repr(own) and SENTINEL not in repr(own.suggestions)


# --- the quota ---


def test_an_own_key_start_never_calls_reserve(client_factory, quota_store, own, long_pdf, monkeypatch):
    reserve = MagicMock(wraps=quota_store.reserve)
    monkeypatch.setattr(quota_store, "reserve", reserve)
    with client_factory() as client:
        session_id = _ready(client, long_pdf, own)
        assert client.post(f"/sessions/{session_id}/start").status_code == 200
        while not _answer(client, session_id).json()["done"]:
            pass
        quota = client.get("/quota").json()
    reserve.assert_not_called()
    assert quota["used"] == 0


def test_own_key_defenses_run_when_the_free_quota_and_the_ip_limit_are_used_up(
    client_factory, quota_store, clock, own, long_pdf
):
    with client_factory(per_ip_daily_starts=1) as client:
        cap = client.get("/quota").json()["cap"]
        day = clock.now.date()
        for _ in range(cap):
            quota_store.reserve(day, cap)
        assert _upload(client, long_pdf).status_code == 429  # a free upload is refused
        for _ in range(2):  # more than the per-IP limit of 1
            session_id = _ready(client, long_pdf, own)
            assert client.post(f"/sessions/{session_id}/start").status_code == 200
            client.delete(f"/sessions/{session_id}")
        assert client.get("/quota").json()["used"] == cap  # deleting gave nothing back, nothing was taken


# --- mid-defense errors (Decision 4) ---


def _started(client, pdf, headers=None) -> str:
    session_id = _ready(client, pdf, headers)
    assert client.post(f"/sessions/{session_id}/start").status_code == 200
    return session_id


def test_an_own_key_rate_limit_is_own_key_limited(client_factory, gemini, own, long_pdf):
    with client_factory() as client:
        session_id = _started(client, long_pdf, own)
        gemini.fail_next("scoring", LLMQuotaExhaustedError("429", model="gemini-3.1-flash-lite"))
        response = _answer(client, session_id)
        retry = _answer(client, session_id)
    assert response.status_code == 429
    assert response.json() == {"detail": OWN_KEY_LIMITED_MESSAGE, "code": "own_key_limited"}
    assert response.headers["Retry-After"] == "60"
    assert retry.status_code == 200  # nothing was recorded; the same answer goes through


@pytest.mark.parametrize(
    "api_error",
    [
        _api_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),
        _api_error(403, "PERMISSION_DENIED", "Permission denied: Consumer has been suspended."),
        _api_error(401, "UNAUTHENTICATED", "Request had invalid authentication credentials."),
    ],
)
def test_a_revoked_own_key_is_own_key_rejected(client_factory, gemini, own, long_pdf, api_error):
    with client_factory() as client:
        session_id = _started(client, long_pdf, own)
        gemini.fail_next("scoring", _provider_error(api_error))
        response = _answer(client, session_id)
    assert response.status_code == 403
    assert response.json() == {"detail": OWN_KEY_REJECTED_MESSAGE, "code": "own_key_rejected"}


def test_a_rejected_own_key_on_the_first_question_is_own_key_rejected(client_factory, gemini, own, long_pdf):
    with client_factory() as client:
        session_id = _ready(client, long_pdf, own)
        gemini.fail_next("question", _provider_error(_api_error(400, "INVALID_ARGUMENT", "API key expired.")))
        response = client.post(f"/sessions/{session_id}/start")
    assert response.status_code == 403
    assert response.json()["code"] == "own_key_rejected"


def test_an_overloaded_gemini_stays_panel_busy_on_an_own_key(client_factory, gemini, own, long_pdf):
    with client_factory() as client:
        session_id = _started(client, long_pdf, own)
        gemini.fail_next("scoring", LLMServiceUnavailableError("503", model="gemini-3.1-flash-lite"))
        response = _answer(client, session_id)
    assert response.status_code == 503
    assert response.json() == {"detail": PANEL_BUSY_MESSAGE, "code": "panel_busy"}


def test_a_server_key_defense_maps_the_same_errors_as_before(client_factory, gemini, long_pdf):
    with client_factory() as client:
        session_id = _started(client, long_pdf)
        gemini.fail_next("scoring", LLMQuotaExhaustedError("429", model="gemini-3.1-flash-lite"))
        limited = _answer(client, session_id)
        gemini.fail_next("scoring", _provider_error(_api_error(400, "INVALID_ARGUMENT", "API key not valid.")))
        rejected = _answer(client, session_id)
    assert (limited.status_code, limited.json()["code"]) == (503, "panel_busy")
    assert rejected.status_code == 502 and rejected.json() == {"detail": PANEL_ERROR_MESSAGE}


# --- the suggestions fallback (Decision 3), through the real GeminiProvider ---


class Out(BaseModel):
    x: int


def _gemini(model: str, errors: list[Exception]) -> tuple[GeminiProvider, MagicMock]:
    client = MagicMock()
    client.models.generate_content.side_effect = errors
    with patch("google.genai.Client", return_value=client):
        return GeminiProvider(api_key=SENTINEL, model=model), client


def _ok() -> MagicMock:
    return MagicMock(text='{"x": 7}')


@pytest.mark.parametrize(
    "api_error",
    [
        _api_error(404, "NOT_FOUND", "models/gemini-2.5-flash is not found for API version v1beta."),
        _api_error(403, "PERMISSION_DENIED", "This model is not available to new users."),
    ],
)
def test_suggestions_fall_back_when_the_model_isnt_available(api_error, caplog):
    primary, primary_client = _gemini("gemini-2.5-flash", [api_error, api_error])
    fallback, fallback_client = _gemini("gemini-3.1-flash-lite", [_ok()])
    wrapped = SuggestionsWithFallback(primary, fallback, "gemini-3.1-flash-lite")
    with caplog.at_level(logging.DEBUG):
        assert wrapped.generate_structured("p", Out) == Out(x=7)
    assert primary_client.models.generate_content.call_count == 2  # v1's own retry, then the fallback
    assert fallback_client.models.generate_content.call_count == 1
    [line] = [r.getMessage() for r in caplog.records if r.name == "backend.own_key"]
    assert line == f"suggestions fallback to=gemini-3.1-flash-lite reason={api_error.code} {api_error.status}"
    assert SENTINEL not in caplog.text


def test_suggestions_fall_back_on_a_free_tier_429(caplog):
    """Amendment (2026-11-08): Google often blocks a free-tier model with a 429 whose limit
    is 0. v1 doesn't retry a 429, so the primary is called once, then the fallback once."""
    limit_zero = _api_error(
        429, "RESOURCE_EXHAUSTED",
        "Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, "
        "limit: 0, model: gemini-2.5-flash",
    )
    primary, primary_client = _gemini("gemini-2.5-flash", [limit_zero])
    fallback, fallback_client = _gemini("gemini-3.1-flash-lite", [_ok()])
    wrapped = SuggestionsWithFallback(primary, fallback, "gemini-3.1-flash-lite")
    with caplog.at_level(logging.DEBUG):
        assert wrapped.generate_structured("p", Out) == Out(x=7)
    assert primary_client.models.generate_content.call_count == 1
    assert fallback_client.models.generate_content.call_count == 1
    [line] = [r.getMessage() for r in caplog.records if r.name == "backend.own_key"]
    assert line == "suggestions fallback to=gemini-3.1-flash-lite reason=429 RESOURCE_EXHAUSTED"
    assert SENTINEL not in caplog.text


def test_a_failed_fallback_raises_after_one_try():
    """Only one fallback: if flash-lite also fails, the error goes up, and v1's report
    builder ships the debrief without suggestions, as it does today."""
    limit_zero = _api_error(429, "RESOURCE_EXHAUSTED", "limit: 0")
    primary, _ = _gemini("gemini-2.5-flash", [limit_zero])
    fallback, fallback_client = _gemini("gemini-3.1-flash-lite", [limit_zero])
    with pytest.raises(LLMQuotaExhaustedError):
        SuggestionsWithFallback(primary, fallback, "gemini-3.1-flash-lite").generate_structured("p", Out)
    assert fallback_client.models.generate_content.call_count == 1


@pytest.mark.parametrize(
    "api_error, raised",
    [
        (_api_error(503, "UNAVAILABLE", "The model is overloaded."), LLMServiceUnavailableError),
        (_api_error(500, "INTERNAL", "Internal error."), LLMProviderError),
    ],
)
def test_suggestions_dont_fall_back_on_other_errors(api_error, raised):
    primary, _ = _gemini("gemini-2.5-flash", [api_error, api_error])
    fallback, fallback_client = _gemini("gemini-3.1-flash-lite", [_ok()])
    wrapped = SuggestionsWithFallback(primary, fallback, "gemini-3.1-flash-lite")
    with pytest.raises(raised):
        wrapped.generate_structured("p", Out)
    fallback_client.models.generate_content.assert_not_called()


def test_suggestions_fallback_covers_text_calls_too():
    error = _api_error(404, "NOT_FOUND", "not found")
    primary, _ = _gemini("gemini-2.5-flash", [error, error])
    fallback, _ = _gemini("gemini-3.1-flash-lite", [MagicMock(text="a narrative")])
    assert SuggestionsWithFallback(primary, fallback, "gemini-3.1-flash-lite").generate_text("p") == "a narrative"
