"""``python -m billytalk.core`` — the cycle-1 core: no UI, console exit.

Wires the real collaborators into the driver and runs the loop on the main
thread. Until the tray exists (cycle 2), quitting is typing ``q`` + Enter in
the console (harness §9's "предусмотреть завершение по консоли"), or Ctrl+C.
"""

from __future__ import annotations

import ctypes as ct
import gc
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path
from typing import Any, Final

import win32api
import win32process

from ..i18n import set_language, t
from .audio.capture import CaptureError, CaptureSession
from .audio.cues import play_cue
from .audio.encode import encode_flac
from .audio.trim import trim_silence
from .hooks.edges import HookSnapshot
from .hooks.lowlevel import HookThread
from .hooks.watchdog import HookWatchdog
from .audio.devices import (
    default_input_device,
    hold_for_probe,
    input_device_names,
    probe_active,
    reload_portaudio,
    resolve_input_device,
)
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
from .ui_language import resolve_ui_language
from .ui_launch import UiHost
from .wiring import TrayMenuBridge, UiMessageRouter, connect_greeting, plan_publish

log = logging.getLogger("billytalk.main")

# ShutdownBlockReasonCreate holds the logoff/shutdown off while we save an
# in-flight dictation (spec §3). HWND overflows the default c_int restype, so
# argtypes are pinned — the recurring 64-bit truncation trap (research/02).
_user32 = ct.WinDLL("user32", use_last_error=True)
_user32.ShutdownBlockReasonCreate.restype = wintypes.BOOL
_user32.ShutdownBlockReasonCreate.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
_user32.ShutdownBlockReasonDestroy.restype = wintypes.BOOL
_user32.ShutdownBlockReasonDestroy.argtypes = (wintypes.HWND,)


def _block_shutdown(hwnd: int | None) -> None:
    if hwnd:
        _user32.ShutdownBlockReasonCreate(hwnd, "BillyTalk сохраняет диктовку")


def _unblock_shutdown(hwnd: int | None) -> None:
    if hwnd:
        _user32.ShutdownBlockReasonDestroy(hwnd)


_STREAM_PHASES: Final = frozenset({"Initialized", "Recording"})
"""Phases in which the capture stream is open: nothing else may touch the
microphone or reload PortAudio while the machine is in one of them."""

_QUIET_CODES: frozenset[ErrorCode] = frozenset({ErrorCode.HOOK_DEAD})
"""Recovered by itself, nothing for the user to do: the icon and the log say
it happened. A toast for every self-healed hook would train the user to
dismiss toasts without reading them, which is how the *important* ones get
missed."""

_WARNING_CODES: frozenset[ErrorCode] = frozenset({
    ErrorCode.NETWORK_DOWN, ErrorCode.RATE_LIMITED, ErrorCode.PROVIDER_ERROR,
    ErrorCode.FOCUS_LOST, ErrorCode.SECURE_FIELD, ErrorCode.CLIP_TOO_LONG,
})
"""Recoverable and already handled by us — a warning icon, not an error one."""


class Notifier:
    """Failures reach the user as a toast through the tray icon (spec §11).

    Cycle 1 printed to a console; the shipped build is ``console=False``, so
    that print goes to a handle nobody owns — the notification would simply
    not exist. The icon is created late in ``main`` (it needs the window), and
    ``DriverDeps`` is built early, so the tray is injected afterwards through
    :attr:`tray`; until then, and if the icon failed to add at all, this
    degrades to the log. That is safe by spec §11's own rule: the toast is
    never the only channel — every one of these codes already sounded a cue
    and moved the icon.

    **Thread:** usually the driver's, but not only — ``groq_key`` hands this
    to the provider, so a transcription worker reports a KEY_INVALID from the
    pool. ``Shell_NotifyIcon`` is thread-safe, and ``TrayIcon`` draws all its
    icons in ``add()`` precisely so that the path from here touches no
    lazily-built state.
    """

    def __init__(self) -> None:
        self.tray: TrayIcon | None = None

    def notify(self, code: ErrorCode) -> None:
        title = t(f"err.{code.value}.title")
        action = t(f"err.{code.value}.action")
        log.info("notify %s", code.value)  # code only: never the text (spec §13)
        if code in _QUIET_CODES or self.tray is None:
            return
        level = "warning" if code in _WARNING_CODES else "error"
        self.tray.notify(title, action, level=level)


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

    # Every string the core shows — the tooltip and the notifications — speaks
    # this language from here on; the interface is told the resolved code in
    # get_config and matches it (cycle 3, ui_language).
    set_language(resolve_ui_language(config.ui_language))
    notifier = Notifier()

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
            notifier.notify(ErrorCode.KEY_INVALID)
            return None

    provider = GroqProvider(groq_key, model=config.groq_model)
    clipboard = Clipboard()
    # The verifier's COM side initialises lazily on the driver thread — the
    # only thread that ever calls the insert ladder (verify.py's contract).
    inserter = Inserter(clipboard, verifier=InsertVerifier())

    # PortAudio's device list is frozen between reloads, so enumerating it on
    # every press (the dictation-start hot path) is redundant latency — cache
    # it and refresh only when a device change actually reloads the library
    # (M4 review, low). Driver thread touches both, so no lock.
    device_names_cache: list[str] = input_device_names()

    def chosen_input_device() -> str | None:
        # Spec §5's ranked auto-fallback against the cached list; a device that
        # came back is picked up after the next reload refreshes the cache. A
        # failed enumeration is an empty cache → the system default (None).
        return resolve_input_device(
            config.audio_input_ranking, device_names_cache,
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
        notify=notifier.notify,
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
        # Atomic against a failed write: persist first, mutate live state only
        # if it stuck — otherwise config and the hook's bound set would drift
        # apart (config.ptt_code changed, bound_codes not) and the shown key
        # would not be the acted one (M3 review). The raise propagates to the
        # capture job, which deactivates in finally regardless.
        previous = config.ptt_code
        config.ptt_code = code
        try:
            save_config(roaming / "config.json", config)
        except Exception:
            config.ptt_code = previous
            raise
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
        # The tooltip and the notifications follow the interface's language
        # setting the moment it changes, without a restart.
        set_language(resolve_ui_language(config.ui_language))

    def has_groq_key() -> bool:
        try:
            return secrets.read_secret(secrets.TARGET_GROQ) is not None
        except secrets.SecretUndecodable:
            return False  # presence check only; the loud path is groq_key()

    # -- the wizard's collaborators (spec §12) --------------------------- #

    _PROBE_MS = 700
    """Long enough for a Bluetooth headset to wake (100–300 ms, spec §5) and
    for a syllable to land in the buffer; short enough that the wizard's
    button feels like a button."""

    def probe_microphone() -> dict[str, Any]:
        """Open the real device the way a dictation would, listen briefly,
        and report what happened (wizard step 1).

        Runs on the background pool, never on the driver thread: opening a
        device blocks for as long as the device feels like.

        It refuses while a dictation is in flight. Not because the device is
        exclusive — measured on this stack (MME/Realtek) a second input stream
        opens perfectly well alongside the first — but because a probe is a
        diagnostic, and running one through somebody's recording gives the
        machine a second stream to reason about for no gain.
        """
        if driver.state.phase.name in _STREAM_PHASES:
            return {"ok": False, "code": "recording", "device": None,
                    "inputs": device_names_cache, "level": 0}
        names = input_device_names()
        if not names:
            return {"ok": False, "code": "no_device", "device": None,
                    "inputs": [], "level": 0}
        device = chosen_input_device()
        shown = device or (default_input_device() or {}).get("name") or names[0]
        # The probe opens a SECOND PortAudio stream, outside the machine's
        # phases, and a device change could otherwise unload the library from
        # under it (audio/devices.py holds that contract now).
        try:
            with hold_for_probe() as claimed:
                if not claimed:
                    # A device reload is mid-flight (or another probe). Both
                    # are short; «занято» with a retry button is honest.
                    return {"ok": False, "code": "mic_busy", "device": shown,
                            "inputs": names, "level": 0}
                return _probe_body(device, shown, names)
        finally:
            # A device change that arrived while we held PortAudio deferred
            # itself; run it now rather than leaving the list stale until the
            # next dictation publishes.
            driver.post_job(system_events.on_idle)

    def _probe_body(
        device: str | None, shown: str, names: list[str]
    ) -> dict[str, Any]:
        first_frame = threading.Event()
        session = CaptureSession(device=device, on_started=first_frame.set)
        try:
            session.start()
        except CaptureError as exc:
            return {"ok": False, "code": exc.code.value, "device": shown,
                    "inputs": names, "level": 0}
        try:
            heard = first_frame.wait(_PROBE_MS / 1000)
            samples = session.stop(tail_ms=0)
        except Exception:
            session.cancel()
            return {"ok": False, "code": "mic_busy", "device": shown,
                    "inputs": names, "level": 0}
        if not heard or samples.size == 0:
            # The stream opened and stayed mute. Measured as its own outcome
            # rather than folded into mic_denied: capture.py's docstring warns
            # that some builds answer a privacy block with silence instead of
            # an error, and a hardware mute switch looks exactly the same. The
            # wizard names both possibilities instead of guessing one.
            return {"ok": False, "code": "no_frames", "device": shown,
                    "inputs": names, "level": 0}
        peak = int(abs(samples.astype("int32")).max())
        return {"ok": True, "code": "ok", "device": shown, "inputs": names,
                "level": min(100, round(peak * 100 / 32767))}

    def write_groq_key(key: str) -> None:
        # The one place a key is written. No log line here or in the caller
        # carries the value (spec §13).
        secrets.write_secret(secrets.TARGET_GROQ, key)

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
        submit_background=lambda job: stt_pool.submit(job),
        probe_microphone=probe_microphone,
        write_groq_key=write_groq_key,
        check_groq_key=provider.check_key,
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
    # Frozen (cycle 3): the shipped BillyTalk.exe is both roles; it relaunches
    # itself with --ui. Dev checkout: the interface is a `-m billytalk.ui` child.
    if getattr(sys, "frozen", False):
        ui_argv = [sys.executable, "--ui", channel_name, core_image]
    else:
        ui_argv = [sys.executable, "-m", "billytalk.ui", channel_name, core_image]
    ui_host = UiHost(ui_argv)

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

    # A windowed build (console=False) has no stdin at all — sys.stdin is None
    # and iterating it raises inside a thread nobody watches. The console exit
    # is a dev-checkout convenience (harness §9); shipped, the tray owns it.
    if sys.stdin is not None:
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
    # From here failures reach the user as toasts, not as prints into a
    # console that a windowed build does not have.
    notifier.tray = tray

    # -- power, session and device messages on the hidden window (spec §3/§5) -- #
    def reload_devices() -> None:
        # Driver thread: refresh PortAudio's frozen list and the cache the
        # press path reads, then tell the interface. The machine's stream is
        # provably closed — SystemEvents gated it — and the lock covers the
        # one stream that gate cannot see, the microphone probe.
        nonlocal device_names_cache
        if not reload_portaudio():
            # Practically unreachable: the probe also raises probe_active(),
            # which SystemEvents' gate reads, so a change during a probe
            # defers before getting here. If it ever does happen, a stale
            # device list beats unloading the library under a live stream.
            log.warning("device reload skipped: the microphone probe holds PortAudio")
            return
        device_names_cache = input_device_names()
        server.send({
            "type": "device_list_changed",
            "inputs": device_names_cache,
            "outputs": [],
        })

    system_events = SystemEvents(
        post_event=driver.post,
        post_job=driver.post_job,
        # «Is any PortAudio stream of ours open» — the machine's, or the
        # wizard's microphone probe, which lives outside the phases entirely.
        stream_open=lambda: (
            driver.state.phase.name in _STREAM_PHASES or probe_active()
        ),
        reload_devices=reload_devices,
        reinstall_hook=deps.request_hook_reinstall,
        reset_watchdog=lambda: hook_holder[0].note_probe_sent() if hook_holder else None,
        block_shutdown=lambda: _block_shutdown(window.hwnd),
        unblock_shutdown=lambda: _unblock_shutdown(window.hwnd),
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

    # First run (spec §12): the wizard is not something to go looking for in a
    # tray menu — a fresh install has no key, and without a key the first
    # dictation can only fail. Raise the interface into the wizard right here.
    # `wizard_done` flips only when the wizard itself says so, so an install
    # that was abandoned halfway asks again next time.
    if not config.wizard_done:
        log.info("first run: raising the interface for the wizard")
        ui_host.ensure_running()

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
