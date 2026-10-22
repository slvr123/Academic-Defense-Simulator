"""The client IP behind the proxy, the per-IP start limit, and /debug/client."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from starlette.requests import Request

from backend.client_ip import StartLimiter, client_ip
from backend.defense_routes import QUOTA_DOWN_MESSAGE, per_ip_message
from backend.quota import QuotaUnavailable
from backend.tests.conftest import make_pdf, upload, wait_for

DAY = date(2026, 10, 20)


def _request(*forwarded: str, peer: str = "10.0.0.7") -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return Request({"type": "http", "headers": headers, "client": (peer, 5000)})


# Render's chain as checked live: the client, then Cloudflare, then Render's internal proxy.
CLIENT, CLOUDFLARE, RENDER = "112.208.176.112", "172.70.1.2", "10.201.3.4"
CHAIN = f"{CLIENT}, {CLOUDFLARE}, {RENDER}"


@pytest.mark.parametrize(
    "forwarded, hops, expected",
    [
        ((), 3, "10.0.0.7"),  # no header (local run): the TCP peer
        ((CHAIN,), 3, CLIENT),  # a browser's request through Render
        ((f"203.0.113.9, {CHAIN}",), 3, CLIENT),  # the live spoof check: 4 entries, spoof ignored
        (("203.0.113.9, 198.51.100.7", f"{CLIENT}, {CLOUDFLARE}, {RENDER}"), 3, CLIENT),  # repeated headers
        (("203.0.113.5",), 1, "203.0.113.5"),  # one trusted proxy
        ((f"{CLIENT}, {RENDER}",), 3, "10.0.0.7"),  # fewer entries than trusted hops: the peer
        ((CHAIN,), 0, "10.0.0.7"),  # 0 hops never reads the header
        (("not-an-ip, 172.70.1.2, 10.201.3.4",), 3, "10.0.0.7"),
        (("2001:db8::1, 172.70.1.2, 10.201.3.4",), 3, "2001:db8::1"),
    ],
)
def test_client_ip(forwarded, hops, expected):
    assert client_ip(_request(*forwarded), hops) == expected


def test_a_chain_shorter_than_the_trusted_hops_warns(caplog):
    with caplog.at_level("WARNING", logger="backend.client_ip"):
        assert client_ip(_request(f"{CLIENT}, {RENDER}"), 3) == "10.0.0.7"
        client_ip(_request(CHAIN), 3)
        client_ip(_request(), 3)  # no header at all is a local run, not a warning
    [record] = caplog.records
    assert "has 2 entries, fewer than ADS_TRUSTED_PROXY_HOPS=3" in record.getMessage()


def test_limiter_counts_per_ip_and_starts_over_each_day():
    limiter = StartLimiter(limit=2)
    assert [limiter.take(DAY, "a") for _ in range(3)] == [True, True, False]
    assert limiter.take(DAY, "b")
    limiter.give_back(DAY, "a")
    assert limiter.take(DAY, "a") and not limiter.take(DAY, "a")
    assert limiter.take(DAY + timedelta(days=1), "a")


# --- through the API ---


@pytest.fixture
def long_pdf(tmp_path):
    return make_pdf(tmp_path / "thesis.pdf", pages=16)


def _start_from(client, pdf, ip: str, spoofed: str | None = None):
    session_id = upload(client, pdf).json()["session_id"]
    wait_for(client, session_id, lambda s: s.get("status") == "ready")
    header = f"{spoofed + ', ' if spoofed else ''}{ip}, {CLOUDFLARE}, {RENDER}"  # through Render's chain
    return client.post(f"/sessions/{session_id}/start", headers={"X-Forwarded-For": header})


def test_the_fourth_start_from_one_ip_is_429(client_factory, quota_store, long_pdf):
    with client_factory(max_live_sessions=10) as client:
        assert [_start_from(client, long_pdf, "203.0.113.5").status_code for _ in range(3)] == [200] * 3
        fourth = _start_from(client, long_pdf, "203.0.113.5", spoofed="9.9.9.9")  # spoofing doesn't help
        other = _start_from(client, long_pdf, "198.51.100.1")
    assert fourth.status_code == 429 and fourth.json()["detail"] == per_ip_message(3)
    assert fourth.json()["code"] == "per_ip_limit"
    assert "retry-after" in fourth.headers
    assert other.status_code == 200
    assert quota_store.used(DAY) == 4  # the refused start reserved nothing


def test_a_start_the_quota_refuses_doesnt_count_against_the_ip(client_factory, quota_store, long_pdf):
    with client_factory(per_ip_daily_starts=1) as client:
        original = quota_store.reserve

        def down(day, cap):
            raise QuotaUnavailable("connection refused")

        quota_store.reserve = down
        assert _start_from(client, long_pdf, "203.0.113.5").json()["detail"] == QUOTA_DOWN_MESSAGE
        quota_store.reserve = original
        assert _start_from(client, long_pdf, "203.0.113.5").status_code == 200


def test_debug_client_is_off_by_default_and_shows_the_resolution_when_on(client_factory):
    with client_factory() as client:
        assert client.get("/debug/client").status_code == 404
    with client_factory(debug_client=True) as client:
        body = client.get("/debug/client", headers={"X-Forwarded-For": f"203.0.113.9, {CHAIN}"}).json()
    assert body == {
        "peer": "testclient", "x_forwarded_for": ["203.0.113.9", CLIENT, CLOUDFLARE, RENDER],
        "trusted_proxy_hops": 3, "resolved_ip": CLIENT,
    }
