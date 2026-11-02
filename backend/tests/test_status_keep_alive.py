"""M12 D5: the keep-alive pinger hits GET /status every 10 minutes. It must keep the web
service up without touching the quota database (Neon), or each ping would wake Neon too."""

from __future__ import annotations

from datetime import date

from backend.quota import InMemoryQuotaStore


class CountingStore(InMemoryQuotaStore):
    """The in-memory store, counting every call a request makes to it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def used(self, day: date) -> int:
        self.calls.append("used")
        return super().used(day)

    def reserve(self, day: date, cap: int) -> int | None:
        self.calls.append("reserve")
        return super().reserve(day, cap)

    def release(self, day: date) -> int:
        self.calls.append("release")
        return super().release(day)

    def ensure_schema(self) -> None:
        self.calls.append("ensure_schema")


def test_status_answers_without_touching_the_quota_store(client_factory):
    store = CountingStore()
    with client_factory() as client:
        client.app.state.quota = store  # what /quota and /start read; installed after startup's warm-up
        store.calls.clear()
        responses = [client.get("/status") for _ in range(5)]
        assert [r.status_code for r in responses] == [200] * 5
        assert responses[0].json()["status"] == "ok" and responses[0].json()["model_loaded"] is True
        assert store.calls == []
        # The same store does see a request that needs it, so the count above means something.
        client.get("/quota")
        assert store.calls == ["used"]
