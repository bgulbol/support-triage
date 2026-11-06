"""Quality gate for CI: fail the build when an eval metric drops below a threshold.

Usage:
    python -m triage.evals.gate --summary results/evals/ci_triage.summary.json --metric metrics.all_ok --min 0.85

A threshold, not 'no regressions at all': with 1 repeat in CI, a flaky case (like t26) can
fail by chance. The per-case compare report is printed for humans; the gate decides.
"""
import argparse
import json
import sys
from pathlib import Path


def read_metric(summary: dict, dotted: str) -> float:
    """'metrics.all_ok' -> summary['metrics']['all_ok']"""
    value = summary
    for key in dotted.split("."):
        value = value[key]
    return float(value)


def check(summary_path: Path, metric: str, minimum: float) -> bool:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    value = read_metric(summary, metric)
    passed = value >= minimum
    print(f"{'PASS' if passed else 'FAIL'}  {summary_path.name}  {metric} = {value:.1%}  (min {minimum:.0%})")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail when an eval metric is below a threshold.")
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--metric", required=True, help="Dotted path inside the summary, e.g. metrics.all_ok")
    parser.add_argument("--min", required=True, type=float, dest="minimum")
    args = parser.parse_args()
    sys.exit(0 if check(args.summary, args.metric, args.minimum) else 1)


if __name__ == "__main__":
    main()
