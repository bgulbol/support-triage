"""Step 4: the same answers with and without streaming.

Measures time to first event, time to first text (what the customer waits for),
and total time. A short live demo runs first so you can see streaming happen.
"""
import json
import statistics
import time
from pathlib import Path

from triage.client import MODEL, cost_usd, get_client, text_of

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_FILE = ROOT / "data" / "questions.jsonl"
HANDBOOK_FILE = ROOT / "prompts" / "handbook_v2.md"
RESULTS_DIR = ROOT / "results"

LANGUAGES = ["tr", "en"]
REPEATS = 2
EFFORT = "low"
MAX_TOKENS = 1024


def load_questions() -> list[dict]:
    with QUESTIONS_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def request_kwargs(system_prompt: str, question: str) -> dict:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system_prompt,
        "output_config": {"effort": EFFORT},
        "messages": [{"role": "user", "content": question}],
    }


def run_blocking(client, kwargs: dict) -> dict:
    """Classic call: nothing arrives until the whole answer is ready."""
    start = time.perf_counter()
    response = client.messages.create(**kwargs)
    total_ms = (time.perf_counter() - start) * 1000
    return {"first_event_ms": total_ms, "first_text_ms": total_ms, "total_ms": total_ms,
            "response": response}


def run_streaming(client, kwargs: dict, echo: bool = False) -> dict:
    """Streaming call: events arrive as the model writes."""
    start = time.perf_counter()
    first_event_ms = first_text_ms = None
    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            now_ms = (time.perf_counter() - start) * 1000
            if first_event_ms is None:
                first_event_ms = now_ms
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                if first_text_ms is None:
                    first_text_ms = now_ms
                if echo:
                    print(event.delta.text, end="", flush=True)
        response = stream.get_final_message()
    total_ms = (time.perf_counter() - start) * 1000
    if echo:
        print()
    return {"first_event_ms": first_event_ms, "first_text_ms": first_text_ms or total_ms,
            "total_ms": total_ms, "response": response}


def live_demo(client, system_prompt: str, question: str) -> None:
    print("=" * 80)
    print(f"Live demo (streaming): {question}")
    print("-" * 80)
    timing = run_streaming(client, request_kwargs(system_prompt, question), echo=True)
    print("-" * 80)
    print(f"first text after {timing['first_text_ms']:.0f} ms, finished after {timing['total_ms']:.0f} ms")
    print("=" * 80 + "\n")


def main() -> None:
    client = get_client()
    questions = load_questions()
    system_prompt = HANDBOOK_FILE.read_text(encoding="utf-8")
    RESULTS_DIR.mkdir(exist_ok=True)

    live_demo(client, system_prompt, questions[0]["tr"])

    rows = []
    for q in questions:
        for lang in LANGUAGES:
            for rep in range(1, REPEATS + 1):
                # Alternate the order so neither mode always goes first.
                modes = ["blocking", "streaming"] if rep % 2 else ["streaming", "blocking"]
                for mode in modes:
                    kwargs = request_kwargs(system_prompt, q[lang])
                    timing = run_blocking(client, kwargs) if mode == "blocking" else run_streaming(client, kwargs)
                    response = timing.pop("response")
                    row = {
                        "mode": mode, "question_id": q["id"], "lang": lang, "rep": rep,
                        **{k: round(v) for k, v in timing.items()},
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cost_usd": cost_usd(response.usage),
                        "stop_reason": response.stop_reason,
                        "block_types": [b.type for b in response.content],
                        "answer": text_of(response),
                    }
                    rows.append(row)
                    print(f"{mode:9} {q['id']:16} {lang} #{rep}  first_text={row['first_text_ms']:5} ms  "
                          f"total={row['total_ms']:5} ms  out={row['output_tokens']:4}  "
                          f"blocks={row['block_types']}")

    path = RESULTS_DIR / "step4_runs.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(rows)} runs to {path.relative_to(ROOT)}")
    print_summary(rows)


def print_summary(rows: list[dict]) -> None:
    print("\nmode       lang  n   median_first_text  median_total  avg_out  waited_share")
    for mode in ["blocking", "streaming"]:
        for lang in LANGUAGES:
            g = [r for r in rows if r["mode"] == mode and r["lang"] == lang]
            first = statistics.median(r["first_text_ms"] for r in g)
            total = statistics.median(r["total_ms"] for r in g)
            out = sum(r["output_tokens"] for r in g) / len(g)
            print(f"{mode:9}  {lang:4} {len(g):2}   {first:15.0f} ms  {total:9.0f} ms  {out:7.0f}  "
                  f"{first / total:11.0%}")
    print(f"\nTotal cost: ${sum(r['cost_usd'] for r in rows):.6f}")
    print("waited_share = how much of the total time the customer stares at an empty screen")


if __name__ == "__main__":
    main()