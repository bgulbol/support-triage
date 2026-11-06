"""LLM-as-judge for customer replies, calibrated against human labels.

The judge gets the handbook (the truth), the rubric, the customer's message, the triage
decision and the reply. It reasons first, then gives pass/fail and the failed rules.

Usage:
    python -m triage.evals.judge_replies --runs v2_dev v2_holdout --labels v2 --name judge_haiku
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from anthropic import transform_schema
from pydantic import BaseModel, Field

from triage.client import get_client, text_of
from triage.evals.export_replies import LABELS_DIR, load_replies
from triage.evals.run_triage_eval import PROMPTS_DIR, RESULTS_DIR, ROOT
from triage.evals.scorers import agreement_report

# USD per million tokens, prompts up to 100K. Source: platform.claude.com pricing page.
# Cache write is 1.25x input (5-minute cache), cache read is listed per model.
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
}
MAX_TOKENS = 4096  # thinking tokens count towards max_tokens

Rule = Literal["R1", "R2", "R3", "R4", "R5"]


class JudgeVerdict(BaseModel):
    # Field order matters: the model writes the reasoning before it commits to a verdict.
    reasoning: str = Field(description="Check the reply against each rule, one short sentence per rule")
    failed_rules: list[Rule] = Field(description="Rules the reply breaks; empty if none")
    verdict: Literal["pass", "fail"] = Field(description="fail if any rule is broken")


def build_system(handbook: str, triage: str) -> list[dict]:
    """The judge sees exactly the instructions the assistant saw, then the rubric."""
    instructions = (PROMPTS_DIR / f"{handbook}.md").read_text(encoding="utf-8") + "\n\n" + \
                   (PROMPTS_DIR / f"{triage}.md").read_text(encoding="utf-8")
    rubric = (PROMPTS_DIR / "reply_rubric.md").read_text(encoding="utf-8")
    text = ("You grade replies written by a customer support assistant.\n\n"
            "# The assistant's instructions\n\n"
            f"{instructions}\n\n{rubric}")
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def judge_one(client, model: str, effort: str, system: list[dict], schema: dict, r: dict) -> dict:
    user = (f"Customer message ({r['lang']}):\n{r['customer']}\n\n"
            f"Triage decision: needs_human = {str(r['needs_human']).lower()}\n\n"
            f"Reply to grade:\n{r['reply']}")
    start = time.perf_counter()
    row = {"case_id": r["case_id"], "verdict": None, "failed_rules": [], "reasoning": "", "error": ""}
    try:
        response = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        v = JudgeVerdict.model_validate_json(text_of(response))
        u = response.usage
        p = PRICES[model]
        row.update(v.model_dump())
        row["cost_usd"] = (u.input_tokens * p["input"]
                           + (u.cache_creation_input_tokens or 0) * p["input"] * 1.25
                           + (u.cache_read_input_tokens or 0) * p["cache_read"]
                           + u.output_tokens * p["output"]) / 1_000_000
        row["output_tokens"] = u.output_tokens
    except Exception as e:  # record and keep going, like the triage runner
        row["error"] = f"{type(e).__name__}: {e}"
    row["latency_ms"] = round((time.perf_counter() - start) * 1000)
    return row


def load_labels(name: str) -> dict[str, dict]:
    path = LABELS_DIR / f"reply_labels_{name}.jsonl"
    with path.open(encoding="utf-8") as f:
        labels = {r["case_id"]: r for r in map(json.loads, filter(str.strip, f))}
    missing = [c for c, r in labels.items() if r["label"] not in ("pass", "fail")]
    if missing:
        raise SystemExit(f"Label these cases first (pass/fail) in {path.name}: {', '.join(missing)}")
    return labels


def load_extra(paths: list[str]) -> tuple[list[dict], dict[str, dict]]:
    """Extra labelled replies (e.g. known-bad answers). Each line holds the reply and its label."""
    replies, labels = [], {}
    for path in paths:
        with (ROOT / path).open(encoding="utf-8") as f:
            for row in map(json.loads, filter(str.strip, f)):
                replies.append(row)
                labels[row["case_id"]] = row
    return replies, labels


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade replies with an LLM judge and compare with human labels.")
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--labels", required=True, help="Suffix of data/golden/reply_labels_<labels>.jsonl")
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", default="claude-haiku-5-5", choices=sorted(PRICES))
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--handbook", default="handbook_v3")
    parser.add_argument("--triage", default="triage_v2", help="Must match the prompt the replies were made with")
    parser.add_argument("--extra", nargs="*", default=["data/golden/reply_negatives.jsonl"],
                        help="Extra labelled replies, e.g. known-bad answers")
    args = parser.parse_args()

    labels = load_labels(args.labels)  # fail fast, before spending money
    replies = [r for r in load_replies(args.runs) if r["case_id"] in labels]
    extra_replies, extra_labels = load_extra(args.extra)
    replies += extra_replies
    labels |= extra_labels
    client = get_client().with_options(timeout=90.0)
    system, schema = build_system(args.handbook, args.triage), transform_schema(JudgeVerdict)

    wall = time.perf_counter()
    rows = [judge_one(client, args.model, args.effort, system, schema, replies[0])]  # writes the cache
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows += list(pool.map(lambda r: judge_one(client, args.model, args.effort, system, schema, r), replies[1:]))
    wall_s = time.perf_counter() - wall

    for row in rows:
        row["human"] = labels[row["case_id"]]["label"]
        row["human_rules"] = labels[row["case_id"]]["failed_rules"]
    report = agreement_report([r["human"] for r in rows], [r["verdict"] for r in rows])

    print(f"\n=== {args.name}  ({args.model}, effort {args.effort}, {len(rows)} replies) ===")
    print(f"human pass rate {report['human_pass_rate']:.0%}   judge pass rate {report['judge_pass_rate']:.0%}")
    print(f"agreement {report['agreement']:.0%}   Cohen's kappa {report['kappa']:.2f}")
    print(f"false pass (judge pass, human fail): {report['false_pass']}   "
          f"false fail (judge fail, human pass): {report['false_fail']}   errors: {report['errors']}")
    print("\nDisagreements:")
    for r in rows:
        if r["verdict"] != r["human"]:
            print(f"  {r['case_id']}  human={r['human']} {r['human_rules']}  judge={r['verdict']} {r['failed_rules']}"
                  f"  {r['error']}\n      judge: {r['reasoning'][:300]}")
    cost = sum(r.get("cost_usd", 0) for r in rows)
    print(f"\nCost ${cost:.6f}   avg output tokens "
          f"{sum(r.get('output_tokens', 0) for r in rows) / len(rows):.0f}   wall {wall_s:.1f} s")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / f"{args.name}.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    by_set = {}
    for label, group in (("main", [r for r in rows if r["case_id"] not in extra_labels]),
                         ("extra", [r for r in rows if r["case_id"] in extra_labels])):
        if group:
            by_set[label] = agreement_report([r["human"] for r in group], [r["verdict"] for r in group])
            print(f"{label:5}: n={by_set[label]['n']}  agreement {by_set[label]['agreement']:.0%}  "
                  f"false pass {by_set[label]['false_pass']}  false fail {by_set[label]['false_fail']}")
    summary = {"name": args.name, "model": args.model, "effort": args.effort, "runs": args.runs,
               "handbook": args.handbook, "triage": args.triage, "extra": args.extra, "by_set": by_set,
               **report, "cost_usd": round(cost, 6), "wall_s": round(wall_s, 1)}
    (RESULTS_DIR / f"{args.name}.summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved results/evals/{args.name}.jsonl and .summary.json")


if __name__ == "__main__":
    main()
