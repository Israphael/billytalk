"""``python -m billytalk.core`` — the cycle-1 core: no UI, console exit.

Wires the real collaborators into the driver and runs the loop on the main
thread. Until the tray exists (cycle 2), quitting is typing ``q`` + Enter in
the console (harness §9's "предусмотреть завершение по консоли"), or Ctrl+C.
"""

from __future__ import annotations

import gc
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import win32api
import win32process

from .audio.capture import CaptureSession
from .audio.cues import play_cue
from .audio.encode import encode_flac
from .audio.trim import trim_silence
from .hooks.edges import HookSnapshot
from .hooks.lowlevel import HookThread
from .hooks.watchdog import HookWatchdog
from .insert.clipboard import Clipboard
from .insert.focus import capture_target
from .insert.inserter import Inserter
from .ipc.protocol import menu_command, state_changed
from .ipc.server import IpcServer, PipeBusy, pipe_name
from .logging_setup import configure_logging
from .machine.driver import Driver, DriverDeps
from .machine.effects import ErrorCode
from .machine.events import Exit, HookDied, SetDictationEnabled
from .stt.groq import GroqProvider
from .store import CleanupGate, HistoryStore, connect, ensure_schema, load_config
from .store import secrets
from .text.dictionary import Dictionary
from .tray import (
    HiddenWindow,
    TrayIcon,
    TrayMenuItem,
    TrayState,
    menu_items_from_wire,
    tray_state_for,
    tray_tooltip_for,
)
from .ui_launch import UiHost

log = logging.getLogger("billytalk.main")

_ACTIVE_PHASE_NAMES = frozenset({"Initialized", "Recording", "Finalizing", "Delivering"})
"""Phases where a dictation is in flight — the trigger to have the interface up
so the plashka can show through Finalizing/Delivering (OPEN-QUESTIONS §25)."""

_ACTION_HINTS: dict[ErrorCode, str] = {
    ErrorCode.MIC_DENIED: "откройте Параметры → Конфиденциальность → Микрофон",
    ErrorCode.MIC_BUSY: "выберите другое устройство записи",
    ErrorCode.NO_API_KEY: "сохраните ключ Groq (диспетчер учётных данных, BillyTalk/groq-api-key)",
    ErrorCode.KEY_INVALID: "замените ключ Groq",
    ErrorCode.RATE_LIMITED: "лимит запросов — подождите",
    ErrorCode.NETWORK_DOWN: "нет связи; повторим сами, записи ждут",
    ErrorCode.PROVIDER_ERROR: "сервис недоступен; повторим сами",
    ErrorCode.PASTE_FAILED: "вставка не удалась — Ctrl+Alt+Z вставит последнее",
    ErrorCode.FOCUS_LOST: "окно ушло из фокуса — текст в буфере, Ctrl+Alt+Z",
    ErrorCode.SECURE_FIELD: "поле пароля: вставьте вручную из буфера",
    ErrorCode.HOOK_DEAD: "перехват ввода переустановлен",
    ErrorCode.CLIP_TOO_LONG: "клип длиннее 20 минут — говорите короче",
}


def _console_notify(code: ErrorCode) -> None:
    """Cycle-1 stand-in for toasts: the error code and its ready action
    (harness §7: every failure message carries an action, never a statement)."""
    hint = _ACTION_HINTS.get(code, "")
    print(f"[billytalk] {code.value}: {hint}", flush=True)


def main() -> int:
    local = Path(os.environ["LOCALAPPDATA"]) / "BillyTalk"
    roaming = Path(os.environ["APPDATA"]) / "BillyTalk"
    audio_dir = local / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # configure_logging also performs spec §13's startup duty: it resets
    # http.client debuglevel to 0 — the one output channel that bypasses
    # logging entirely — before any connection can exist.
    configure_logging(local / "logs")

    loaded = load_config(roaming / "config.json", now_ms=int(time.time() * 1000))
    config = loaded.config
    if loaded.corrupt_backup is not None:
        print(f"[billytalk] конфиг был повреждён; сохранён как {loaded.corrupt_backup.name}", flush=True)

    conn = connect(local / "history.db")
    ensure_schema(conn)
    store = HistoryStore(conn)
    gate = CleanupGate()
    dictionary = Dictionary.from_db(conn)

    removed = store.sweep_orphan_audio(audio_dir)
    if removed:
        log.info("startup sweep removed %d orphan audio files", len(removed))

    def groq_key() -> str | None:
        try:
            return secrets.read_secret(secrets.TARGET_GROQ)
        except secrets.SecretUndecodable:
            # A key another tool wrote in the wrong encoding behaves like no
            # key: the user re-saves it. Anything else would crash a worker.
            _console_notify(ErrorCode.KEY_INVALID)
            return None

    provider = GroqProvider(groq_key, model=config.groq_model)
    clipboard = Clipboard()
    inserter = Inserter(clipboard)
    stt_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="billytalk-stt")

    hook_holder: list[HookThread] = []

    deps = DriverDeps(
        store=store,
        gate=gate,
        provider=provider,
        dictionary=dictionary,
        clipboard=clipboard,
        inserter=inserter,
        capture_factory=lambda **kw: CaptureSession(device=config.audio_input_device, **kw),
        capture_target=capture_target,
        play_cue=play_cue,
        notify=_console_notify,
        trim=trim_silence,
        encode=encode_flac,
        audio_dir=audio_dir,
        set_hook_snapshot=lambda snap: hook_holder[0].set_snapshot(snap) if hook_holder else None,
        request_hook_reinstall=lambda: hook_holder[0].request_reinstall() if hook_holder else None,
        submit_transcription=lambda job: stt_pool.submit(job),
        language=config.language,
        max_hold_ms=config.max_hold_ms,
        retention_minutes=config.retention_minutes,
        audio_cap_rows=config.audio_cap_rows,
        audio_cap_bytes=config.audio_cap_bytes,
        bound_codes=frozenset({config.ptt_code}),
    )
    driver = Driver(deps)

    # -- IPC: the channel the interface speaks over, and the single-instance
    #    gate. FILE_FLAG_FIRST_PIPE_INSTANCE makes start() fail if the name is
    #    taken, so a second core refuses **here**, before it installs the global
    #    hooks (harness §2, §3; acceptance «второй экземпляр ядра не запускается»).
    channel_name = pipe_name()

    # The interface fills the tray menu over IPC (OPEN-QUESTIONS §22); this holds
    # the model it sent, or None to fall back to the core's own minimum. Written
    # on the server read thread, read on the window thread — a lone atomic ref.
    ui_menu: list[tuple[TrayMenuItem, ...] | None] = [None]

    def ipc_handler(message):
        """UI → core. Fire-and-forget for milestone 2; the config/history/hotkey
        verbs are milestone 3. Runs on the server's read thread — it only posts
        events, the same as the tray does from the window thread."""
        kind = message.get("type")
        if kind == "toggle_dictation":
            enabled = message.get("enabled")
            if enabled is None:
                enabled = not driver.state.enabled
            driver.post(SetDictationEnabled(bool(enabled)))
        elif kind == "shutdown":
            driver.post(Exit())
        elif kind == "menu_model":
            items = menu_items_from_wire(message.get("items"))
            ui_menu[0] = items or None
        return None

    def on_ui_connect():
        # Push the current display state to a freshly connected interface so its
        # menu check mark and plashka are right at once — a stopped/idle core
        # emits no state_changed on its own, so the interface would otherwise
        # sit on its enabled=True assumption forever (cycle-2 review, finding 2).
        # Read thread: driver.state is a frozen record and gate.offline a bool,
        # the same safe cross-thread reads menu_model makes. No store touch —
        # count_waiting belongs to the driver thread's sqlite connection, so the
        # offline count is left to the next real publish.
        st = driver.state
        tstate = tray_state_for(
            phase_name=st.phase.name, enabled=st.enabled,
            offline=gate.offline, queue_len=len(st.queue),
        )
        server.send(state_changed(tstate.value, queue_len=len(st.queue)))

    def on_ui_disconnect():
        # A dead interface falls back to the core's own menu (OQ §22). This is
        # also where hotkey capture releases in milestone 3 (spec §14).
        ui_menu[0] = None

    server = IpcServer(
        channel_name, handler=ipc_handler,
        on_connect=on_ui_connect, on_disconnect=on_ui_disconnect,
    )
    try:
        server.start()
    except PipeBusy:
        print("[billytalk] ядро уже запущено", flush=True)
        return 4

    # The interface verifies this exact image before it trusts the channel
    # (ui/ipc/client). GetModuleFileNameEx, not sys.executable: under a uv venv
    # the trampoline and the real image differ (harness §13).
    core_image = win32process.GetModuleFileNameEx(win32api.GetCurrentProcess(), 0)
    # cycle 3: the frozen build launches itself with a flag, not `-m`.
    ui_host = UiHost([sys.executable, "-m", "billytalk.ui", channel_name, core_image])

    waiting = driver.enqueue_startup_pending()
    if waiting:
        print(f"[billytalk] {waiting} записей ждут расшифровки", flush=True)

    stt_pool.submit(provider.warm)  # pre-pay the TLS handshake off the start path

    hook = HookThread(
        driver.on_hook_event,
        HookSnapshot(bound=frozenset({config.ptt_code}), suppress=True, recording=False),
    )
    hook_holder.append(hook)
    hook.start()
    if not hook.wait_ready(5.0) or hook.install_failed:
        print(f"[billytalk] хук не установился: {hook.install_failed}", flush=True)
        return 3

    watchdog = HookWatchdog(hook)

    def probe() -> None:
        driver.scheduler.at(deps.now_ms() + 2_000, probe)
        if not watchdog.probe():
            log.warning("hook confirmed dead; reinstalling")
            driver.post(HookDied())

    driver.scheduler.at(deps.now_ms() + 2_000, probe)

    def stdin_watcher() -> None:
        for line in sys.stdin:
            if line.strip().lower() in ("q", "quit", "exit"):
                driver.post(Exit())
                return

    threading.Thread(target=stdin_watcher, name="billytalk-stdin", daemon=True).start()

    # -- tray (spec §11; OPEN-QUESTIONS §22: the icon lives in the core) --- #
    CMD_TOGGLE, CMD_EXIT = 102, 109
    # Which model the open menu was built from, snapshotted when it is served so
    # a click routes the way the shown menu expects. TrackPopupMenuEx blocks
    # while the user reads, and ui_menu[0] can flip under it (the UI boots via
    # §25's ensure_running, or dies mid-open) — and the two command namespaces
    # (fallback 102/109 vs UI 201–209) are disjoint, so re-reading the cache at
    # click time drops the click (cycle-2 review, finding 1). Window thread only,
    # so a plain cell is race-free.
    served_from_ui = [False]

    def menu_model() -> tuple[TrayMenuItem, ...]:
        # Window thread. Opening the menu is a demand that raises the interface
        # too (§25); once it has sent its model, that is what we show.
        ui_host.ensure_running()
        ui = ui_menu[0]
        served_from_ui[0] = ui is not None
        if ui is not None:
            return ui
        # Dead-interface minimum (OQ §22): enough to toggle and quit. driver.state
        # is a frozen record rebound atomically, safe to read from this thread.
        return (
            TrayMenuItem(CMD_TOGGLE, "Диктовка включена", checked=driver.state.enabled),
            TrayMenuItem(),
            TrayMenuItem(CMD_EXIT, "Выход"),
        )

    def on_tray_command(command: int) -> None:
        # Route by the model that was actually shown, not by re-reading the cache
        # (which may have flipped during the blocking menu). When the interface
        # owns the menu it owns the click — forward it and let the UI answer
        # (toggle_dictation / shutdown / a window).
        if served_from_ui[0]:
            server.send(menu_command(command))
            return
        if command == CMD_TOGGLE:
            driver.post(SetDictationEnabled(not driver.state.enabled))
        elif command == CMD_EXIT:
            driver.post(Exit())

    window = HiddenWindow()
    window.start()
    tray: TrayIcon | None = None
    if window.wait_ready(5.0) and window.hwnd:
        tray = TrayIcon(window, menu_provider=menu_model, on_command=on_tray_command)
        if not tray.add():
            log.warning("tray icon failed to add; continuing without it")
            tray = None
    else:
        log.warning("hidden window failed to start; continuing without a tray")

    def publish(state) -> None:  # driver thread; display only
        tstate = tray_state_for(
            phase_name=state.phase.name,
            enabled=state.enabled,
            offline=gate.offline,
            queue_len=len(state.queue),
        )
        # count_waiting() touches the store on the driver thread — the one that
        # owns the single SQLite connection — so it is safe here. Only the
        # offline icon shows a number (spec §3), so only offline pays the query.
        waiting = store.count_waiting() if tstate is TrayState.OFFLINE else 0
        if tray is not None:
            tray.set_state(tstate, tooltip=tray_tooltip_for(tstate, waiting=waiting))
        # Raise the interface lazily on the first dictation so the plashka is up
        # by Finalizing (OPEN-QUESTIONS §25); it stays up afterwards.
        if state.phase.name in _ACTIVE_PHASE_NAMES:
            ui_host.ensure_running()
        server.send(state_changed(tstate.value, queue_len=len(state.queue)))

    deps.publish_state = publish

    # Everything long-lived exists; move it out of GC's reach so a collection
    # pass never runs inside the hook callback's budget (spec §2, ADR-0004).
    gc.freeze()

    print("[billytalk] ядро запущено: зажмите Mouse 4 и говорите; q + Enter — выход", flush=True)
    try:
        driver.run()
    except KeyboardInterrupt:
        driver.dispatch(Exit())
    finally:
        if tray is not None:
            tray.remove()
        server.stop()      # drop the channel; the interface exits itself on it
        ui_host.stop()     # backstop a hung interface
        window.stop()
        hook.stop()
        stt_pool.shutdown(wait=False, cancel_futures=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
