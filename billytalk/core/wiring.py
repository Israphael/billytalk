"""Core↔interface wiring, factored out of ``__main__`` into testable objects.

Every confirmed finding of the cycle-2 review — the menu-click routing race,
the lying toggle check mark, and before them the latent ``TrayState.OFFLINE``
NameError — lived in ``__main__``'s closures, the one place no unit test
reaches. This module carries that logic as plain objects over injected
callables, so ``__main__`` keeps only what genuinely needs the real world:
process spawn, hooks, window and thread plumbing.

Threading contract (unchanged from the closures it replaces):

- :meth:`UiMessageRouter.handle` runs on the IPC server's read thread. It only
  posts machine events and swaps the menu bridge's model reference — an atomic
  rebind of one name, the same discipline ``ui_menu[0]`` followed.
- :meth:`TrayMenuBridge.provide` and :meth:`TrayMenuBridge.route_click` run on
  the hidden window's thread, around the blocking ``TrackPopupMenuEx``.
- :func:`plan_publish` and :func:`connect_greeting` are pure; their callers
  pick the thread (driver thread and server read thread respectively).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from .ipc.protocol import menu_command, reply, state_changed
from .machine.events import Event, Exit, SetDictationEnabled
from .tray import TrayMenuItem, TrayState, menu_items_from_wire, tray_state_for

__all__ = [
    "CMD_FALLBACK_TOGGLE",
    "CMD_FALLBACK_EXIT",
    "TrayMenuBridge",
    "UiMessageRouter",
    "PublishPlan",
    "plan_publish",
    "connect_greeting",
]

CMD_FALLBACK_TOGGLE: Final = 102
CMD_FALLBACK_EXIT: Final = 109

_ACTIVE_PHASE_NAMES: Final = frozenset(
    {"Initialized", "Recording", "Finalizing", "Delivering"}
)
"""Phases where a dictation is in flight — the trigger to have the interface up
so the plashka can show through Finalizing/Delivering (OPEN-QUESTIONS §25)."""


class TrayMenuBridge:
    """The tray menu's two-thread heart (OPEN-QUESTIONS §22).

    The interface owns the menu content and sends it over IPC; the core renders
    it and forwards clicks back. When no interface is connected, the bridge
    serves the core's own minimum — enough to toggle dictation and quit.

    A click is routed by the model that was actually *shown*, not by re-reading
    the cache: ``TrackPopupMenuEx`` blocks while the user reads, the model can
    flip underneath (the UI boots via §25's ensure-running, or dies mid-open),
    and the two command namespaces are disjoint — re-reading at click time
    dropped the click (cycle-2 review, finding 1). ``provide`` snapshots the
    origin; ``route_click`` trusts the snapshot. Both run on the window thread,
    so the snapshot needs no lock.
    """

    def __init__(
        self,
        *,
        ensure_ui: Callable[[], None],
        dictation_enabled: Callable[[], bool],
        post: Callable[[Event], None],
        send: Callable[[dict[str, Any]], bool],
    ) -> None:
        self._ensure_ui = ensure_ui
        self._dictation_enabled = dictation_enabled
        self._post = post
        self._send = send
        # Written on the server's read thread, read on the window thread — a
        # lone atomic reference, never mutated in place.
        self._model: tuple[TrayMenuItem, ...] | None = None
        self._served_from_ui = False  # window thread only

    def set_model(self, items_wire: object) -> None:
        """Read thread: the interface sent its menu. Empty or malformed falls
        back to the core's minimum rather than rendering a blank popup."""
        self._model = menu_items_from_wire(items_wire) or None

    def clear(self) -> None:
        """A dead interface falls back to the core's own menu (OQ §22)."""
        self._model = None

    def provide(self) -> tuple[TrayMenuItem, ...]:
        """Window thread, on menu open. Opening the menu is a demand that
        raises the interface too (OQ §25); once it has sent its model, that is
        what we show."""
        self._ensure_ui()
        model = self._model
        self._served_from_ui = model is not None
        if model is not None:
            return model
        return (
            TrayMenuItem(
                CMD_FALLBACK_TOGGLE,
                "Диктовка включена",
                checked=self._dictation_enabled(),
            ),
            TrayMenuItem(),
            TrayMenuItem(CMD_FALLBACK_EXIT, "Выход"),
        )

    def route_click(self, command: int) -> None:
        """Window thread, after the menu closed on a choice. When the interface
        owned the shown menu it owns the click — forward it and let the UI
        answer (toggle_dictation / shutdown / a window)."""
        if self._served_from_ui:
            self._send(menu_command(command))
            return
        if command == CMD_FALLBACK_TOGGLE:
            self._post(SetDictationEnabled(not self._dictation_enabled()))
        elif command == CMD_FALLBACK_EXIT:
            self._post(Exit())


class UiMessageRouter:
    """UI → core message routing, on the server's read thread.

    Like the tray does from the window thread, it only posts events and swaps
    the menu model — nothing here blocks or touches the store. The server has
    already rejected types outside the protocol's ``UI_TO_CORE`` set, so every
    message that arrives is legal; a legal verb this milestone does not serve
    yet is answered ``unimplemented`` when the request carried an ``id``, so
    the interface waits on a reply, never on a silence (OPEN-QUESTIONS §21).
    """

    def __init__(
        self,
        *,
        post: Callable[[Event], None],
        dictation_enabled: Callable[[], bool],
        menu: TrayMenuBridge,
    ) -> None:
        self._post = post
        self._dictation_enabled = dictation_enabled
        self._menu = menu

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        kind = message.get("type")
        if kind == "toggle_dictation":
            enabled = message.get("enabled")
            if enabled is None:
                enabled = not self._dictation_enabled()
            self._post(SetDictationEnabled(bool(enabled)))
        elif kind == "shutdown":
            self._post(Exit())
        elif kind == "menu_model":
            self._menu.set_model(message.get("items"))
        else:
            request_id = message.get("id")
            if isinstance(request_id, int):
                return reply(request_id, error="unimplemented")
        return None


@dataclass(frozen=True, slots=True)
class PublishPlan:
    """What one ``publish_state`` call must do, decided without doing it.

    The executor in ``__main__`` owns the side effects: the store query (driver
    thread — the one that owns the single SQLite connection), the tray icon,
    the UI raise, the channel send.
    """

    tray_state: TrayState
    queue_len: int
    count_waiting: bool
    """Only the offline icon shows a number (spec §3), so only offline pays
    the ``count_waiting()`` query."""
    raise_ui: bool
    """Raise the interface lazily on the first dictation so the plashka is up
    by Finalizing (OPEN-QUESTIONS §25); it stays up afterwards."""

    def frame(self) -> dict[str, Any]:
        """The ``state_changed`` push for the channel — display state only,
        never a transcript (spec §13)."""
        return state_changed(self.tray_state.value, queue_len=self.queue_len)


def plan_publish(
    *, phase_name: str, enabled: bool, offline: bool, queue_len: int
) -> PublishPlan:
    """Driver thread, from ``publish_state``: fold the machine's state into
    everything the displays need."""
    tstate = tray_state_for(
        phase_name=phase_name, enabled=enabled, offline=offline, queue_len=queue_len
    )
    return PublishPlan(
        tray_state=tstate,
        queue_len=queue_len,
        count_waiting=tstate is TrayState.OFFLINE,
        raise_ui=phase_name in _ACTIVE_PHASE_NAMES,
    )


def connect_greeting(
    *, phase_name: str, enabled: bool, offline: bool, queue_len: int
) -> dict[str, Any]:
    """The ``state_changed`` a freshly connected interface is greeted with.

    A stopped or idle core emits no ``state_changed`` on its own, so without
    the greeting the interface would sit on its enabled=True assumption
    forever — the check mark lied (cycle-2 review, finding 2). Pure fold only:
    no store touch here, the waiting count belongs to the driver thread's
    SQLite connection and is left to the next real publish.
    """
    tstate = tray_state_for(
        phase_name=phase_name, enabled=enabled, offline=offline, queue_len=queue_len
    )
    return state_changed(tstate.value, queue_len=queue_len)
