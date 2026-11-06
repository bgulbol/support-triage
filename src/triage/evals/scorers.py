"""Code-based scorers. Pure functions: no API calls, so they are unit-tested and run in CI for free."""
import math
from collections import Counter
from itertools import combinations

URGENCY_LEVELS = ["low", "medium", "high"]


def as_list(value) -> list:
    """Golden fields can be one value or a list of acceptable values."""
    return value if isinstance(value, list) else [value]


def primary(value):
    """The first acceptable value: the 'main' expected label, used for the confusion matrix."""
    return as_list(value)[0]


def score_triage(case: dict, result: dict | None) -> dict:
    """Compare one triage result with the golden case.

    expected_category and expected_urgency are lists: some tickets have more than one
    acceptable answer. A missing result (invalid output, API error) fails every field.
    """
    if result is None:
        return {"category_ok": False, "urgency_ok": False, "needs_human_ok": False, "all_ok": False}
    category_ok = result["category"] in as_list(case["expected_category"])
    urgency_ok = result["urgency"] in as_list(case["expected_urgency"])
    needs_human_ok = result["needs_human"] == case["expected_needs_human"]
    return {
        "category_ok": category_ok,
        "urgency_ok": urgency_ok,
        "needs_human_ok": needs_human_ok,
        "all_ok": category_ok and urgency_ok and needs_human_ok,
    }


def accuracy(rows: list[dict], key: str) -> float:
    """Share of rows where scores[key] is True. Empty input returns 0.0 instead of dividing by zero."""
    if not rows:
        return 0.0
    return sum(1 for r in rows if r["scores"][key]) / len(rows)


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100). Enough for a few dozen latency samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    # ceil, not round(): Python rounds 2.5 to 2 (banker's rounding), which breaks nearest-rank.
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def urgency_confusion(rows: list[dict]) -> dict[tuple[str, str], int]:
    """Count (expected, predicted) urgency pairs. Rows without a result count as predicted 'none'."""
    pairs = Counter()
    for r in rows:
        predicted = r["result"]["urgency"] if r["result"] else "none"
        pairs[(primary(r["expected_urgency"]), predicted)] += 1
    return dict(pairs)


def flaky_cases(rows: list[dict]) -> list[str]:
    """Case ids whose (category, urgency, needs_human) differ between repeats of the same run."""
    answers: dict[str, set] = {}
    for r in rows:
        res = r["result"]
        key = (res["category"], res["urgency"], res["needs_human"]) if res else None
        answers.setdefault(r["case_id"], set()).add(key)
    return sorted(case_id for case_id, seen in answers.items() if len(seen) > 1)


def agreement_report(human: list[str], judge: list[str | None]) -> dict:
    """Compare pass/fail labels from a human and a judge.

    agreement: share of identical labels. kappa (Cohen's): agreement corrected for chance.
    If both say 'pass' 90% of the time, they agree ~82% by luck alone; kappa removes that.
    A judge error (None) counts as a disagreement.
    """
    n = len(human)
    if n == 0:
        return {"n": 0, "agreement": 0.0, "kappa": 0.0, "human_pass_rate": 0.0, "judge_pass_rate": 0.0,
                "false_pass": 0, "false_fail": 0, "errors": 0}
    p_o = sum(h == j for h, j in zip(human, judge)) / n
    h_pass = sum(h == "pass" for h in human) / n
    j_pass = sum(j == "pass" for j in judge) / n
    j_fail = sum(j == "fail" for j in judge) / n
    p_e = h_pass * j_pass + (1 - h_pass) * j_fail
    kappa = (p_o - p_e) / (1 - p_e) if p_e < 1 else 1.0
    return {
        "n": n,
        "agreement": round(p_o, 3),
        "kappa": round(kappa, 3),
        "human_pass_rate": round(h_pass, 3),
        "judge_pass_rate": round(j_pass, 3),
        # The dangerous error: the judge lets a bad reply through.
        "false_pass": sum(h == "fail" and j == "pass" for h, j in zip(human, judge)),
        "false_fail": sum(h == "pass" and j == "fail" for h, j in zip(human, judge)),
        "errors": sum(j is None for j in judge),
    }


def _norm_value(v):
    """Make equal answers compare equal: 29700.0 == 29700, 0.3333333 ~ 0.33, text trimmed."""
    if isinstance(v, float):
        v = round(v, 2)
        return int(v) if v.is_integer() else v
    if isinstance(v, str):
        return v.strip()
    return v


def _norm_row(row) -> tuple:
    """Column order is ignored: (code, label) and (label, code) are the same answer."""
    return tuple(sorted((_norm_value(v) for v in row), key=lambda x: (type(x).__name__, str(x))))


def results_match(gold: list[tuple], pred: list[tuple], ordered: bool = False, order_col: int | None = None,
                  allow_extra_columns: bool = False) -> bool:
    """Execution accuracy: do the two queries return the same rows?

    - Unordered questions: same rows in any order (a multiset comparison).
    - Ordered questions: same rows in the same order. Rows that tie on the gold sort column
      (order_col) may come in any order among themselves, because SQL leaves that undefined.
    Column names are ignored. The number of columns must match, unless allow_extra_columns:
    then the prediction passes if some of its columns, taken together, match the gold result
    (e.g. the right company plus a count nobody asked for). Missing columns always fail.
    """
    if allow_extra_columns and gold and pred and len(pred[0]) > len(gold[0]):
        return any(results_match(gold, [tuple(r[i] for i in cols) for r in pred], ordered, order_col)
                   for cols in combinations(range(len(pred[0])), len(gold[0])))
    gold_n, pred_n = [_norm_row(r) for r in gold], [_norm_row(r) for r in pred]
    if len(gold_n) != len(pred_n):
        return False
    if not ordered:
        return Counter(gold_n) == Counter(pred_n)
    if order_col is None:
        return gold_n == pred_n
    i = 0
    while i < len(gold):  # walk the gold rows group by group of equal sort keys
        j = i
        while j < len(gold) and gold[j][order_col] == gold[i][order_col]:
            j += 1
        if Counter(gold_n[i:j]) != Counter(pred_n[i:j]):
            return False
        i = j
    return True


def normalize_sql(sql: str) -> str:
    """For the exact-match baseline: lower case, single spaces, no trailing semicolon."""
    return " ".join(sql.lower().replace(";", " ").split())
