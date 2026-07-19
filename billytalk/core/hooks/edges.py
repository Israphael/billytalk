"""Edge pairing, suppression, auto-repeat and the double-Esc window (spec §2).

Pure logic, deliberately separated from the ctypes layer: every rule in here is
a place the ecosystem has a documented defect, so every rule in here is tested
without Windows in the loop.

The rules, verbatim from spec §2:

* **The suppression decision is taken on the press. A release is suppressed if
  and only if its paired press was suppressed** — regardless of what changed in
  between. This pairing rule outranks every other rule (including "Esc only
  while recording"), because an orphaned release edge is the defect class this
  product exists to prevent: a press swallowed and its release delivered leaves
  the target application with a stuck button.
* Keys already held when the hook is installed are **foreign**: their release is
  not suppressed and produces no event.
* Auto-repeat is squelched by a held-keys set, or a held hotkey would restart
  the recording thirty times a second.
* A single Esc always passes through; two presses within 400 ms are coalesced
  into ``double_esc`` **here**, because the machine's state record has no field
  for the window (OPEN-QUESTIONS §11).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from .keycodes import CODE_ESC

__all__ = ["ESC_WINDOW_MS", "EdgeDecision", "EdgeLogic", "HookSnapshot"]

ESC_WINDOW_MS: Final = 400


@dataclass(frozen=True, slots=True)
class HookSnapshot:
    """One immutable object the callback reads with a single attribute load.

    Rebinding a reference is atomic in CPython, so the hook thread sees either
    the old snapshot or the new one, never a torn mix — no lock on the hottest
    path in the process (spec §2).

    ``suppress`` is the driver's rendering of ``SetSuppression``: false whenever
    a recording cannot start, because a bound button must never mean "nothing
    happens" — Mouse 4 has to go back to being Back.
    """

    bound: frozenset[int]
    suppress: bool
    recording: bool


EventKind = Literal["press", "release", "esc", "double_esc"]


@dataclass(frozen=True, slots=True)
class EdgeDecision:
    """What the callback does with one edge: swallow it, and/or report it."""

    suppress: bool
    event: EventKind | None
    code: int


class EdgeLogic:
    """State that belongs to the *process*, not to one hook installation.

    The suppressed-press set must survive a watchdog reinstall (spec §2): if it
    were hook-instance state, a reinstall between a press and its release would
    orphan the release — the exact defect the pairing rule exists to prevent.
    Confined to the hook thread; no locking needed or wanted.
    """

    def __init__(self, *, already_down: frozenset[int] = frozenset()) -> None:
        self._suppressed_down: set[int] = set()
        self._foreign_down: set[int] = set(already_down)
        self._keys_down: set[int] = set()
        self._last_esc_ms: int | None = None

    def tracks(self, code: int) -> bool:
        """Does this code have pending edge state — a held key, a swallowed
        press, a foreign mark?

        The ctypes layer must route an edge here when the code is bound OR
        tracked: gating on the current bound set alone delivers an orphaned
        release when a binding changes mid-hold — the exact defect the pairing
        rule exists to prevent (review round 1). Hook-thread confined, like
        everything else on this class.
        """
        return (
            code in self._keys_down
            or code in self._suppressed_down
            or code in self._foreign_down
        )

    def on_edge(self, code: int, *, pressed: bool, now_ms: int, snapshot: HookSnapshot) -> EdgeDecision:
        """Decide for one edge of one interesting code (a bound code or Esc).

        Uninteresting codes never reach this method — the ctypes callback passes
        them through before doing any work, to stay far inside the 1000 ms
        budget after which Windows silently unhooks us.
        """
        if code == CODE_ESC:
            return self._on_esc(pressed=pressed, now_ms=now_ms, snapshot=snapshot)

        if code in self._foreign_down:
            # Held before we existed. Its release belongs to whoever saw the
            # press; a press while marked foreign means we missed the release,
            # so the mark is stale — drop it and treat the press normally.
            if pressed:
                self._foreign_down.discard(code)
            else:
                self._foreign_down.discard(code)
                return EdgeDecision(suppress=False, event=None, code=code)

        if pressed:
            if code in self._keys_down:
                # Auto-repeat: swallow if the original press was swallowed,
                # pass through otherwise; either way it is not a new press.
                return EdgeDecision(
                    suppress=code in self._suppressed_down, event=None, code=code
                )
            self._keys_down.add(code)
            suppress = snapshot.suppress and code in snapshot.bound
            if suppress:
                self._suppressed_down.add(code)
            event: EventKind | None = "press" if code in snapshot.bound else None
            return EdgeDecision(suppress=suppress, event=event, code=code)

        # Release: pairing outranks everything (spec §2).
        self._keys_down.discard(code)
        suppress = code in self._suppressed_down
        self._suppressed_down.discard(code)
        event = "release" if code in snapshot.bound else None
        return EdgeDecision(suppress=suppress, event=event, code=code)

    def _on_esc(self, *, pressed: bool, now_ms: int, snapshot: HookSnapshot) -> EdgeDecision:
        if not pressed:
            self._keys_down.discard(CODE_ESC)
            suppress = CODE_ESC in self._suppressed_down
            self._suppressed_down.discard(CODE_ESC)
            return EdgeDecision(suppress=suppress, event=None, code=CODE_ESC)

        if CODE_ESC in self._keys_down:  # auto-repeat of a held Esc
            return EdgeDecision(
                suppress=CODE_ESC in self._suppressed_down, event=None, code=CODE_ESC
            )
        self._keys_down.add(CODE_ESC)

        if self._last_esc_ms is not None and now_ms - self._last_esc_ms <= ESC_WINDOW_MS:
            self._last_esc_ms = None
            # The second press of a pair. Swallow it only while recording ("Esc
            # only during dictation") — the first one already reached the
            # application, which is what keeps single-Esc usable. The event is
            # emitted regardless; the machine ignores it in phases with no cell.
            suppress = snapshot.recording
            if suppress:
                self._suppressed_down.add(CODE_ESC)
            return EdgeDecision(suppress=suppress, event="double_esc", code=CODE_ESC)

        self._last_esc_ms = now_ms
        return EdgeDecision(suppress=False, event="esc", code=CODE_ESC)
