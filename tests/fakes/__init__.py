"""Fakes required by harness §8. ``FakeProvider`` and ``FakeInput`` arrive with the
modules they stand in for; ``FakeClock`` is needed now."""

from .clock import FakeClock

__all__ = ["FakeClock"]
