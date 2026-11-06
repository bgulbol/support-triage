"""Run the NL2SQL golden set: the model writes SQL, we execute it and compare results with the gold query.

Usage:
    python -m triage.nl2sql.run_nl2sql_eval --name sql_low
    python -m triage.nl2sql.run_nl2sql_eval --name sql_medium --effort medium
"""
import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from anthropic import transform_schema
from pydantic import BaseModel, Field

from triage.client import MODEL, cost_usd, get_client, text_of
from triage.evals.run_triage_eval import PROMPTS_DIR, RESULTS_DIR, ROOT
from triage.evals.scorers import normalize_sql, percentile, results_match
from triage.nl2sql.db import SCHEMA, TODAY, build_db, is_safe_select, run_readonly

GOLDEN_FILE = ROOT / "data" / "golden" / "nl2sql.jsonl"
MAX_TOKENS = 4096


class SqlAnswer(BaseModel):
    answerable: bool = Field(description="false if the question asks to change data or needs data that is not in the tables")
    sql: str = Field(description="One SQLite SELECT statement, or an empty string when not answerable")
    note: str = Field(description="One short sentence: what the query does, or why it cannot be answered")


def build_system(prompt: str) -> tuple[list[dict], str]:
    text = (PROMPTS_DIR / f"{prompt}.md").read_text(encoding="utf-8").format(schema=SCHEMA, today=TODAY)
    prompt_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}], prompt_hash


def score(case: dict, answer: SqlAnswer | None) -> dict:
    """Outcome of one case. Exactly one of the outcome flags is True."""
    out = {"correct": False, "correct_strict": False, "exact_match": False, "outcome": "",
           "pred_rows": None, "pred_row_count": None, "sql_error": ""}
    if answer is None:
        out["outcome"] = "no_answer"
        return out
    if not case["answerable"]:
        refused = not answer.answerable and not answer.sql.strip()
        out["correct"] = out["correct_strict"] = refused
        out["outcome"] = "refused_ok" if refused else "should_refuse"
        return out
    if not answer.answerable:
        out["outcome"] = "false_refusal"
        return out
    out["exact_match"] = normalize_sql(answer.sql) == normalize_sql(case["gold_sql"])
    if not is_safe_select(answer.sql):
        out["outcome"] = "unsafe_sql"
        return out
    try:
        pred = run_readonly(answer.sql)
    except Exception as e:  # syntax error, unknown column, ...
        out["outcome"] = "sql_error"
        out["sql_error"] = f"{type(e).__name__}: {e}"
        return out
    gold = run_readonly(case["gold_sql"])
    out["pred_rows"] = [list(r) for r in pred[:10]]
    out["pred_row_count"] = len(pred)
    out["correct_strict"] = results_match(gold, pred, case["ordered"], case.get("order_col"))
    # Lenient is the main metric: extra columns are harmless for the team, missing ones are not.
    out["correct"] = results_match(gold, pred, case["ordered"], case.get("order_col"), allow_extra_columns=True)
    if out["correct_strict"]:
        out["outcome"] = "correct"
    elif out["correct"]:
        out["outcome"] = "correct_extra_columns"
    else:
        out["outcome"] = "wrong_result"
    return out


def run_case(client, system, schema, effort, case, rep) -> dict:
    start = time.perf_counter()
    row = {"case_id": case["id"], "rep": rep, "lang": case["lang"], "tags": case["tags"],
           "question": case["question"], "answer": None, "error": ""}
    answer = None
    try:
        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": case["question"]}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        answer = SqlAnswer.model_validate_json(text_of(response))
        u = response.usage
        row.update({"input_tokens": u.input_tokens, "cache_read_tokens": u.cache_read_input_tokens or 0,
                    "output_tokens": u.output_tokens, "cost_usd": cost_usd(u)})
        row["answer"] = answer.model_dump()
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    row["latency_ms"] = round((time.perf_counter() - start) * 1000)
    row.update(score(case, answer))
    return row


def rescore_saved(source: str, cases: list[dict]) -> tuple[list[dict], dict]:
    """Re-run the saved SQL of an earlier run against the current scorer. No API calls, $0."""
    by_id = {c["id"]: c for c in cases}
    old = json.loads((RESULTS_DIR / f"{source}.summary.json").read_text(encoding="utf-8"))
    with (RESULTS_DIR / f"{source}.jsonl").open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    for row in rows:
        answer = SqlAnswer(**row["answer"]) if row["answer"] else None
        row.update(score(by_id[row["case_id"]], answer))
    return rows, old["config"] | {"rescored_from": source}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NL2SQL golden set.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--prompt", default="nl2sql_v1")
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rescore", help="Name of a saved run: re-score its SQL without calling the API")
    args = parser.parse_args()

    build_db()  # fresh, identical data on every run
    with GOLDEN_FILE.open(encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]

    if args.rescore:
        rows, config = rescore_saved(args.rescore, cases)
        config["name"] = args.name
        wall_s = 0.0
    else:
        client = get_client().with_options(timeout=60.0)
        system, prompt_hash = build_system(args.prompt)
        schema = transform_schema(SqlAnswer)
        jobs = [(case, rep) for rep in range(1, args.reps + 1) for case in cases]
        wall = time.perf_counter()
        rows = [run_case(client, system, schema, args.effort, *jobs[0])]  # writes the cache
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            rows += list(pool.map(lambda job: run_case(client, system, schema, args.effort, *job), jobs[1:]))
        wall_s = time.perf_counter() - wall
        config = {"name": args.name, "model": MODEL, "effort": args.effort, "prompt": args.prompt,
                  "prompt_hash": prompt_hash, "reps": args.reps, "cases": len(cases),
                  "date": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    n = len(rows)
    answerable = [r for r in rows if r["case_id"] in {c["id"] for c in cases if c["answerable"]}]
    outcomes = {}
    for r in rows:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    tags = sorted({t for c in cases for t in c["tags"]})
    by_tag = {t: round(sum(r["correct"] for r in rows if t in r["tags"]) /
                       max(1, sum(1 for r in rows if t in r["tags"])), 3) for t in tags}
    ok = [r for r in rows if "cost_usd" in r]
    lat = [r["latency_ms"] for r in ok]
    summary = {
        "config": config,
        "accuracy": round(sum(r["correct"] for r in rows) / n, 3),
        "accuracy_strict": round(sum(r["correct_strict"] for r in rows) / n, 3),
        "execution_accuracy_answerable": round(sum(r["correct"] for r in answerable) / max(1, len(answerable)), 3),
        "exact_match_answerable": round(sum(r["exact_match"] for r in answerable) / max(1, len(answerable)), 3),
        "outcomes": outcomes,
        "by_tag": by_tag,
        "cost_usd": round(sum(r["cost_usd"] for r in ok), 6),
        "avg_output_tokens": round(sum(r["output_tokens"] for r in ok) / max(1, len(ok))),
        "latency_ms": {"p50": percentile(lat, 50), "p95": percentile(lat, 95)},
        "wall_s": round(wall_s, 1),
    }

    c = summary["config"]
    print(f"\n=== {c['name']}  ({c['prompt']}, hash {c['prompt_hash']}, effort {c['effort']}, reps {c['reps']}) ===")
    print(f"overall accuracy {summary['accuracy']:.0%}  ({sum(r['correct'] for r in rows)}/{n})   "
          f"strict (no extra columns) {summary['accuracy_strict']:.0%}")
    print(f"answerable: execution accuracy {summary['execution_accuracy_answerable']:.0%}   "
          f"exact SQL match {summary['exact_match_answerable']:.0%}")
    print("outcomes: " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))
    print("by tag:   " + ", ".join(f"{t} {v:.0%}" for t, v in by_tag.items()))
    print("\nFailures:")
    for r in rows:
        if r["correct"]:
            continue
        sql = (r["answer"] or {}).get("sql", "")
        print(f"  {r['case_id']} rep{r['rep']} [{r['outcome']}] {r['question']}\n      sql: {sql}"
              f"{chr(10) + '      got: ' + str(r['pred_rows']) if r['pred_rows'] is not None else ''}"
              f"{chr(10) + '      ' + (r['sql_error'] or r['error']) if (r['sql_error'] or r['error']) else ''}")
    print(f"\nCost ${summary['cost_usd']:.6f}   avg output tokens {summary['avg_output_tokens']}   "
          f"latency p50 {summary['latency_ms']['p50']} ms  p95 {summary['latency_ms']['p95']} ms   wall {summary['wall_s']} s")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / f"{args.name}.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (RESULTS_DIR / f"{args.name}.summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                           encoding="utf-8")
    print(f"Saved results/evals/{args.name}.jsonl and .summary.json")


if __name__ == "__main__":
    main()
