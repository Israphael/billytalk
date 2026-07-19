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
from .logging_setup import configure_logging
from .machine.driver import Driver, DriverDeps
from .machine.effects import ErrorCode
from .machine.events import Exit, HookDied, SetDictationEnabled
from .stt.groq import GroqProvider
from .store import CleanupGate, HistoryStore, connect, ensure_schema, load_config
from .store import secrets
from .text.dictionary import Dictionary
from .tray import HiddenWindow, TrayIcon, TrayMenuItem, tray_state_for

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

    def menu_model() -> tuple[TrayMenuItem, ...]:
        # Window thread; driver.state is a frozen record rebound atomically.
        return (
            TrayMenuItem(101, "Открыть настройки", enabled=False),  # UI: далее в цикле 2
            TrayMenuItem(),
            TrayMenuItem(CMD_TOGGLE, "Диктовка включена", checked=driver.state.enabled),
            TrayMenuItem(),
            TrayMenuItem(CMD_EXIT, "Выход"),
        )

    def on_tray_command(command: int) -> None:
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

    if tray is not None:
        tray_ref = tray

        def publish(state) -> None:  # driver thread; display only
            tray_ref.set_state(
                tray_state_for(
                    phase_name=state.phase.name,
                    enabled=state.enabled,
                    offline=gate.offline,
                    queue_len=len(state.queue),
                )
            )

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
        window.stop()
        hook.stop()
        stt_pool.shutdown(wait=False, cancel_futures=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
