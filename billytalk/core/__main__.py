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
from .audio.devices import input_device_names, reload_portaudio, resolve_input_device
from .insert.clipboard import Clipboard
from .insert.focus import capture_target
from .insert.inserter import Inserter
from .insert.verify import InsertVerifier
from .ipc.server import IpcServer, PipeBusy, pipe_name
from .hotkeys import CHORD_COPY_VK, CHORD_PASTE_VK, HotkeyActions, HotkeyCapture
from .logging_setup import configure_logging
from .machine.driver import Driver, DriverDeps
from .machine.effects import ErrorCode
from .machine.events import Exit, HookDied
from .services import UiServices
from .store.config import save_config
from .system_events import SystemEvents
from .stt.groq import GroqProvider
from .store import CleanupGate, HistoryStore, connect, ensure_schema, load_config
from .store import secrets
from .text.dictionary import Dictionary
from .tray import HiddenWindow, TrayIcon, tray_tooltip_for
from .ui_launch import UiHost
from .wiring import TrayMenuBridge, UiMessageRouter, connect_greeting, plan_publish

log = logging.getLogger("billytalk.main")

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
    # The verifier's COM side initialises lazily on the driver thread — the
    # only thread that ever calls the insert ladder (verify.py's contract).
    inserter = Inserter(clipboard, verifier=InsertVerifier())

    def chosen_input_device() -> str | None:
        # Spec §5's ranked auto-fallback, resolved fresh at each stream open so
        # a device that came back since last time is picked up. An enumeration
        # failure falls through to the system default (None).
        return resolve_input_device(
            config.audio_input_ranking, input_device_names(),
            current=config.audio_input_device,
        )
    stt_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="billytalk-stt")

    hook_holder: list[HookThread] = []

    deps = DriverDeps(
        store=store,
        gate=gate,
        provider=provider,
        dictionary=dictionary,
        clipboard=clipboard,
        inserter=inserter,
        capture_factory=lambda **kw: CaptureSession(device=chosen_input_device(), **kw),
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
        chord_codes=frozenset({CHORD_PASTE_VK, CHORD_COPY_VK}),
    )
    driver = Driver(deps)

    # -- hotkeys (spec §9, §14): capture + Ctrl+Alt+Z/X, driver-thread jobs -- #
    def apply_ptt_binding(code: int) -> None:
        # Driver thread, inside the capture job: live for the hook the moment
        # end_capture re-syncs the snapshot (мастер шага 3: назначается само).
        config.ptt_code = code
        save_config(roaming / "config.json", config)
        deps.bound_codes = frozenset({code})

    hotkey_capture = HotkeyCapture(
        post_job=driver.post_job,
        schedule_at=driver.scheduler.at,
        now_ms=deps.now_ms,
        begin_capture=lambda: driver.set_capture_mode(True),
        end_capture=lambda: driver.set_capture_mode(False),
        apply_binding=apply_ptt_binding,
        send=lambda frame: server.send(frame),
    )
    hotkey_actions = HotkeyActions(
        post_job=driver.post_job,
        last_shown=store.last_shown,
        capture_target=capture_target,
        clipboard_write=clipboard.write,
        insert=inserter.insert,
        play_cue=play_cue,
        now_ms=deps.now_ms,
        wall_ms=deps.wall_ms,
    )
    deps.on_chord = hotkey_actions.on_chord
    deps.on_capture = hotkey_capture.on_capture_event

    # -- IPC: the channel the interface speaks over, and the single-instance
    #    gate. FILE_FLAG_FIRST_PIPE_INSTANCE makes start() fail if the name is
    #    taken, so a second core refuses **here**, before it installs the global
    #    hooks (harness §2, §3; acceptance «второй экземпляр ядра не запускается»).
    channel_name = pipe_name()

    # The logic lives in core/wiring.py where tests reach it (milestone-3 M0);
    # __main__ hands it the real collaborators. ui_host and server are created
    # a few lines below — the lambdas bind late, and nothing calls them before
    # both exist.
    bridge = TrayMenuBridge(
        ensure_ui=lambda: ui_host.ensure_running(),
        dictation_enabled=lambda: driver.state.enabled,
        post=driver.post,
        send=lambda frame: server.send(frame),
    )

    def apply_config_to_deps() -> None:
        # Driver thread (via post_job): refresh the deps copies a set_config
        # may have moved. The closures that read `config` live are already
        # current — these are the fields DriverDeps captured by value.
        deps.language = config.language
        deps.max_hold_ms = config.max_hold_ms
        deps.retention_minutes = config.retention_minutes

    def has_groq_key() -> bool:
        try:
            return secrets.read_secret(secrets.TARGET_GROQ) is not None
        except secrets.SecretUndecodable:
            return False  # presence check only; the loud path is groq_key()

    services = UiServices(
        config=config,
        config_path=roaming / "config.json",
        db_path=local / "history.db",
        post_job=driver.post_job,
        send=lambda frame: server.send(frame),
        store_get=store.get,
        clipboard_write=clipboard.write,
        insert=inserter.insert,
        last_target=lambda: driver.last_target,
        play_cue=play_cue,
        swap_dictionary=lambda d: setattr(deps, "dictionary", d),
        save_dictionary=lambda d: d.save_to_db(conn),
        current_dictionary=lambda: deps.dictionary,
        apply_config_to_deps=apply_config_to_deps,
        has_groq_key=has_groq_key,
        hotkey_capture=hotkey_capture,
    )
    router = UiMessageRouter(
        post=driver.post,
        dictation_enabled=lambda: driver.state.enabled,
        menu=bridge,
        services=services,
    )

    def on_ui_connect() -> None:
        # Read thread: driver.state is a frozen record and gate.offline a bool,
        # the same safe cross-thread reads the menu bridge makes.
        st = driver.state
        server.send(connect_greeting(
            phase_name=st.phase.name, enabled=st.enabled,
            offline=gate.offline, queue_len=len(st.queue),
        ))

    def on_ui_disconnect() -> None:
        bridge.clear()
        # Spec §14: «режим захвата хоткея... снимается при разрыве канала» —
        # a dead interface must never leave keys suppressed.
        hotkey_capture.cancel_on_disconnect()

    server = IpcServer(
        channel_name, handler=router.handle,
        on_connect=on_ui_connect,
        on_disconnect=on_ui_disconnect,
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
    window = HiddenWindow()
    window.start()
    tray: TrayIcon | None = None
    if window.wait_ready(5.0) and window.hwnd:
        tray = TrayIcon(window, menu_provider=bridge.provide, on_command=bridge.route_click)
        if not tray.add():
            log.warning("tray icon failed to add; continuing without it")
            tray = None
    else:
        log.warning("hidden window failed to start; continuing without a tray")

    # -- power, session and device messages on the hidden window (spec §3/§5) -- #
    _STREAM_PHASES = frozenset({"Initialized", "Recording"})

    def reload_devices() -> None:
        # Driver thread: refresh PortAudio's frozen list, then tell the
        # interface. The stream is provably closed — SystemEvents gated it.
        reload_portaudio()
        server.send({
            "type": "device_list_changed",
            "inputs": input_device_names(),
            "outputs": [],
        })

    system_events = SystemEvents(
        post_event=driver.post,
        post_job=driver.post_job,
        stream_open=lambda: driver.state.phase.name in _STREAM_PHASES,
        reload_devices=reload_devices,
        reinstall_hook=deps.request_hook_reinstall,
        reset_watchdog=lambda: hook_holder[0].note_probe_sent() if hook_holder else None,
    )
    if window.hwnd:
        system_events.register(window)

    def publish(state) -> None:  # driver thread; display only
        plan = plan_publish(
            phase_name=state.phase.name,
            enabled=state.enabled,
            offline=gate.offline,
            queue_len=len(state.queue),
        )
        # count_waiting() touches the store on the driver thread — the one that
        # owns the single SQLite connection — so it is safe here.
        waiting = store.count_waiting() if plan.count_waiting else 0
        if tray is not None:
            tray.set_state(plan.tray_state,
                           tooltip=tray_tooltip_for(plan.tray_state, waiting=waiting))
        if plan.raise_ui:
            ui_host.ensure_running()
        # A device change that arrived mid-recording runs its deferred reload
        # the moment the stream is closed again (spec §5).
        if state.phase.name not in _STREAM_PHASES:
            system_events.on_idle()
        server.send(plan.frame())

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
