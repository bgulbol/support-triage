"""Step 5: the Step 3 tool conversations with and without prompt caching.

The fixed prefix (tool definitions + handbook + tool rules) is the same in every request.
With caching, it is written to the cache once and then read at a tenth of the input price.
"""
import json
import time
from pathlib import Path

from triage.client import MODEL, cost_usd, get_client, text_of
from triage.step3_tools import LOOKUP_LICENSE_TOOL, load_questions, run_tool

ROOT = Path(__file__).resolve().parents[2]
HANDBOOK_FILE = ROOT / "prompts" / "handbook_v2.md"
TOOLS_PROMPT_FILE = ROOT / "prompts" / "tools_v1.md"
RESULTS_DIR = ROOT / "results"

CONDITIONS = ["no_cache", "cache"]
PASSES = 2
EFFORT = "low"
MAX_TOKENS = 1024
MAX_ROUNDS = 5


def build_system(prompt: str, use_cache: bool) -> list[dict]:
    """System prompt as a content block, so a cache breakpoint can be attached to it."""
    block = {"type": "text", "text": prompt}
    if use_cache:
        # Breakpoint at the end of the system prompt: tools + system are cached together.
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def converse(client, system: list[dict], question: str) -> list[dict]:
    """Same loop as Step 3, but returns one record per API call."""
    messages = [{"role": "user", "content": question}]
    calls = []
    for round_no in range(1, MAX_ROUNDS + 1):
        start = time.perf_counter()
        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=system, tools=[LOOKUP_LICENSE_TOOL],
            output_config={"effort": EFFORT}, messages=messages,
        )
        u = response.usage
        calls.append({
            "round": round_no,
            "latency_ms": round((time.perf_counter() - start) * 1000),
            "input_tokens": u.input_tokens,
            "cache_write_tokens": u.cache_creation_input_tokens or 0,
            "cache_read_tokens": u.cache_read_input_tokens or 0,
            "output_tokens": u.output_tokens,
            "cost_usd": cost_usd(u),
            "stop_reason": response.stop_reason,
            "answer": text_of(response),
        })
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user",
                         "content": [run_tool(b) for b in response.content if b.type == "tool_use"]})
    return calls


def main() -> None:
    client = get_client()
    questions = load_questions()
    prompt = HANDBOOK_FILE.read_text(encoding="utf-8") + "\n\n" + TOOLS_PROMPT_FILE.read_text(encoding="utf-8")
    RESULTS_DIR.mkdir(exist_ok=True)

    rows = []
    for condition in CONDITIONS:
        system = build_system(prompt, use_cache=condition == "cache")
        for pass_no in range(1, PASSES + 1):
            for q in questions:
                for call in converse(client, system, q["text"]):
                    row = {"condition": condition, "pass": pass_no, "question_id": q["id"], "lang": q["lang"], **call}
                    rows.append(row)
                    print(f"{condition:8} pass{pass_no} {q['id']} r{call['round']}  "
                          f"in={call['input_tokens']:5} write={call['cache_write_tokens']:5} "
                          f"read={call['cache_read_tokens']:5} out={call['output_tokens']:4}  "
                          f"{call['latency_ms']:5} ms  ${call['cost_usd']:.6f}")

    path = RESULTS_DIR / "step5_runs.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(rows)} API calls to {path.relative_to(ROOT)}")
    print_summary(rows)


def print_summary(rows: list[dict]) -> None:
    print("\ncondition  calls  input  cache_write  cache_read  read_share  median_ms  input_cost  total_cost")
    for condition in CONDITIONS:
        g = [r for r in rows if r["condition"] == condition]
        n = len(g)
        uncached = sum(r["input_tokens"] for r in g)
        write = sum(r["cache_write_tokens"] for r in g)
        read = sum(r["cache_read_tokens"] for r in g)
        latencies = sorted(r["latency_ms"] for r in g)
        median_ms = latencies[n // 2] if n % 2 else (latencies[n // 2 - 1] + latencies[n // 2]) / 2
        total = sum(r["cost_usd"] for r in g)
        output_cost = sum(r["output_tokens"] for r in g) * 0.50 / 1_000_000
        print(f"{condition:9} {n:5}  {uncached:6}  {write:10}  {read:10}  "
              f"{read / (uncached + write + read):9.0%}  {median_ms:9.0f}  "
              f"{total - output_cost:10.6f}  {total:10.6f}")
    print(f"\nTotal cost: ${sum(r['cost_usd'] for r in rows):.6f}")
    print("read_share = share of all input tokens that came from the cache")


if __name__ == "__main__":
    main()