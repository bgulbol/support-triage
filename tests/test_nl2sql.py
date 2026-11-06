"""NL2SQL safety and scoring tests. No API calls."""
import json
import sqlite3

import pytest

from triage.evals.scorers import results_match
from triage.nl2sql.db import build_db, is_safe_select, run_readonly
from triage.nl2sql.run_nl2sql_eval import GOLDEN_FILE


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return build_db(tmp_path_factory.mktemp("db") / "licenses.db")


def test_every_gold_query_runs_and_returns_rows(db):
    with GOLDEN_FILE.open(encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    assert len(cases) == 30
    for case in cases:
        if case["answerable"]:
            assert run_readonly(case["gold_sql"], db), case["id"]


@pytest.mark.parametrize("sql", [
    "DELETE FROM licenses",
    "SELECT 1; DROP TABLE licenses",
    "UPDATE licenses SET is_active = 1",
    "PRAGMA table_info(licenses)",
    "",
])
def test_unsafe_sql_is_rejected(sql):
    assert not is_safe_select(sql)


def test_select_and_with_are_allowed():
    assert is_safe_select("SELECT COUNT(*) FROM licenses;")
    assert is_safe_select("WITH a AS (SELECT 1) SELECT * FROM a")


def test_readonly_connection_blocks_writes_even_without_the_filter(db):
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM licenses")
    conn.close()


def test_results_match_ignores_row_order_column_order_and_float_format():
    assert results_match([("a", 1), ("b", 2)], [(2, "b"), (1, "a")])
    assert results_match([(29700.0,)], [(29700,)])
    assert not results_match([("a",)], [("a",), ("b",)])


def test_ordered_results_allow_ties_to_swap_but_not_the_order():
    gold = [("x", "2026-11-15"), ("y", "2026-11-25"), ("z", "2026-11-25")]
    assert results_match(gold, [("x", "2026-11-15"), ("z", "2026-11-25"), ("y", "2026-11-25")], True, 1)
    assert not results_match(gold, [("y", "2026-11-25"), ("x", "2026-11-15"), ("z", "2026-11-25")], True, 1)
    assert not results_match([("a",), ("b",)], [("b",), ("a",)], True)


def test_extra_columns_pass_only_in_lenient_mode():
    gold = [("Anadolu Solar A.Ş.",)]
    pred = [("Anadolu Solar A.Ş.", 3)]
    assert not results_match(gold, pred)
    assert results_match(gold, pred, allow_extra_columns=True)


def test_missing_columns_fail_even_in_lenient_mode():
    gold = [("K2N4-7TQA", "Anadolu Solar A.Ş.", "2026-11-25")]
    pred = [("Anadolu Solar A.Ş.", "2026-11-25")]
    assert not results_match(gold, pred, allow_extra_columns=True)


def test_lenient_mode_keeps_the_order_check():
    gold = [("a",), ("b",)]
    assert not results_match(gold, [("b", 1), ("a", 2)], ordered=True, allow_extra_columns=True)
    assert results_match(gold, [("a", 1), ("b", 2)], ordered=True, allow_extra_columns=True)
