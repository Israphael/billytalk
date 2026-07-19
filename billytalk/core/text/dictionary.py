"""Deterministic replacement after transcription, before polishing (spec §7).

Two rule types with one mechanism: ``normalize`` unifies spellings the
transcriber already gets right (``впс`` → ``VPS``), ``replace`` fixes what it
reliably gets wrong (``прот`` → ``прод`` — measured in research/07: no prompt
fixes an ordinary-word mishearing, only a deterministic pass does).

Matching rules, all from spec §7:

* case-insensitive;
* on word boundaries, Unicode-aware — a rule never fires inside a word
  (``впсовый`` stays whole). Lookarounds rather than ``\\b`` because a pattern
  may begin or end with a character ``\\b`` considers a non-word;
* longer rules before shorter, so ``впс премиум`` wins over ``впс``;
* patterns are literals with ``|`` alternatives, never regexes — users write
  them, and a stray ``(`` must not be a crash.

The starting table is the customer's data, not an invented glossary — redaction
1 shipped seven terms that appear zero times in 2,445 real transcripts. What the
transcripts actually contain: VPS in several spellings, VPN likewise, and the
stable mishearing «прот» for «прод». Case-insensitivity collapses most spelling
variants, so the seed stays short.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Final, Literal

__all__ = ["DEFAULT_RULES", "Dictionary", "Rule"]


@dataclass(frozen=True, slots=True)
class Rule:
    """One row of the ``dictionary`` table (harness §4)."""

    type: Literal["normalize", "replace"]
    pat: str
    repl: str
    enabled: bool = True

    def alternatives(self) -> list[str]:
        """The literal spellings this rule matches, longest first."""
        return sorted((alt.strip() for alt in self.pat.split("|") if alt.strip()),
                      key=len, reverse=True)


DEFAULT_RULES: Final[tuple[Rule, ...]] = (
    Rule("normalize", "впс", "VPS"),
    Rule("normalize", "впн", "VPN"),
    Rule("replace", "прот", "прод"),
)
"""Seeded from the customer's 2,445 transcripts (spec §7), nothing invented."""


class Dictionary:
    """A compiled rule set. Build once, apply per transcript."""

    def __init__(self, rules: tuple[Rule, ...] | list[Rule] = DEFAULT_RULES) -> None:
        self.rules: tuple[Rule, ...] = tuple(rules)
        # "Longer before shorter" (spec §7) holds across ALTERNATIVES globally,
        # not across rules: ordering whole rules by their longest alternative
        # lets a rule's short alternative fire before another rule's longer one
        # ("x" beating "x y") and eat its match. Flattened, every spelling
        # competes at its own length. Compiled once — apply() runs per dictation.
        flattened: list[tuple[str, str]] = [
            (alt, rule.repl)
            for rule in self.rules
            if rule.enabled
            for alt in rule.alternatives()
        ]
        flattened.sort(key=lambda pair: len(pair[0]), reverse=True)
        self._compiled: list[tuple[re.Pattern[str], str]] = [
            # (?<!\w)…(?!\w) rather than \b: correct even when the pattern
            # starts or ends with a character \b classes as non-word, and
            # explicit UNICODE per the harness §13 trap list.
            (
                re.compile(rf"(?<!\w){re.escape(alt)}(?!\w)", re.IGNORECASE | re.UNICODE),
                repl,
            )
            for alt, repl in flattened
        ]

    def apply(self, text: str) -> str:
        """Replace every match of every enabled rule, longer rules first.

        The replacement is passed as a callable so ``re.sub`` applies no escape
        semantics at all: users write these strings, and a ``\\`` in one must
        mean a backslash, not a group reference."""
        for pattern, repl in self._compiled:
            text = pattern.sub(lambda _m, _r=repl: _r, text)
        return text

    def prompt_terms(self) -> list[str]:
        """Unique right-hand sides, for the transcription prompt (spec §6).

        Proper names benefit from the prompt (research/07: «реалити» → Reality
        only with it); the prompt builder in ``stt/`` puts these into a normal
        sentence — never a bare comma list, which measurably breaks punctuation.
        """
        seen: dict[str, None] = {}
        for rule in self.rules:
            if rule.enabled and rule.repl not in seen:
                seen[rule.repl] = None
        return list(seen)

    # ------------------------------------------------------------------ #
    # storage (harness §4: the dictionary table; export is JSON)
    # ------------------------------------------------------------------ #

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> Dictionary:
        rows = conn.execute(
            "SELECT type, pat, repl, enabled FROM dictionary ORDER BY id"
        ).fetchall()
        if not rows:
            return cls(DEFAULT_RULES)
        return cls([Rule(r[0], r[1], r[2], bool(r[3])) for r in rows])

    def save_to_db(self, conn: sqlite3.Connection) -> None:
        with conn:
            conn.execute("DELETE FROM dictionary")
            conn.executemany(
                "INSERT INTO dictionary (type, pat, repl, enabled) VALUES (?, ?, ?, ?)",
                [(r.type, r.pat, r.repl, int(r.enabled)) for r in self.rules],
            )

    def to_json(self) -> str:
        return json.dumps(
            [
                {"type": r.type, "pat": r.pat, "repl": r.repl, "enabled": r.enabled}
                for r in self.rules
            ],
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def from_json(cls, payload: str) -> Dictionary:
        raw = json.loads(payload)
        return cls(
            [
                Rule(
                    item["type"],
                    item["pat"],
                    item["repl"],
                    bool(item.get("enabled", True)),
                )
                for item in raw
            ]
        )
