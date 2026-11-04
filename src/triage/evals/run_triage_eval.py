"""Run the triage golden set against one prompt version and save scored results.

Usage:
    python -m triage.evals.run_triage_eval --name v1_baseline
    python -m triage.evals.run_triage_eval --name v2 --triage triage_v2 --split dev --reps 3

Every run writes two files to results/evals/:
    <name>.jsonl         one scored row per (case, repeat)
    <name>.summary.json  config + metrics, the input for regression comparisons
"""
import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from anthropic import transform_schema
from pydantic import ValidationError

from triage.client import MODEL, cost_usd, get_client, text_of
from triage.evals.scorers import (URGENCY_LEVELS, accuracy, flaky_cases, percentile,
                                  score_triage, urgency_confusion)
from triage.schema import TriageResult

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_FILE = ROOT / "data" / "golden" / "triage.jsonl"
PROMPTS_DIR = ROOT / "prompts"
RESULTS_DIR = ROOT / "results" / "evals"

EFFORT = "low"
MAX_TOKENS = 1024
METRICS = ["category_ok", "urgency_ok", "needs_human_ok", "all_ok"]


def load_cases(split: str) -> list[dict]:
    with GOLDEN_FILE.open(encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    return cases if split == "all" else [c for c in cases if c["split"] == split]


def build_system(handbook: str, triage: str) -> tuple[list[dict], str]:
    """System prompt as one cached block. Returns the block list and a short hash of the text."""
    text = (PROMPTS_DIR / f"{handbook}.md").read_text(encoding="utf-8") + "\n\n" + \
           (PROMPTS_DIR / f"{triage}.md").read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}], prompt_hash


def run_case(client, system: list[dict], schema: dict, case: dict, rep: int) -> dict:
    """One API call for one golden case. Never raises: errors become a failed row."""
    start = time.perf_counter()
    row = {"case_id": case["id"], "rep": rep, "split": case["split"], "lang": case["lang"],
           "tags": case["tags"], "expected_urgency": case["expected_urgency"], "result": None, "error": ""}
    try:
        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": case["text"]}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        )
        u = response.usage
        row.update({
            "input_tokens": u.input_tokens,
            "cache_write_tokens": u.cache_creation_input_tokens or 0,
            "cache_read_tokens": u.cache_read_input_tokens or 0,
            "output_tokens": u.output_tokens,
            "cost_usd": cost_usd(u),
            "stop_reason": response.stop_reason,
        })
        row["result"] = TriageResult.model_validate_json(text_of(response)).model_dump()
    except ValidationError as e:
        row["error"] = f"schema: {e.errors()[0]['msg']}"
    except Exception as e:  # API errors, timeouts: record and keep going
        row["error"] = f"{type(e).__name__}: {e}"
    row["latency_ms"] = round((time.perf_counter() - start) * 1000)
    row["scores"] = score_triage(case, row["result"])
    return row


def run(args) -> tuple[list[dict], dict]:
    client = get_client().with_options(timeout=60.0)
    cases = load_cases(args.split)
    system, prompt_hash = build_system(args.handbook, args.triage)
    schema = transform_schema(TriageResult)
    jobs = [(case, rep) for rep in range(1, args.reps + 1) for case in cases]

    wall_start = time.perf_counter()
    # The first call runs alone: it writes the cache (and compiles the schema grammar),
    # so the parallel calls after it can read the cached prefix.
    rows = [run_case(client, system, schema, *jobs[0])]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows += list(pool.map(lambda job: run_case(client, system, schema, *job), jobs[1:]))
    wall_s = time.perf_counter() - wall_start

    config = {"name": args.name, "model": MODEL, "effort": EFFORT, "handbook": args.handbook,
              "triage": args.triage, "prompt_hash": prompt_hash, "split": args.split, "reps": args.reps,
              "cases": len(cases), "date": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return rows, summarize(rows, config, wall_s)


def summarize(rows: list[dict], config: dict, wall_s: float) -> dict:
    ok_rows = [r for r in rows if "cost_usd" in r]
    latencies = [r["latency_ms"] for r in ok_rows]
    by_split = {}
    for split in sorted({r["split"] for r in rows}):
        group = [r for r in rows if r["split"] == split]
        by_split[split] = {m: round(accuracy(group, m), 3) for m in METRICS} | {"n": len(group)}
    return {
        "config": config,
        "n_rows": len(rows),
        "errors": sum(1 for r in rows if r["error"]),
        "metrics": {m: round(accuracy(rows, m), 3) for m in METRICS},
        "by_split": by_split,
        "by_lang": {lang: round(accuracy([r for r in rows if r["lang"] == lang], "all_ok"), 3)
                    for lang in ("tr", "en")},
        "flaky_cases": flaky_cases(rows) if config["reps"] > 1 else [],
        "failed_cases": sorted({r["case_id"] for r in rows if not r["scores"]["all_ok"]}),
        "urgency_confusion": {f"{e}->{p}": n for (e, p), n in sorted(urgency_confusion(rows).items())},
        "cost_usd": round(sum(r["cost_usd"] for r in ok_rows), 6),
        "cache_read_share": round(sum(r["cache_read_tokens"] for r in ok_rows) /
                                  max(1, sum(r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"]
                                             for r in ok_rows)), 3),
        "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95),
                       "max": max(latencies, default=0)},
        "wall_s": round(wall_s, 1),
    }


def save(rows: list[dict], summary: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    name = summary["config"]["name"]
    with (RESULTS_DIR / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (RESULTS_DIR / f"{name}.summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved results/evals/{name}.jsonl and {name}.summary.json")


def print_report(rows: list[dict], summary: dict) -> None:
    c = summary["config"]
    print(f"\n=== {c['name']}  ({c['handbook']} + {c['triage']}, hash {c['prompt_hash']}, "
          f"split={c['split']}, reps={c['reps']}) ===")
    print(f"rows={summary['n_rows']}  errors={summary['errors']}")
    print("\nsplit     n   category  urgency  human   all")
    for split, m in summary["by_split"].items():
        print(f"{split:8} {m['n']:2}   {m['category_ok']:8.0%} {m['urgency_ok']:8.0%} "
              f"{m['needs_human_ok']:6.0%} {m['all_ok']:6.0%}")
    m = summary["metrics"]
    print(f"{'total':8} {summary['n_rows']:2}   {m['category_ok']:8.0%} {m['urgency_ok']:8.0%} "
          f"{m['needs_human_ok']:6.0%} {m['all_ok']:6.0%}")
    print(f"\nall_ok by language: tr {summary['by_lang']['tr']:.0%}  en {summary['by_lang']['en']:.0%}")

    print("\nUrgency confusion (rows = expected, columns = predicted)")
    conf = urgency_confusion(rows)
    print("          " + "".join(f"{p:>8}" for p in URGENCY_LEVELS))
    for e in URGENCY_LEVELS:
        print(f"{e:>8}  " + "".join(f"{conf.get((e, p), 0):8}" for p in URGENCY_LEVELS))

    print("\nFailures:")
    for r in rows:
        if r["scores"]["all_ok"]:
            continue
        got = r["result"] or {}
        wrong = [k.removesuffix("_ok") for k in ("category_ok", "urgency_ok", "needs_human_ok") if not r["scores"][k]]
        print(f"  {r['case_id']} rep{r['rep']} [{r['split']}] wrong={','.join(wrong):24} got "
              f"{got.get('category')}/{got.get('urgency')}/human={got.get('needs_human')} {r['error']}")
    if summary["flaky_cases"]:
        print(f"\nFlaky across repeats: {', '.join(summary['flaky_cases'])}")
    lat = summary["latency_ms"]
    print(f"\nCost ${summary['cost_usd']:.6f}   cache read share {summary['cache_read_share']:.0%}   "
          f"latency p50 {lat['p50']} ms  p95 {lat['p95']} ms  max {lat['max']} ms   wall {summary['wall_s']} s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the triage golden set.")
    parser.add_argument("--name", required=True, help="Run name, used for the result file names")
    parser.add_argument("--handbook", default="handbook_v3")
    parser.add_argument("--triage", default="triage_v1")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    rows, summary = run(args)
    print_report(rows, summary)
    save(rows, summary)


if __name__ == "__main__":
    main()
