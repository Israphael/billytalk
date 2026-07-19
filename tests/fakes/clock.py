"""A monotonic clock a test can turn by hand.

Harness §8 requires it for a blunt reason: a test that reads the real clock has to
sleep, and a suite that sleeps is a suite that flakes. The machine already takes
``now`` as a parameter, so this is just a tidy way to carry it.
"""

from __future__ import annotations

__all__ = ["FakeClock"]


class FakeClock:
    """Monotonic milliseconds, advanced explicitly."""

    def __init__(self, start: int = 0) -> None:
        self._now = start

    @property
    def now(self) -> int:
        return self._now

    def advance(self, ms: int) -> int:
        """Move forward and return the new time. Never moves backwards."""
        if ms < 0:
            raise ValueError("a monotonic clock does not go backwards")
        self._now += ms
        return self._now
