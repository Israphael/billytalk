"""The warm connection pool (spec §6): mandatory, not an optimisation.

Measured (research/07): 368 ms on a warm connection against 623 ms cold — the
TLS handshake to ``api.groq.com`` alone costs 540 ms cumulative from Brazil.
That difference is paid on *every dictation*, so the pool is part of the latency
budget, not a nicety.

Connections go stale: Cloudflare closes idle keep-alives, and a request on a
dead socket surfaces as an immediate error that is *not* a network outage. The
pool's answer is an idle TTL — anything unused longer is closed and replaced —
and the provider's answer is one silent retry on a fresh connection when a
*reused* one dies mid-request (see ``groq.py``).

The clock and the connection factory are injectable so the tests turn both by
hand; nothing here sleeps.
"""

from __future__ import annotations

import http.client
import threading
from collections.abc import Callable
from time import monotonic

__all__ = ["PooledConnection", "WarmConnectionPool"]


class PooledConnection:
    """A connection plus the fact the pool needs: has it been used before?

    ``reused`` is what makes the stale-socket retry safe: a *fresh* connection
    dying mid-request is a real network problem, while a *reused* one dying is
    the ordinary death of an idle keep-alive and deserves one silent retry.
    """

    def __init__(self, raw: http.client.HTTPSConnection, *, reused: bool) -> None:
        self.raw = raw
        self.reused = reused


class WarmConnectionPool:
    def __init__(
        self,
        host: str,
        *,
        timeout_s: float = 30.0,
        max_idle_s: float = 60.0,
        factory: Callable[[], http.client.HTTPSConnection] | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._host = host
        self._timeout_s = timeout_s
        self._max_idle_s = max_idle_s
        self._factory = factory or self._default_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._idle: list[tuple[http.client.HTTPSConnection, float]] = []

    def _default_factory(self) -> http.client.HTTPSConnection:
        conn = http.client.HTTPSConnection(self._host, timeout=self._timeout_s)
        # Belt and braces under spec §13: these are the connections that carry
        # the key and the audio, and debuglevel prints via bare print(), past
        # every logging filter. configure_logging resets the class attribute;
        # this pins the instance even if something flips it later.
        conn.set_debuglevel(0)
        return conn

    def warm(self) -> None:
        """Pre-pay the TLS handshake so the next dictation does not.

        Called at startup and re-called by the driver after idle periods; a
        connection already warm and fresh is left alone.
        """
        with self._lock:
            self._drop_stale()
            if self._idle:
                return
        conn = self._factory()
        conn.connect()
        self.release(conn)

    def acquire(self) -> PooledConnection:
        """A warm connection if one is fresh, otherwise a new one.

        The new connection is *not* pre-connected here — ``request`` connects
        implicitly — because acquire runs on the latency path and an extra
        round-trip decision belongs to ``warm``.
        """
        with self._lock:
            self._drop_stale()
            if self._idle:
                raw, _ = self._idle.pop()
                return PooledConnection(raw, reused=True)
        return PooledConnection(self._factory(), reused=False)

    def acquire_fresh(self) -> PooledConnection:
        """A factory-fresh connection, bypassing the idle list.

        The stale-keep-alive retry must run on a socket that did not share the
        dead one's fate: with parallel transcriptions the pool lawfully holds
        several idle connections, and a VPN switch or Wi-Fi roam kills them
        all at once — popping another idle one turns a routine socket death
        into a false network outage (review round 1)."""
        return PooledConnection(self._factory(), reused=False)

    def release(self, raw: http.client.HTTPSConnection) -> None:
        """Return a connection whose response was fully read."""
        with self._lock:
            self._idle.append((raw, self._clock()))

    def discard(self, raw: http.client.HTTPSConnection) -> None:
        """The connection misbehaved; close it and forget it."""
        try:
            raw.close()
        except Exception:
            pass

    def _drop_stale(self) -> None:
        now = self._clock()
        fresh: list[tuple[http.client.HTTPSConnection, float]] = []
        for raw, last_used in self._idle:
            if now - last_used <= self._max_idle_s:
                fresh.append((raw, last_used))
            else:
                try:
                    raw.close()
                except Exception:
                    pass
        self._idle = fresh
