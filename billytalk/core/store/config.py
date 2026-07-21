"""``config.json`` (harness §5): atomic, tolerant of corruption, afraid of the future.

Three rules, verbatim from the harness:

* no file → create from defaults;
* unparseable → rename to ``config.corrupt-{ts}.json``, start with defaults,
  tell the user — never guess at half a file;
* ``schema_version`` newer than ours → **refuse to start.** A downgraded process
  rewriting a future config would silently destroy settings the newer version
  cared about.

Secrets are never here (spec §13) — they live in the Credential Manager, see
``secrets.py``.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "Config",
    "ConfigTooNew",
    "LoadedConfig",
    "load_config",
    "save_config",
]

CONFIG_SCHEMA_VERSION: Final = 1

# Key codes in the unified space of spec §2: mouse offset by 0x1000, so Mouse 4 is
# 0x1000 + 3 = 4099.
DEFAULT_PTT_CODE: Final = 4099


@dataclass
class Config:
    """Everything the user can change, with the defaults of spec §2 and §3.

    ``usage`` exists now (spec §14) so that mode 2's word counter does not need a
    config migration later. Unknown keys in the file are dropped on load; missing
    keys take these defaults — both directions of version drift stay harmless
    within the same schema_version.
    """

    schema_version: int = CONFIG_SCHEMA_VERSION
    language: str = "ru"
    ptt_code: int = DEFAULT_PTT_CODE
    retention_minutes: int = 60
    max_hold_ms: int = 5 * 60 * 1000
    max_clip_ms: int = 20 * 60 * 1000
    audio_input_device: str | None = None
    audio_input_ranking: list[str] = field(default_factory=list)
    """Spec §5's ranked microphone list with auto-fallback: names in priority
    order, matched against what PortAudio actually reports. Empty means «system
    default». Additive within schema_version 1 — an old config without the key
    loads with this default, harmless in both drift directions."""
    provider_id: str = "groq"
    groq_model: str = "whisper-large-v3-turbo"
    polish_enabled: bool = False
    press_enter_after: bool = False
    audio_cap_rows: int = 500
    audio_cap_bytes: int = 2 * 1024**3
    usage: dict[str, int] = field(default_factory=lambda: {"words_this_week": 0})


class ConfigTooNew(RuntimeError):
    """The config was written by a newer BillyTalk. Refuse to run (harness §5)."""

    def __init__(self, found: int) -> None:
        super().__init__(
            f"config.json has schema_version {found}, this build understands "
            f"{CONFIG_SCHEMA_VERSION}"
        )
        self.found = found


@dataclass(frozen=True)
class LoadedConfig:
    """What ``load_config`` did, so the caller can tell the user (harness §5)."""

    config: Config
    created: bool = False
    corrupt_backup: Path | None = None


def load_config(path: Path, *, now_ms: int) -> LoadedConfig:
    """Read the config, healing what can be healed and refusing what cannot.

    ``now_ms`` names the corrupt-file backup; a parameter, not a clock read, for
    the same testability rule as everywhere else.
    """
    if not path.exists():
        config = Config()
        save_config(path, config)
        return LoadedConfig(config, created=True)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config root must be an object")
        version = raw.get("schema_version", CONFIG_SCHEMA_VERSION)
        if not isinstance(version, int) or isinstance(version, bool):
            # "2" or 2.0 would silently bypass the newer-version gate below;
            # a config whose version field cannot be trusted is a corrupt
            # config, handled as such — kept for inspection, not guessed at.
            raise ValueError("schema_version must be an integer")
    except (ValueError, OSError):
        backup = path.with_name(f"config.corrupt-{now_ms}.json")
        os.replace(path, backup)
        config = Config()
        save_config(path, config)
        return LoadedConfig(config, corrupt_backup=backup)

    if version > CONFIG_SCHEMA_VERSION:
        raise ConfigTooNew(version)

    known = {f.name for f in dataclasses.fields(Config)}
    kwargs: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
    return LoadedConfig(Config(**kwargs))


def save_config(path: Path, config: Config) -> None:
    """Write atomically (harness §5): temp file in the same directory, then
    ``os.replace``. A crash mid-write leaves either the old file or the new one,
    never a half-file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(
        json.dumps(dataclasses.asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)
