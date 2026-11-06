"""Unit tests for the code-based scorers. No API calls: these run in CI for free."""
from triage.evals.scorers import agreement_report, accuracy, flaky_cases, percentile, score_triage, urgency_confusion

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


def test_urgency_accepts_a_list_and_a_plain_string():
    case = {"expected_category": ["billing"], "expected_urgency": ["medium", "high"], "expected_needs_human": True}
    assert score_triage(case, result(category="billing", urgency="high"))["urgency_ok"] is True
    assert score_triage(case, result(category="billing", urgency="low"))["urgency_ok"] is False
    assert score_triage(CASE | {"expected_urgency": "high"}, result())["urgency_ok"] is True


def test_confusion_uses_the_primary_expected_label():
    rows = [row("a", result(urgency="high"), expected_urgency=["medium", "high"])]
    assert urgency_confusion(rows) == {("medium", "high"): 1}


def test_agreement_report_counts_and_kappa():
    human = ["pass", "pass", "fail", "fail"]
    judge = ["pass", "fail", "fail", "pass"]
    report = agreement_report(human, judge)
    assert report["agreement"] == 0.5
    assert report["kappa"] == 0.0          # 50% agreement is exactly what chance gives here
    assert report["false_pass"] == 1 and report["false_fail"] == 1


def test_agreement_report_perfect_and_judge_error():
    assert agreement_report(["pass", "fail"], ["pass", "fail"])["kappa"] == 1.0
    report = agreement_report(["pass", "fail"], ["pass", None])
    assert report["errors"] == 1 and report["agreement"] == 0.5
