"""English strings. Same key set as ``ru.py`` — the test suite compares the two
tables, so drift fails a test rather than surfacing as a Russian label inside an
English window."""

from __future__ import annotations

from typing import Final

STRINGS: Final[dict[str, str]] = {
    # ------------------------------------------------------------------ #
    # common
    # ------------------------------------------------------------------ #
    "app.name": "BillyTalk",
    "common.back": "Back",
    "common.next": "Next",
    "common.skip": "Skip",
    "common.done": "Done",
    "common.close": "Close",
    "common.cancel": "Cancel",
    "common.retry": "Try again",
    "common.change": "Change",
    "common.dash": "—",
    "common.system_default": "System default",

    # ------------------------------------------------------------------ #
    # tray (core)
    # ------------------------------------------------------------------ #
    "tray.idle": "BillyTalk — ready",
    "tray.recording": "BillyTalk — recording",
    "tray.transcribing": "BillyTalk — transcribing",
    "tray.queue": "BillyTalk — dictations queued",
    "tray.offline": "BillyTalk — offline, {waiting} dictations waiting",
    "tray.stopped": "BillyTalk — dictation off",
    "tray.error": "BillyTalk — error",

    # tray menu (interface)
    "menu.settings": "Settings",
    "menu.history": "History",
    "menu.toggle": "Dictation enabled",
    "menu.exit": "Exit",

    # ------------------------------------------------------------------ #
    # notifications: what happened plus the action to take (harness §7)
    # ------------------------------------------------------------------ #
    "err.mic_denied.title": "No microphone access",
    "err.mic_denied.action": "Open Settings → Privacy → Microphone",
    "err.mic_busy.title": "Microphone unavailable",
    "err.mic_busy.action": "Pick another recording device in settings",
    "err.no_api_key.title": "A Groq key is needed",
    "err.no_api_key.action": "Open settings and save the key — the recording is kept",
    "err.key_invalid.title": "Groq rejected the key",
    "err.key_invalid.action": "Replace the key in settings",
    "err.rate_limited.title": "Rate limited",
    "err.rate_limited.action": "Wait — the recordings are queued and will go by themselves",
    "err.network_down.title": "No connection",
    "err.network_down.action": "Recordings are saved; they transcribe once you are online",
    "err.provider_error.title": "Transcription service is down",
    "err.provider_error.action": "We retry by ourselves; nothing is lost",
    "err.paste_failed.title": "Paste failed",
    "err.paste_failed.action": "The text is on the clipboard — Ctrl+V or Ctrl+Alt+Z",
    "err.focus_lost.title": "The window lost focus",
    "err.focus_lost.action": "Text is on the clipboard — click the field and press Ctrl+Alt+Z",
    "err.secure_field.title": "Password field",
    "err.secure_field.action": "We do not type there — paste it yourself from the clipboard",
    "err.hook_dead.title": "Input hook reinstalled",
    "err.hook_dead.action": "The dictation button works again",
    "err.clip_too_long.title": "Recording too long",
    "err.clip_too_long.action": "The limit is 20 minutes; keep it shorter",
    "err.audio_unreadable.title": "Audio could not be read",
    "err.audio_unreadable.action": "The recording is gone or damaged — dictate it again",

    # ------------------------------------------------------------------ #
    # settings window
    # ------------------------------------------------------------------ #
    "settings.title": "BillyTalk — settings",
    "settings.section.general": "General",
    "settings.section.bindings": "Bindings",
    "settings.section.mic": "Microphone",
    "settings.section.stt": "Transcription",
    "settings.section.dictionary": "Dictionary",
    "settings.section.about": "About",

    "settings.autostart": "Start when I sign in to Windows",
    "settings.autostart.hint": "Starts the core; the icon appears in the tray",
    "settings.autostart.disabled_by_windows":
        "Turned off in Windows Settings → Apps → Startup",
    "settings.autostart.unavailable": "Unavailable: not running from the installed folder",
    "settings.autostart.failed": "Windows refused to change the startup entry",
    "settings.ui_language": "Program language",
    "settings.ui_language.hint": "Windows and notifications; does not affect dictation",
    "settings.ui_language.auto": "Same as Windows",
    "settings.plashka": "Badge while recording",
    "settings.plashka.hint": "Can be turned off once you have used Ctrl+Alt+Z at least once",

    "settings.binding.ptt": "Dictation — hold",
    "settings.binding.fallback": "Dictation — fallback",
    "settings.binding.toggle": "Dictation — toggle",
    "settings.binding.paste": "Paste the last one",
    "settings.binding.copy": "Copy the last one",
    "settings.binding.window": "Main window",
    "binding.code": "Key {code}",
    "settings.binding.fixed": "Fixed combination in MVP-0",
    "settings.binding.note":
        "Cancel with a double Esc while recording. A single Esc always reaches the app.",

    "settings.mic.device": "Recording device",
    "settings.mic.hint": "The list refreshes when devices are plugged in or out",
    "settings.mic.check": "Test",
    "settings.mic.ranked":
        "If the chosen device is not there, BillyTalk falls back to the system "
        "default by itself — an unplugged headset does not cost you a dictation.",
    "settings.mic.checking": "Testing…",

    "settings.language": "Dictation language",
    "settings.language.hint": "Set explicitly, not detected",
    "settings.key": "API key",
    "settings.key.hint": "Kept in the Windows Credential Manager, never written to a file",
    "settings.key.saved": "Saved",
    "settings.key.missing": "Not saved",
    "settings.key.replace": "Replace…",
    "settings.polish": "Text polishing",
    "settings.polish.hint": "Separate key; both versions of the text are stored",
    "settings.model": "Model",
    "settings.model.hint": "Groq cloud, your own key",

    "settings.rule.type": "Type",
    "settings.rule.heard": "Heard as",
    "settings.rule.written": "Written as",
    "settings.rule.enabled": "On",
    "settings.rule.add": "Add rule",
    "settings.rule.edit": "Edit",
    "settings.rule.delete": "Delete",
    "settings.rule.toggle": "On/off",
    "settings.rule.dialog": "Dictionary rule",
    "settings.rule.heard_field": "Heard as (variants separated by |)",
    "settings.rule.enabled_field": "Enabled",
    "settings.rule.incomplete": "Fill in “Heard as” and “Written as”.",
    "settings.rule.type.normalize": "spelling",
    "settings.rule.type.replace": "replacement",

    "settings.about.version": "BillyTalk {version}",
    "settings.about.repo": "github.com/Israphael/billytalk",
    "settings.about.logs": "Log",
    "settings.about.logs.hint": "Dictated text and keystrokes never reach the log",
    "settings.about.logs.open": "Open the log folder",
    "settings.about.data": "Data",
    "settings.about.data.hint": "History and audio on this computer",
    "settings.about.data.clear": "Clear history…",
    "clear.title": "Clear history and audio",
    "clear.body":
        "This deletes PERMANENTLY: every transcript, every history record and "
        "every audio file on this computer. There is no undo.",
    "clear.stays":
        "Kept: settings, the dictionary, and the Groq key in the Credential Manager.",
    "clear.confirm": "Delete everything",
    "clear.busy": "A dictation is in flight — wait for it to finish and try again.",
    "clear.failed": "The history could not be cleared.",
    "clear.done": "Deleted: {rows} records, {files} audio files.",
    "settings.about.data.soon": "Arrives together with its confirmation window",
    "settings.about.wizard": "First-run wizard",
    "settings.about.wizard.hint": "Microphone, button, key and a live test — step by step",
    "settings.about.wizard.run": "Run again",

    "settings.rejected": "The core rejected the change",
    "settings.rule.rejected": "Rule rejected — the list was restored",

    "language.ru": "Russian",
    "language.en": "English",

    # ------------------------------------------------------------------ #
    # history window
    # ------------------------------------------------------------------ #
    "history.title": "BillyTalk — history",
    "history.search.hint": "Search the history…",
    "history.find": "Find",
    "history.export": "Export…",
    "history.insert": "Insert",
    "history.copy": "Copy",
    "history.column.time": "Time",
    "history.column.text": "Text",
    "history.column.app": "App",
    "history.column.status": "Status",
    "history.filter.all": "All statuses",
    "history.filter.delivered": "Inserted",
    "history.filter.clipboard": "On the clipboard",
    "history.filter.waiting": "Waiting for a connection",
    "history.filter.other": "Other",
    "history.footer.page": "{shown} shown · {total} records · text is kept forever",
    "history.footer.found": "Found: {shown} (first {page})",
    "history.footer.select": "Select a record",
    "history.footer.search_failed": "The search did not answer — try again",
    "history.footer.insert_failed": "The insert failed — the text can still be copied",
    "history.footer.copied": "Copied — press Ctrl+V.",
    "history.footer.clipboard_busy": "Another app owns the clipboard — try again",
    "history.footer.export_failed": "Export failed — check the path and try again",
    "history.footer.exported": "Records exported: {rows}",
    "history.export.dialog": "Export the history",
    "history.export.file": "billytalk-history",
    "history.export.wildcard": "Text (*.txt)|*.txt|CSV (*.csv)|*.csv|JSON (*.json)|*.json",

    "status.inserted": "Inserted",
    "status.left_on_clipboard": "Clipboard · Ctrl+V",
    "status.withheld": "Not inserted",
    "status.focus_lost": "Clipboard · focus lost",
    "status.verify_impossible": "Unconfirmed",
    "status.blocked_secure": "Password field",
    "status.pending_transcribe": "Transcribing…",
    "status.pending_retry": "Waiting for a connection",
    "status.transcribe_failed": "Transcription error",
    "status.cancelled": "Cancelled",
    "status.too_short": "Too short",
    "status.empty": "Empty",

    "outcome.inserted": "Inserted.",
    "outcome.verify_impossible":
        "Sent; no confirmation — the text is on the clipboard too (Ctrl+V).",
    "outcome.left_on_clipboard": "The text is on the clipboard — press Ctrl+V.",
    "outcome.focus_lost": "The window lost focus — the text is on the clipboard, press Ctrl+V.",
    "outcome.blocked_secure": "Password field: paste it yourself from the clipboard.",

    # ------------------------------------------------------------------ #
    # hotkey capture
    # ------------------------------------------------------------------ #
    "capture.title": "BillyTalk — capture a button",
    "capture.prompt": "Press the mouse button or key you want for dictation",
    "capture.hint": "Esc cancels. The window closes by itself after 30 seconds.\n"
                    "Side mouse buttons work best.",

    # ------------------------------------------------------------------ #
    # first-run wizard (spec §12)
    # ------------------------------------------------------------------ #
    "wizard.title": "BillyTalk — first run",
    "wizard.step_of": "Step {step} of {total}",
    "wizard.finish": "Finish",
    "wizard.later": "Finish later",
    "wizard.reopen_hint": "You can run the wizard again from settings → About.",

    "wizard.mic.title": "Microphone",
    "wizard.mic.body":
        "Let us check that BillyTalk can hear you. The test takes a second and records "
        "nothing.",
    "wizard.mic.check": "Test the microphone",
    "wizard.mic.ok": "The microphone works: {device}",
    "wizard.mic.denied":
        "Windows is blocking microphone access. Open the privacy settings and allow it "
        "for desktop apps.",
    "wizard.mic.busy":
        "The device is busy or unavailable. Close the app holding the microphone and "
        "test again.",
    "wizard.mic.none": "Windows sees no microphone at all.",
    "wizard.mic.silent":
        "The microphone opened but no sound arrived: check the hardware mute switch on "
        "the headset and the input level in Windows.",
    "wizard.mic.level": "Level: {level}%",
    "wizard.mic.settings": "Open microphone settings",

    "wizard.language.title": "Language",
    "wizard.language.body":
        "The dictation language is set explicitly: auto-detection confuses similar "
        "words more often than it helps.",
    "wizard.language.dictation": "Dictation language",
    "wizard.language.ui": "Program language",

    "wizard.hotkey.title": "The dictation button",
    "wizard.hotkey.body":
        "It works like this: hold the button, speak, release. The text appears wherever "
        "the caret was.",
    "wizard.hotkey.current": "Currently: {key}",
    "wizard.hotkey.change": "Assign another",
    "wizard.hotkey.note":
        "A side mouse button works best: it sits under your thumb and almost no program "
        "needs it.",

    "wizard.driver.title": "“Back” on the side button",
    "wizard.driver.body":
        "The side button usually means “Back”. While BillyTalk runs it suppresses that "
        "— but there is no suppression if the program crashed, if an elevated window is "
        "in front, or during the brief gap while the hook is reinstalled.",
    "wizard.driver.why":
        "What that means in practice: the browser can go “Back” and lose a filled-in "
        "form. Unbind “Back” from the button in your mouse software (Razer Synapse, "
        "Logitech G HUB, Bloody, Mouse Properties) and any such glitch becomes harmless.",
    "wizard.driver.how":
        "How: open the mouse software → profile → button assignments → set the side "
        "button to “No action” or “Mouse button 4”.",
    "wizard.driver.done": "“Back” is unbound",
    "wizard.driver.optional": "This step is optional — you can leave things as they are.",

    "wizard.stt.title": "Transcription",
    "wizard.stt.warning":
        "BillyTalk transcribes speech in the cloud. Without the internet, or on a weak "
        "connection, dictation will not work: recordings are kept and transcribed once "
        "you are online, but you will not get the text at that moment.",
    "wizard.stt.body":
        "The MVP-0 provider is Groq (whisper-large-v3-turbo), on your own key. "
        "Offline local transcription arrives in 1.0.",

    "wizard.key.title": "Groq key",
    "wizard.key.body":
        "The key is free and needs no card. Open console.groq.com, sign in, create an "
        "API key and paste it here.",
    "wizard.key.open": "Open console.groq.com",
    "wizard.key.field": "Key",
    "wizard.key.save": "Save and check",
    "wizard.key.stored": "A key is already saved. Paste a new one to replace it.",
    "wizard.key.checking": "Checking the key…",
    "wizard.key.ok": "The key was accepted.",
    "wizard.key.invalid": "Groq rejected the key. Check that you copied all of it.",
    "wizard.key.network": "No connection — the key is saved but could not be checked now.",
    "wizard.key.empty": "Paste the key into the field.",
    "wizard.key.failed": "The key could not be saved to the Credential Manager.",
    "wizard.key.privacy":
        "Kept in the Windows Credential Manager. It never reaches a file, the history "
        "or the log.",

    "wizard.test.title": "Live test",
    "wizard.test.body":
        "Open any text field — Notepad, a chat, a search box. Hold {key}, say a "
        "sentence, release.",
    "wizard.test.waiting": "Waiting for your first dictation…",
    "wizard.test.got": "Done. Last dictation: “{text}”",
    "wizard.test.status": "State: {status}",
    "wizard.test.tray":
        "Tip: pin the icon next to the clock — click “^” by the tray and drag the "
        "BillyTalk icon out. The icon shows recording, transcription and the queue.",
    "wizard.test.autostart": "Start BillyTalk when I sign in to Windows",

    "wizard.done.title": "All set",
    "wizard.done.body":
        "BillyTalk lives in the tray. Hold the button to dictate, Ctrl+Alt+Z pastes the "
        "last one, Ctrl+Alt+X copies it.",
    "wizard.done.nokey":
        "No key saved: recordings will pile up and transcribe as soon as a key appears "
        "in settings.",
}
