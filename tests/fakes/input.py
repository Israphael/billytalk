"""``FakeInput`` (harness §8): records chords, scripts the modifier state.

The modifier timeline is a list of booleans consumed one poll at a time; when
it runs dry the last value holds. Combined with a fake clock this makes the
500 ms modifier wait fully deterministic — nothing sleeps.
"""

from __future__ import annotations

from billytalk.core.insert.apprules import PasteChord

__all__ = ["FakeInput"]


class FakeInput:
    def __init__(self, *, modifier_timeline: list[bool] | None = None) -> None:
        self.chords: list[PasteChord] = []
        self._timeline = list(modifier_timeline or [False])
        self.slept: list[float] = []
        self.now = 0.0

    # the seams Inserter accepts --------------------------------------- #

    def send_chord(self, chord: PasteChord) -> None:
        self.chords.append(chord)

    def any_modifier_down(self) -> bool:
        if len(self._timeline) > 1:
            return self._timeline.pop(0)
        return self._timeline[0]

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
