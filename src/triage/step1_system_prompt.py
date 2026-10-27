"""Step 1: the same support questions with and without a system prompt.

Measures: input/output tokens, latency, cost, and a cheap forbidden-phrase flag.
Correctness is judged by a human from results/step1_review.md.
"""
import json
from pathlib import Path

from triage.client import MODEL, cost_usd, get_client, text_of, timed_create

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_FILE = ROOT / "data" / "questions.jsonl"
PROMPT_FILE = ROOT / "prompts" / "system_v1.md"
RESULTS_DIR = ROOT / "results"

CONDITIONS = ["none", "system_v1"]
LANGUAGES = ["tr", "en"]
REPEATS = 2
EFFORT = "low"
MAX_TOKENS = 1024  # thinking tokens count toward this limit on Haiku 5.5


def load_questions() -> list[dict]:
    with QUESTIONS_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def find_forbidden(answer: str, forbidden: list[str]) -> list[str]:
    lowered = answer.casefold()
    return [term for term in forbidden if term.casefold() in lowered]


def run_one(client, system_prompt: str | None, question: str):
    kwargs = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "output_config": {"effort": EFFORT},
        "messages": [{"role": "user", "content": question}],
    }
    if system_prompt is not None:
        kwargs["system"] = system_prompt
    return timed_create(client, **kwargs)


def main() -> None:
    client = get_client()
    questions = load_questions()
    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
    RESULTS_DIR.mkdir(exist_ok=True)

    rows = []
    for condition in CONDITIONS:
        prompt = system_prompt if condition == "system_v1" else None
        for q in questions:
            for lang in LANGUAGES:
                for rep in range(1, REPEATS + 1):
                    response, ms = run_one(client, prompt, q[lang])
                    answer = text_of(response)
                    row = {
                        "condition": condition,
                        "question_id": q["id"],
                        "lang": lang,
                        "rep": rep,
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "latency_ms": round(ms),
                        "cost_usd": cost_usd(response.usage),
                        "stop_reason": response.stop_reason,
                        "block_types": [b.type for b in response.content],
                        "flags": find_forbidden(answer, q["forbidden"]),
                        "answer": answer,
                    }
                    rows.append(row)
                    print(f"{condition:10} {q['id']:16} {lang} #{rep}  "
                          f"in={row['input_tokens']:4} out={row['output_tokens']:4} "
                          f"{row['latency_ms']:5} ms  flags={row['flags']}")

    write_jsonl(rows)
    write_review(rows, questions)
    print_summary(rows)


def write_jsonl(rows: list[dict]) -> None:
    path = RESULTS_DIR / "step1_runs.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(rows)} runs to {path.relative_to(ROOT)}")


def write_review(rows: list[dict], questions: list[dict]) -> None:
    lines = ["# Step 1 review: judge each answer against the expected behaviour", ""]
    for q in questions:
        lines += [f"## {q['id']}", "", f"**Expected:** {q['expected']}", ""]
        for row in rows:
            if row["question_id"] != q["id"]:
                continue
            flag_text = f" · flags: {', '.join(row['flags'])}" if row["flags"] else ""
            lines += [f"### {row['condition']} · {row['lang']} · #{row['rep']}{flag_text}", "",
                      row["answer"].strip() or "_(empty answer)_", "",
                      "Verdict: correct / generic / wrong", ""]
    path = RESULTS_DIR / "step1_review.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved review sheet to {path.relative_to(ROOT)}")


def print_summary(rows: list[dict]) -> None:
    print("\ncondition   lang  calls  avg_in  avg_out  avg_ms  flagged  cost_usd")
    for condition in CONDITIONS:
        for lang in LANGUAGES:
            group = [r for r in rows if r["condition"] == condition and r["lang"] == lang]
            n = len(group)
            avg_in = sum(r["input_tokens"] for r in group) / n
            avg_out = sum(r["output_tokens"] for r in group) / n
            avg_ms = sum(r["latency_ms"] for r in group) / n
            flagged = sum(1 for r in group if r["flags"])
            cost = sum(r["cost_usd"] for r in group)
            print(f"{condition:10}  {lang:4}  {n:5}  {avg_in:6.0f}  {avg_out:7.0f}  "
                  f"{avg_ms:6.0f}  {flagged:4}/{n:<2}  {cost:.6f}")
    total = sum(r["cost_usd"] for r in rows)
    refusals = sum(1 for r in rows if r["stop_reason"] == "refusal")
    print(f"\nTotal cost: ${total:.6f}   refusals: {refusals}")


if __name__ == "__main__":
    main()