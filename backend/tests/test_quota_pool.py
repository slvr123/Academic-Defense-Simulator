"""M14 D5: the quota pool holds no connection while idle (min_size=0), so nothing
reconnects to Neon in the background while the keep-alive keeps the service up."""

from __future__ import annotations

import time
import uuid

import pytest

from backend.quota import PostgresQuotaStore
from backend.tests.test_quota_db import DAY, _test_database_url, needs_db

# Nothing listens on port 1: any connection attempt would fail and be counted.
UNREACHABLE = "host=127.0.0.1 port=1 connect_timeout=1"


def test_an_opened_pool_holds_and_attempts_no_connection():
    store = PostgresQuotaStore(UNREACHABLE)
    try:
        time.sleep(1.5)  # time for a min_size > 0 pool to have tried to fill itself
        stats = store._pool.get_stats()
        assert store._pool.min_size == 0
        assert stats.get("pool_size", 0) == 0
        assert stats.get("connections_num", 0) == 0 and stats.get("connections_errors", 0) == 0
    finally:
        store.close()


@needs_db
def test_a_used_connection_closes_after_idling():
    schema = f"ads_test_{uuid.uuid4().hex[:12]}"
    store = PostgresQuotaStore(_test_database_url(), schema=schema, max_idle_s=1.0)
    try:
        store.ensure_schema()
        store.used(DAY)
        assert store._pool.get_stats()["pool_size"] >= 1
        deadline = time.monotonic() + 15
        while store._pool.get_stats()["pool_size"] > 0 and time.monotonic() < deadline:
            time.sleep(0.5)
        assert store._pool.get_stats()["pool_size"] == 0
    finally:
        with store._connection() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        store.close()
