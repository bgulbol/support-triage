"""Export the replies of saved runs for human labelling.

Writes two files:
    results/evals/replies_<name>.md         readable sheet: customer message, triage, reply
    data/golden/reply_labels_<name>.jsonl   one line per case for you to fill in (never overwritten)

Usage:
    python -m triage.evals.export_replies --runs v2_dev v2_holdout --name v2
"""
import argparse
import json

from triage.evals.run_triage_eval import GOLDEN_FILE, RESULTS_DIR, ROOT

LABELS_DIR = ROOT / "data" / "golden"


def load_replies(runs: list[str], rep: int = 1) -> list[dict]:
    """One reply per case: repeat `rep` of each run, joined with the customer's message."""
    with GOLDEN_FILE.open(encoding="utf-8") as f:
        golden = {c["id"]: c for c in map(json.loads, filter(str.strip, f))}
    replies = []
    for run in runs:
        with (RESULTS_DIR / f"{run}.jsonl").open(encoding="utf-8") as f:
            for row in map(json.loads, filter(str.strip, f)):
                if row["rep"] != rep or row["result"] is None:
                    continue
                replies.append({
                    "case_id": row["case_id"],
                    "lang": row["lang"],
                    "customer": golden[row["case_id"]]["text"],
                    "category": row["result"]["category"],
                    "urgency": row["result"]["urgency"],
                    "needs_human": row["result"]["needs_human"],
                    "reply": row["result"]["reply"],
                })
    return sorted(replies, key=lambda r: r["case_id"])


def write_sheet(replies: list[dict], name: str) -> None:
    lines = [f"# Replies to label ({name})", "",
             "Rubric: prompts/reply_rubric.md. Fill in data/golden/reply_labels_" + name + ".jsonl.", ""]
    for r in replies:
        lines += [f"## {r['case_id']} ({r['lang']})", "",
                  f"**Customer:** {r['customer']}", "",
                  f"**Triage:** {r['category']} / {r['urgency']} / needs_human={r['needs_human']}", "",
                  f"**Reply:**", "", f"> {r['reply'].replace(chr(10), chr(10) + '> ')}", ""]
    path = RESULTS_DIR / f"replies_{name}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT)} ({len(replies)} replies)")


def write_label_template(replies: list[dict], name: str) -> None:
    path = LABELS_DIR / f"reply_labels_{name}.jsonl"
    if path.exists():
        # Never overwrite human work.
        print(f"Kept existing {path.relative_to(ROOT)} (delete it yourself to start over)")
        return
    with path.open("w", encoding="utf-8") as f:
        for r in replies:
            f.write(json.dumps({"case_id": r["case_id"], "label": "", "failed_rules": [], "note": ""},
                               ensure_ascii=False) + "\n")
    print(f"Wrote {path.relative_to(ROOT)}: set label to \"pass\" or \"fail\"")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export replies for human labelling.")
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    replies = load_replies(args.runs)
    write_sheet(replies, args.name)
    write_label_template(replies, args.name)


if __name__ == "__main__":
    main()
