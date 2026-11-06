"""Compare two eval runs case by case: what got fixed, what broke (regressions), what stayed.

A case 'passes' when it is fully correct (all_ok) in every repeat. A case that passed in the
baseline and does not pass in the candidate is a regression, even if the total score went up.

Usage:
    python -m triage.evals.compare --baseline v1_relabeled --candidate v2_dev
"""
import argparse
import json
import sys

from triage.evals.run_triage_eval import RESULTS_DIR


def load_run(name: str) -> tuple[dict, dict[str, list[bool]]]:
    summary = json.loads((RESULTS_DIR / f"{name}.summary.json").read_text(encoding="utf-8"))
    per_case: dict[str, list[bool]] = {}
    with (RESULTS_DIR / f"{name}.jsonl").open(encoding="utf-8") as f:
        for row in map(json.loads, filter(str.strip, f)):
            per_case.setdefault(row["case_id"], []).append(row["scores"]["all_ok"])
    return summary, per_case


def pass_rate(results: list[bool]) -> str:
    return f"{sum(results)}/{len(results)}"


def compare(baseline: str, candidate: str) -> int:
    base_summary, base = load_run(baseline)
    cand_summary, cand = load_run(candidate)
    common = sorted(set(base) & set(cand))  # e.g. a dev-only candidate vs an all-cases baseline

    fixed, broken, still_failing, flaky = [], [], [], []
    for case_id in common:
        b, c = all(base[case_id]), all(cand[case_id])
        if not b and c:
            fixed.append(case_id)
        elif b and not c:
            broken.append(case_id)
        elif not b and not c:
            still_failing.append(case_id)
        if 0 < sum(cand[case_id]) < len(cand[case_id]):
            flaky.append(case_id)

    def rate(per_case: dict) -> float:
        cells = [ok for case_id in common for ok in per_case[case_id]]
        return sum(cells) / len(cells)

    bc, cc = base_summary["config"], cand_summary["config"]
    print(f"baseline : {baseline:16} {bc['triage']} (hash {bc['prompt_hash']})")
    print(f"candidate: {candidate:16} {cc['triage']} (hash {cc['prompt_hash']})")
    print(f"common cases: {len(common)}\n")
    print(f"all_ok on common cases: {rate(base):.0%} -> {rate(cand):.0%}")
    print(f"cases passing every repeat: {sum(all(base[c]) for c in common)} -> {sum(all(cand[c]) for c in common)}"
          f" of {len(common)}\n")
    print(f"fixed        ({len(fixed)}): {', '.join(f'{c} {pass_rate(base[c])}->{pass_rate(cand[c])}' for c in fixed) or '-'}")
    print(f"REGRESSIONS  ({len(broken)}): {', '.join(f'{c} {pass_rate(base[c])}->{pass_rate(cand[c])}' for c in broken) or '-'}")
    print(f"still failing({len(still_failing)}): {', '.join(f'{c} {pass_rate(cand[c])}' for c in still_failing) or '-'}")
    print(f"flaky in candidate ({len(flaky)}): {', '.join(flaky) or '-'}")
    print(f"\ncost per row: ${base_summary['cost_usd'] / base_summary['n_rows']:.6f} -> "
          f"${cand_summary['cost_usd'] / cand_summary['n_rows']:.6f}   "
          f"p50 latency: {base_summary['latency_ms']['p50']} -> {cand_summary['latency_ms']['p50']} ms")
    # Exit code 1 on any regression, so CI can block a prompt change (like a failing test).
    return 1 if broken else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two eval runs case by case.")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args()
    sys.exit(compare(args.baseline, args.candidate))


if __name__ == "__main__":
    main()
