"""Re-score a saved eval run against the current golden labels. No API calls, costs $0.

Use it after changing labels, so a label fix and a prompt change are measured separately.

Usage:
    python -m triage.evals.rescore --source v1_baseline --name v1_relabeled
"""
import argparse
import json

from triage.evals.run_triage_eval import (GOLDEN_FILE, RESULTS_DIR, print_report, save, summarize)
from triage.evals.scorers import score_triage


def load_golden() -> dict[str, dict]:
    with GOLDEN_FILE.open(encoding="utf-8") as f:
        return {c["id"]: c for c in map(json.loads, filter(str.strip, f))}


def rescore(source: str, name: str) -> tuple[list[dict], dict]:
    golden = load_golden()
    old_summary = json.loads((RESULTS_DIR / f"{source}.summary.json").read_text(encoding="utf-8"))
    with (RESULTS_DIR / f"{source}.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    for r in rows:
        case = golden[r["case_id"]]
        r["expected_urgency"] = case["expected_urgency"]
        r["split"] = case["split"]
        r["scores"] = score_triage(case, r["result"])

    # Same answers, same config; only the labels changed. Keep a pointer to the source run.
    config = old_summary["config"] | {"name": name, "rescored_from": source}
    return rows, summarize(rows, config, old_summary["wall_s"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score a saved run with the current golden labels.")
    parser.add_argument("--source", required=True, help="Name of the saved run to re-score")
    parser.add_argument("--name", required=True, help="Name for the re-scored result files")
    args = parser.parse_args()
    rows, summary = rescore(args.source, args.name)
    print_report(rows, summary)
    save(rows, summary)


if __name__ == "__main__":
    main()
