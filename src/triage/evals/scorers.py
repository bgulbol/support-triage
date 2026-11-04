"""Code-based scorers. Pure functions: no API calls, so they are unit-tested and run in CI for free."""
import math
from collections import Counter

URGENCY_LEVELS = ["low", "medium", "high"]


def score_triage(case: dict, result: dict | None) -> dict:
    """Compare one triage result with the golden case.

    expected_category is a list: some tickets have more than one acceptable category.
    A missing result (invalid output, API error) fails every field.
    """
    if result is None:
        return {"category_ok": False, "urgency_ok": False, "needs_human_ok": False, "all_ok": False}
    category_ok = result["category"] in case["expected_category"]
    urgency_ok = result["urgency"] == case["expected_urgency"]
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
        pairs[(r["expected_urgency"], predicted)] += 1
    return dict(pairs)


def flaky_cases(rows: list[dict]) -> list[str]:
    """Case ids whose (category, urgency, needs_human) differ between repeats of the same run."""
    answers: dict[str, set] = {}
    for r in rows:
        res = r["result"]
        key = (res["category"], res["urgency"], res["needs_human"]) if res else None
        answers.setdefault(r["case_id"], set()).add(key)
    return sorted(case_id for case_id, seen in answers.items() if len(seen) > 1)
