"""The dictionary (harness §8): Cyrillic word boundaries, longer rules first,
never inside a word."""

from __future__ import annotations

from billytalk.core.store.db import connect, ensure_schema
from billytalk.core.text.dictionary import DEFAULT_RULES, Dictionary, Rule


def test_cyrillic_word_boundaries() -> None:
    """The harness §13 trap: ASCII-only word boundaries silently never match
    Cyrillic. «впс» must be found as a word between Cyrillic words."""
    d = Dictionary([Rule("normalize", "впс", "VPS")])
    assert d.apply("перезапусти впс в бразилии") == "перезапусти VPS в бразилии"
    assert d.apply("впс") == "VPS"
    assert d.apply("рестарт впс, срочно") == "рестарт VPS, срочно"


def test_rule_does_not_fire_inside_a_word() -> None:
    d = Dictionary([Rule("normalize", "впс", "VPS"), Rule("replace", "прот", "прод")])
    assert d.apply("впсовый хостинг") == "впсовый хостинг"
    assert d.apply("протокол не трогать") == "протокол не трогать"
    assert d.apply("завпс") == "завпс"


def test_matching_is_case_insensitive_and_replacement_is_literal() -> None:
    d = Dictionary([Rule("normalize", "впс", "VPS")])
    assert d.apply("ВПС упал") == "VPS упал"
    assert d.apply("Впс упал") == "VPS упал"


def test_longer_rules_apply_before_shorter() -> None:
    """«впс премиум» must win over «впс» regardless of list order (spec §7)."""
    rules = [
        Rule("normalize", "впс", "VPS"),
        Rule("normalize", "впс премиум", "VPS Premium"),
    ]
    for ordering in (rules, rules[::-1]):
        d = Dictionary(ordering)
        assert d.apply("возьми впс премиум и впс") == "возьми VPS Premium и VPS"


def test_alternatives_in_a_pattern_share_one_replacement() -> None:
    d = Dictionary([Rule("normalize", "впс|вэпээс|v p s", "VPS")])
    assert d.apply("вэпээс лежит") == "VPS лежит"
    assert d.apply("подними v p s быстро") == "подними VPS быстро"


def test_replace_fixes_the_stable_mishearing() -> None:
    """research/07: no prompt fixes «прот» for «прод»; only this pass does."""
    d = Dictionary(DEFAULT_RULES)
    assert d.apply("выкати на прот") == "выкати на прод"


def test_disabled_rule_is_inert() -> None:
    d = Dictionary([Rule("replace", "прот", "прод", enabled=False)])
    assert d.apply("выкати на прот") == "выкати на прот"


def test_pattern_is_a_literal_not_a_regex() -> None:
    """Users write patterns; a stray metacharacter must match itself, not crash."""
    d = Dictionary([Rule("normalize", "c++ (новый)", "C++ (modern)")])
    assert d.apply("учу c++ (новый) сейчас") == "учу C++ (modern) сейчас"


def test_replacement_backslash_is_literal() -> None:
    d = Dictionary([Rule("normalize", "домашняя папка", r"C:\Users\Admin")])
    assert d.apply("открой домашняя папка") == r"открой C:\Users\Admin"


def test_empty_dictionary_is_identity() -> None:
    assert Dictionary([]).apply("как есть") == "как есть"


def test_prompt_terms_are_unique_right_hand_sides_in_order() -> None:
    d = Dictionary(
        [
            Rule("normalize", "впс", "VPS"),
            Rule("normalize", "вэпээс", "VPS"),
            Rule("replace", "прот", "прод"),
            Rule("normalize", "реалити", "Reality", enabled=False),
        ]
    )
    assert d.prompt_terms() == ["VPS", "прод"]


def test_round_trips_through_db_and_json() -> None:
    conn = connect(":memory:")
    ensure_schema(conn)
    original = Dictionary(
        [
            Rule("normalize", "впс", "VPS"),
            Rule("replace", "прот", "прод", enabled=False),
        ]
    )
    original.save_to_db(conn)
    assert Dictionary.from_db(conn).rules == original.rules

    as_json = original.to_json()
    assert Dictionary.from_json(as_json).rules == original.rules


def test_empty_db_yields_the_customer_seed() -> None:
    """A fresh install starts with the measured seed, not an empty dictionary."""
    conn = connect(":memory:")
    ensure_schema(conn)
    assert Dictionary.from_db(conn).rules == DEFAULT_RULES
