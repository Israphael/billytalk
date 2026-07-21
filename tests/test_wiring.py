"""core/wiring.py: the ``__main__`` logic that milestone-3 M0 made testable.

Every scenario here is one the cycle-2 review (or the latent NameError before
it) proved can rot invisibly inside ``__main__``'s closures: the menu-click
routing race, the lying toggle check mark on connect, the publish fold. The
wiring objects take plain callables, so the fakes are lists.
"""

from __future__ import annotations

from typing import Any

from billytalk.core.machine.events import Exit, SetDictationEnabled
from billytalk.core.tray import TrayState
from billytalk.core.wiring import (
    CMD_FALLBACK_EXIT,
    CMD_FALLBACK_TOGGLE,
    TrayMenuBridge,
    UiMessageRouter,
    connect_greeting,
    plan_publish,
)


class Harness:
    """A bridge plus the recorders behind its callables."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.posted: list[Any] = []
        self.sent: list[dict[str, Any]] = []
        self.ui_raises = 0
        self.enabled = enabled
        self.bridge = TrayMenuBridge(
            ensure_ui=self._ensure_ui,
            dictation_enabled=lambda: self.enabled,
            post=self.posted.append,
            send=self._send,
        )
        self.router = UiMessageRouter(
            post=self.posted.append,
            dictation_enabled=lambda: self.enabled,
            menu=self.bridge,
        )

    def _ensure_ui(self) -> None:
        self.ui_raises += 1

    def _send(self, frame: dict[str, Any]) -> bool:
        self.sent.append(frame)
        return True


_UI_MENU_WIRE = [
    {"command": 201, "label": "Открыть настройки"},
    {"command": 0, "label": ""},
    {"command": 209, "label": "Выход"},
]


# --------------------------------------------------------------------------- #
# UiMessageRouter
# --------------------------------------------------------------------------- #

def test_router_toggle_with_explicit_enabled_posts_it() -> None:
    h = Harness()
    assert h.router.handle({"type": "toggle_dictation", "enabled": False}) is None
    assert h.posted == [SetDictationEnabled(False)]


def test_router_toggle_without_enabled_flips_the_current_state() -> None:
    h = Harness(enabled=False)
    h.router.handle({"type": "toggle_dictation"})
    assert h.posted == [SetDictationEnabled(True)]


def test_router_shutdown_posts_exit() -> None:
    h = Harness()
    h.router.handle({"type": "shutdown"})
    assert h.posted == [Exit()]


def test_router_menu_model_feeds_the_bridge() -> None:
    h = Harness()
    h.router.handle({"type": "menu_model", "items": _UI_MENU_WIRE})
    items = h.bridge.provide()
    assert [i.command for i in items] == [201, 0, 209]


def test_router_answers_unimplemented_verbs_instead_of_silence() -> None:
    """A legal verb of a later milestone (harness §3 lists it, nothing serves
    it yet) must answer the reply the request asked for — a UI awaiting an
    ``id`` may never hang on a silent core (OPEN-QUESTIONS §21)."""
    h = Harness()
    answer = h.router.handle({"type": "history_search", "id": 7, "query": "x"})
    assert answer == {"type": "reply", "id": 7, "error": "unimplemented"}
    assert h.posted == []


def test_router_unimplemented_verb_without_id_stays_silent() -> None:
    h = Harness()
    assert h.router.handle({"type": "capture_hotkey_stop"}) is None
    assert h.sent == []


# --------------------------------------------------------------------------- #
# TrayMenuBridge: what the menu shows
# --------------------------------------------------------------------------- #

def test_fallback_menu_toggles_and_quits_and_follows_enabled() -> None:
    h = Harness(enabled=True)
    items = h.bridge.provide()
    assert [i.command for i in items] == [CMD_FALLBACK_TOGGLE, 0, CMD_FALLBACK_EXIT]
    assert items[0].checked is True
    assert items[1].is_separator
    h.enabled = False
    assert h.bridge.provide()[0].checked is False, "the check mark must be read live"


def test_menu_open_always_raises_the_interface() -> None:
    """Opening the menu is a demand that raises the UI (OPEN-QUESTIONS §25) —
    whether the model came over IPC or the core serves its minimum."""
    h = Harness()
    h.bridge.provide()
    h.bridge.set_model(_UI_MENU_WIRE)
    h.bridge.provide()
    assert h.ui_raises == 2


def test_empty_or_malformed_model_falls_back_to_the_minimum() -> None:
    h = Harness()
    h.bridge.set_model([])
    assert h.bridge.provide()[0].command == CMD_FALLBACK_TOGGLE
    h.bridge.set_model("not a list")
    assert h.bridge.provide()[0].command == CMD_FALLBACK_TOGGLE


def test_disconnect_clears_the_ui_model() -> None:
    h = Harness()
    h.bridge.set_model(_UI_MENU_WIRE)
    h.bridge.clear()  # the server's on_disconnect
    assert h.bridge.provide()[0].command == CMD_FALLBACK_TOGGLE


# --------------------------------------------------------------------------- #
# TrayMenuBridge: click routing (cycle-2 review, finding 1)
# --------------------------------------------------------------------------- #

def test_click_on_a_ui_served_menu_is_forwarded() -> None:
    h = Harness()
    h.bridge.set_model(_UI_MENU_WIRE)
    h.bridge.provide()
    h.bridge.route_click(209)
    assert h.sent == [{"type": "menu_command", "command": 209}]
    assert h.posted == []


def test_click_routes_by_the_shown_menu_when_the_model_arrives_late() -> None:
    """THE race of review finding 1: the fallback menu is open (blocking
    ``TrackPopupMenuEx``), the freshly booted UI sends its model meanwhile,
    the user clicks «Выход» = 109. Re-reading the cache would look 109 up in
    the UI's namespace and drop the click; the snapshot must route it locally."""
    h = Harness()
    h.bridge.provide()  # fallback served
    h.bridge.set_model(_UI_MENU_WIRE)  # arrives under the open menu
    h.bridge.route_click(CMD_FALLBACK_EXIT)
    assert h.posted == [Exit()]
    assert h.sent == []


def test_click_routes_to_the_ui_even_if_it_died_under_the_open_menu() -> None:
    """The mirror race: the UI's menu is shown, the UI dies (model cleared),
    the click must still be forwarded — send() reports undelivered, the click
    is consciously dropped rather than misread as fallback 102/109."""
    h = Harness()
    h.bridge.set_model(_UI_MENU_WIRE)
    h.bridge.provide()
    h.bridge.clear()
    h.bridge.route_click(209)
    assert h.sent == [{"type": "menu_command", "command": 209}]
    assert h.posted == []


def test_fallback_clicks_toggle_and_exit() -> None:
    h = Harness(enabled=True)
    h.bridge.provide()
    h.bridge.route_click(CMD_FALLBACK_TOGGLE)
    assert h.posted == [SetDictationEnabled(False)]
    h.bridge.route_click(CMD_FALLBACK_EXIT)
    assert h.posted == [SetDictationEnabled(False), Exit()]


def test_fallback_ignores_a_command_it_never_offered() -> None:
    h = Harness()
    h.bridge.provide()
    h.bridge.route_click(203)  # a UI-namespace command against the fallback menu
    assert h.posted == []
    assert h.sent == []


# --------------------------------------------------------------------------- #
# plan_publish / connect_greeting
# --------------------------------------------------------------------------- #

def test_publish_plan_active_phase_raises_ui_and_reports_transcribing() -> None:
    plan = plan_publish(phase_name="Finalizing", enabled=True, offline=False, queue_len=1)
    assert plan.raise_ui is True
    assert plan.tray_state is TrayState.TRANSCRIBING
    assert plan.count_waiting is False
    assert plan.frame() == {"type": "state_changed", "state": "transcribing", "queue_len": 1}


def test_publish_plan_idle_neither_raises_nor_counts() -> None:
    plan = plan_publish(phase_name="Idle", enabled=True, offline=False, queue_len=0)
    assert plan.raise_ui is False
    assert plan.count_waiting is False
    assert plan.tray_state is TrayState.IDLE


def test_publish_plan_only_offline_pays_the_waiting_query() -> None:
    plan = plan_publish(phase_name="Idle", enabled=True, offline=True, queue_len=0)
    assert plan.tray_state is TrayState.OFFLINE
    assert plan.count_waiting is True
    assert plan.raise_ui is False


def test_publish_plan_disabled_wins_over_offline() -> None:
    plan = plan_publish(phase_name="Idle", enabled=False, offline=True, queue_len=0)
    assert plan.tray_state is TrayState.STOPPED
    assert plan.count_waiting is False


def test_connect_greeting_tells_a_fresh_ui_the_stopped_truth() -> None:
    """Review finding 2: a stopped core emits no state_changed on its own, so
    the greeting is the only thing keeping a fresh UI's check mark honest."""
    frame = connect_greeting(phase_name="Idle", enabled=False, offline=False, queue_len=0)
    assert frame == {"type": "state_changed", "state": "stopped", "queue_len": 0}


def test_connect_greeting_carries_the_live_phase() -> None:
    frame = connect_greeting(phase_name="Recording", enabled=True, offline=False, queue_len=2)
    assert frame == {"type": "state_changed", "state": "recording", "queue_len": 2}
