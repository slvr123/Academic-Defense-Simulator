"""CORS for the browser frontend: only the listed origins, only what the frontend uses."""

from __future__ import annotations

import pytest

from backend.settings import Settings
from backend.tests.conftest import make_settings

FRONTEND = "https://ads-frontend.onrender.com"
LOCAL = "http://localhost:5173"
ORIGINS = f"{FRONTEND}, {LOCAL}"


@pytest.mark.parametrize("origin", [FRONTEND, LOCAL])
def test_a_listed_origin_gets_the_allow_origin_header(client_factory, origin):
    with client_factory(cors_origins=ORIGINS) as client:
        response = client.get("/health", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin


def test_an_unlisted_origin_gets_no_allow_origin_header(client_factory):
    with client_factory(cors_origins=ORIGINS) as client:
        response = client.get("/health", headers={"Origin": "https://evil.example"})
        preflight = client.options(
            "/sessions",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
    assert response.status_code == 200  # CORS is enforced by the browser, not by refusing the request
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-origin" not in preflight.headers


def test_no_origins_configured_allows_none(client_factory):
    with client_factory() as client:
        response = client.get("/health", headers={"Origin": LOCAL})
    assert "access-control-allow-origin" not in response.headers


def test_preflight_allows_only_the_methods_and_headers_the_frontend_uses(client_factory):
    with client_factory(cors_origins=ORIGINS) as client:
        ok = client.options(
            "/sessions/abc/answers",
            headers={"Origin": FRONTEND, "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "content-type"},
        )
        delete = client.options(
            "/sessions/abc", headers={"Origin": FRONTEND, "Access-Control-Request-Method": "DELETE"}
        )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == FRONTEND
    assert set(ok.headers["access-control-allow-methods"].split(", ")) == {"GET", "POST"}
    assert delete.status_code == 400


def test_retry_after_is_readable_cross_origin(client_factory):
    with client_factory(cors_origins=ORIGINS) as client:
        response = client.get("/quota", headers={"Origin": FRONTEND})
    assert response.headers["access-control-expose-headers"] == "Retry-After"


def test_origins_are_read_comma_separated_and_trimmed():
    settings: Settings = make_settings(cors_origins=f" {FRONTEND} ,{LOCAL},, ")
    assert settings.cors_origin_list == [FRONTEND, LOCAL]
