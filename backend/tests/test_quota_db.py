"""PostgresQuotaStore against a real Postgres. Runs only when TEST_DATABASE_URL is
set (in the environment or backend/.env); otherwise skips.

Every run works in its own throwaway schema, dropped at the end, so pointing
TEST_DATABASE_URL at the Neon database the service uses never touches its
daily_usage table. The schema is named in the SQL rather than set as a
search_path, because Neon's pooled endpoint (PgBouncer) drops startup options.
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from dotenv import dotenv_values

from backend.quota import PostgresQuotaStore, QuotaUnavailable, warm_up
from backend.settings import BACKEND_DIR

DAY = date(2026, 10, 20)


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get("TEST_DATABASE_URL")


needs_db = pytest.mark.skipif(_test_database_url() is None, reason="TEST_DATABASE_URL is not set")


@pytest.fixture(scope="module")
def schema() -> Iterator[str]:
    import psycopg

    name = f"ads_test_{uuid.uuid4().hex[:12]}"
    yield name
    with psycopg.connect(_test_database_url(), autocommit=True, prepare_threshold=None) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@pytest.fixture
def store(schema: str) -> Iterator[PostgresQuotaStore]:
    store = PostgresQuotaStore(_test_database_url(), schema=schema)
    yield store
    store.close()


def _day(offset: int) -> date:
    """A distinct day per test, so tests sharing the module's schema don't collide."""
    return date.fromordinal(DAY.toordinal() + offset)


@needs_db
def test_the_table_is_created_on_first_use(store, schema):
    import psycopg

    assert store.used(_day(0)) == 0
    with psycopg.connect(_test_database_url(), autocommit=True, prepare_threshold=None) as conn:
        columns = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'daily_usage' ORDER BY ordinal_position",
            (schema,),
        ).fetchall()
    assert columns == [("day", "date"), ("sessions", "integer")]


@needs_db
def test_reserve_stops_exactly_at_the_cap(store):
    day = _day(1)
    assert [store.reserve(day, 2) for _ in range(3)] == [1, 2, None]
    assert store.used(day) == 2


@needs_db
def test_days_are_counted_separately(store):
    assert store.reserve(_day(2), 1) == 1
    assert store.reserve(_day(2), 1) is None
    assert store.reserve(_day(3), 1) == 1  # the next Pacific day starts empty


@needs_db
@pytest.mark.parametrize("round_", range(3))
def test_two_concurrent_starts_for_the_last_slot_have_exactly_one_winner(schema, round_):
    """Two stores, so two pools and two real connections, released at the same instant."""
    day = _day(10 + round_)
    stores = [PostgresQuotaStore(_test_database_url(), schema=schema, max_size=1) for _ in range(2)]
    try:
        for s in stores:
            s.ensure_schema()
        assert stores[0].reserve(day, 10) == 1  # one slot left under a cap of 2
        barrier = threading.Barrier(2)
        results: list[int | None] = [None, None]

        def start(i: int) -> None:
            barrier.wait()
            results[i] = stores[i].reserve(day, 2)

        threads = [threading.Thread(target=start, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results, key=lambda r: r is None) == [2, None]
        assert stores[0].used(day) == 2
    finally:
        for s in stores:
            s.close()


@needs_db
def test_a_dead_pooled_connection_is_replaced_without_an_error(schema):
    """What Neon suspending after 5 idle minutes leaves: a pooled connection whose
    socket is gone while the pool still thinks it's idle and fine. The socket is
    shut down underneath libpq here; without the pool's check this read raised
    QuotaUnavailable (the 503 the first live e2e run hit)."""
    store = PostgresQuotaStore(_test_database_url(), schema=schema, max_size=1)
    try:
        store.ensure_schema()
        with store._pool.connection() as conn:
            dead = conn
            sock = socket.socket(fileno=conn.pgconn.socket)
            sock.shutdown(socket.SHUT_RDWR)
            sock.detach()  # libpq keeps the handle; nothing tells the pool
        assert store.used(_day(20)) == 0
        assert store.reserve(_day(20), 1) == 1
        with store._pool.connection() as conn:
            assert conn is not dead  # the dead one was discarded, a new one opened
    finally:
        store.close()


def test_the_pool_checks_connections_on_checkout():
    """The fix above as configuration, checked without a database."""
    from psycopg_pool import ConnectionPool

    store = PostgresQuotaStore("postgresql://nobody@127.0.0.1:1/none", timeout_s=1.0)
    try:
        assert store._pool._check == ConnectionPool.check_connection
    finally:
        store.close()


def test_an_unreachable_database_raises_quota_unavailable():
    """No real database needed: nothing listens on port 1."""
    store = PostgresQuotaStore("postgresql://nobody@127.0.0.1:1/none", timeout_s=1.0)
    try:
        with pytest.raises(QuotaUnavailable):
            store.used(DAY)
        assert warm_up(store, attempts=2, sleep=lambda _s: None) is False
    finally:
        store.close()
