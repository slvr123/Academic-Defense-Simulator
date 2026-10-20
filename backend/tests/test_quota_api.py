"""GET /quota and the upload's non-reserving quota check, against the in-memory
store and a settable clock."""

from __future__ import annotations

from datetime import date, timedelta

from backend.quota import QuotaUnavailable
from backend.tests.conftest import upload, wait_for

TODAY = date(2026, 10, 20)  # FakeClock starts at noon Pacific on this day


def _fill(store, day: date, n: int) -> None:
    for _ in range(n):
        assert store.reserve(day, 100) is not None


def test_quota_reports_used_cap_remaining_and_the_pacific_reset(client_factory, quota_store):
    _fill(quota_store, TODAY, 4)
    with client_factory() as client:
        body = client.get("/quota").json()
    assert body == {"used": 4, "cap": 11, "remaining": 7, "resets_at": "2026-10-21T00:00:00-07:00"}


def test_quota_starts_over_after_midnight_pacific(client_factory, quota_store, clock):
    _fill(quota_store, TODAY, 11)
    with client_factory() as client:
        assert client.get("/quota").json()["remaining"] == 0
        clock.now += timedelta(hours=11, minutes=59, seconds=59)  # 23:59:59 PDT
        assert client.get("/quota").json()["remaining"] == 0
        clock.now += timedelta(seconds=1)
        body = client.get("/quota").json()
    assert (body["used"], body["remaining"], body["resets_at"]) == (0, 11, "2026-10-22T00:00:00-07:00")


def test_upload_is_refused_once_the_day_is_used_up(client_factory, quota_store, embedder, pdf):
    _fill(quota_store, TODAY, 11)
    with client_factory() as client:
        response = upload(client, pdf)
    assert response.status_code == 429
    assert response.json()["detail"] == (
        "Today's demo defenses are all used up. They reset at midnight Pacific time, in about 12 hours."
    )
    assert response.headers["retry-after"] == str(12 * 3600)
    assert embedder.calls == 0  # refused before any work


def test_upload_with_one_slot_left_goes_through_and_reserves_nothing(client_factory, quota_store, pdf):
    _fill(quota_store, TODAY, 10)
    with client_factory() as client:
        response = upload(client, pdf)
        assert response.status_code == 202
        wait_for(client, response.json()["session_id"], lambda s: s.get("status") == "ready")
        assert client.get("/quota").json()["used"] == 10  # only starting a defense reserves


def test_a_cap_of_zero_refuses_every_upload(client_factory, pdf):
    with client_factory(gemini_model="gemini-2.5-flash") as client:  # 41 calls against 20 RPD
        assert client.get("/quota").json()["cap"] == 0
        assert upload(client, pdf).status_code == 429


class DownStore:
    def used(self, day):
        raise QuotaUnavailable("connection refused")

    def reserve(self, day, cap):
        raise QuotaUnavailable("connection refused")

    def close(self):
        pass


def test_store_down_upload_fails_open_and_quota_is_503(tmp_path, embedder, clock, gemini, pdf):
    from fastapi.testclient import TestClient

    from backend.app import QUOTA_UNAVAILABLE_MESSAGE, create_app
    from backend.tests.conftest import make_settings

    app = create_app(
        make_settings(upload_dir=tmp_path / "uploads"), embedder_factory=lambda _s: embedder,
        quota_store_factory=lambda _s: DownStore(), clock=clock, providers_factory=lambda _s: gemini.providers,
    )
    with TestClient(app) as client:
        assert upload(client, pdf).status_code == 202
        response = client.get("/quota")
    assert response.status_code == 503 and response.json()["detail"] == QUOTA_UNAVAILABLE_MESSAGE


def test_the_server_refuses_to_start_without_a_database_url(tmp_path, embedder):
    import pytest
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.tests.conftest import make_settings

    app = create_app(make_settings(upload_dir=tmp_path / "uploads"), embedder_factory=lambda _s: embedder)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        with TestClient(app):
            pass
