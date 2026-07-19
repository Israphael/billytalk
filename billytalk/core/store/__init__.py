"""Storage: schema, history, configuration and secrets (harness §4, §5).

Split by what can hurt you:

* ``db``      — the schema itself and how a connection is opened. The DDL here is
                the single source of truth; the copy in harness §4 documents it.
* ``history`` — every query the core runs, plus the cleanup policy whose whole
                point is *not* running (spec §3: never while offline).
* ``config``  — ``config.json``, written atomically, tolerant of corruption.
* ``secrets`` — Windows Credential Manager. API keys never touch the config file,
                the database, the logs or the repository.
"""

from .config import Config, ConfigTooNew, LoadedConfig, load_config, save_config
from .db import DDL, SCHEMA_VERSION, SchemaTooNew, connect, ensure_schema
from .history import CleanupGate, HistoryStore

__all__ = [
    "Config",
    "ConfigTooNew",
    "LoadedConfig",
    "load_config",
    "save_config",
    "DDL",
    "SCHEMA_VERSION",
    "SchemaTooNew",
    "connect",
    "ensure_schema",
    "CleanupGate",
    "HistoryStore",
]
