"""Unit tests for the code-based scorers. No API calls: these run in CI for free."""
from triage.evals.scorers import accuracy, flaky_cases, percentile, score_triage, urgency_confusion

CASE = {"expected_category": ["calculation", "billing"], "expected_urgency": "high", "expected_needs_human": True}


def result(category="calculation", urgency="high", needs_human=True):
    return {"category": category, "urgency": urgency, "needs_human": needs_human}


def row(case_id, res, expected_urgency="high", all_ok=True):
    return {"case_id": case_id, "result": res, "expected_urgency": expected_urgency, "scores": {"all_ok": all_ok}}


def test_all_fields_correct():
    assert score_triage(CASE, result())["all_ok"] is True


def test_any_listed_category_is_accepted():
    assert score_triage(CASE, result(category="billing"))["category_ok"] is True


def test_wrong_urgency_fails_all_ok_only_on_that_field():
    scores = score_triage(CASE, result(urgency="medium"))
    assert scores == {"category_ok": True, "urgency_ok": False, "needs_human_ok": True, "all_ok": False}


def test_missing_result_fails_everything():
    assert not any(score_triage(CASE, None).values())


def test_accuracy_and_empty_input():
    rows = [row("a", result()), row("b", result(), all_ok=False)]
    assert accuracy(rows, "all_ok") == 0.5
    assert accuracy([], "all_ok") == 0.0


def test_percentile_nearest_rank():
    values = [100, 200, 300, 400, 5000]
    assert percentile(values, 50) == 300
    assert percentile(values, 95) == 5000
    assert percentile([], 50) == 0.0


def test_urgency_confusion_counts_missing_as_none():
    rows = [row("a", result(urgency="low")), row("b", None)]
    assert urgency_confusion(rows) == {("high", "low"): 1, ("high", "none"): 1}


def test_flaky_cases_detects_changed_answers():
    rows = [row("a", result()), row("a", result(urgency="low")), row("b", result()), row("b", result())]
    assert flaky_cases(rows) == ["a"]
