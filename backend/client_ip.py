"""Who is asking: the client IP behind Render's proxy, and the soft per-IP limit on
defense starts.

X-Forwarded-For is a list the client can start: anything it sends arrives first,
and each proxy appends the address it received the connection from. Only the
entries our own proxies appended can be trusted, so the client is the entry
`trusted_proxy_hops` from the right (1 on Render: the address its proxy saw).
Entries further left are whatever the client chose to send and are ignored.
uvicorn runs with --no-proxy-headers so `request.client` stays the TCP peer, and
this module is the only place the header is read.

The limit is in memory and resets on restart, which the M2 brief accepts: it's a
speed bump for one person refreshing, not a security boundary. The daily quota
in Postgres is the real guard.
"""

from __future__ import annotations

import ipaddress
import threading
from datetime import date

from starlette.requests import Request


def forwarded_for(request: Request) -> list[str]:
    """Every X-Forwarded-For entry, left to right, across repeated headers."""
    joined = ",".join(request.headers.getlist("x-forwarded-for"))
    return [entry.strip() for entry in joined.split(",") if entry.strip()]


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """The entry `trusted_proxy_hops` from the right, if there are that many and it
    is an IP address; otherwise the TCP peer. 0 hops never reads the header."""
    peer = request.client.host if request.client else "unknown"
    entries = forwarded_for(request)
    if trusted_proxy_hops == 0 or len(entries) < trusted_proxy_hops:
        return peer
    candidate = entries[-trusted_proxy_hops]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


class StartLimiter:
    """At most `limit` defense starts per IP per Pacific day."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._day: date | None = None
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def take(self, day: date, ip: str) -> bool:
        """Counts one start for `ip` today; False, counting nothing, if it's at the limit."""
        with self._lock:
            if day != self._day:  # a new day: yesterday's counts go
                self._day, self._counts = day, {}
            if self._counts.get(ip, 0) >= self._limit:
                return False
            self._counts[ip] = self._counts.get(ip, 0) + 1
            return True

    def give_back(self, day: date, ip: str) -> None:
        """Undoes a `take` whose start went no further (the daily quota refused it)."""
        with self._lock:
            if day == self._day and self._counts.get(ip, 0) > 0:
                self._counts[ip] -= 1
