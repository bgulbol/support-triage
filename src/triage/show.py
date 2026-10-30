"""Print selected runs from a results file so they can be read in the terminal.

Usage: python -m triage.show step2 t01 t11
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python -m triage.show <step> <id> [<id> ...]   e.g. python -m triage.show step2 t01 t11")
        return
    step, ids = sys.argv[1], set(sys.argv[2:])
    path = ROOT / "results" / f"{step}_runs.jsonl"
    with path.open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    for row in rows:
        row_id = row.get("ticket_id") or row.get("question_id")
        if row_id not in ids:
            continue
        print("=" * 80)
        print(f"{row.get('method', row.get('condition'))} · {row_id} · {row['lang']} · "
              f"out={row['output_tokens']} · stop={row['stop_reason']}")
        print("-" * 80)
        print(row.get("raw") or row.get("answer"))
    print("=" * 80)


if __name__ == "__main__":
    main()