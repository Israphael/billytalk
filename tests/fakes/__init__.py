"""Fakes required by harness §8. ``FakeInput`` arrives with the module it stands
in for; ``FakeClock`` and ``FakeProvider`` are here."""

from .clock import FakeClock
from .provider import FakeProvider

__all__ = ["FakeClock", "FakeProvider"]
