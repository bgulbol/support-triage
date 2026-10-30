"""Step 3: the same license questions answered without and with a lookup_license tool.

The tool-use loop is written by hand so every request and response is visible.
"""
import json
import time
from pathlib import Path

from triage.client import MODEL, cost_usd, get_client, text_of
from triage.license_store import lookup_license

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_FILE = ROOT / "data" / "tool_questions.jsonl"
HANDBOOK_FILE = ROOT / "prompts" / "handbook_v2.md"
TOOLS_PROMPT_FILE = ROOT / "prompts" / "tools_v1.md"
RESULTS_DIR = ROOT / "results"

CONDITIONS = ["no_tool", "with_tool"]
EFFORT = "low"
MAX_TOKENS = 1024
MAX_ROUNDS = 5  # safety net: never loop forever

LOOKUP_LICENSE_TOOL = {
    "name": "lookup_license",
    "description": (
        "Look up the SolarDesk license registered for one installation code. "
        "Returns whether the code was found, the status (active, expired or turned_off), "
        "the expiry date (YYYY-MM-DD), the number of days left and today's date. "
        "Use it whenever the customer asks about the status or expiry of their license and gives an "
        "installation code. Call it once per code; to check two codes, call it twice."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "install_code": {
                "type": "string",
                 "description": "The installation code exactly as the customer wrote it, in the format ABCD-1234.",
            }
        },
        "required": ["install_code"],
        "additionalProperties": False,
    },
    "strict": True,
}

TOOL_HANDLERS = {"lookup_license": lambda args: lookup_license(args["install_code"])}


def load_questions() -> list[dict]:
    with QUESTIONS_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_tool(block) -> dict:
    """Turn one tool_use block into one tool_result block."""
    handler = TOOL_HANDLERS.get(block.name)
    if handler is None:
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"Unknown tool '{block.name}'. The only tool is lookup_license."}
    try:
        output = handler(block.input)
        return {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(output)}
    except Exception as e:  # a broken tool must not crash the conversation
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"The license lookup failed: {e}. Tell the customer the team will check it."}


def converse(client, system_prompt: str, question: str, use_tools: bool) -> dict:
    """Run the full loop: ask, run requested tools, send results back, until the model answers."""
    messages = [{"role": "user", "content": question}]
    kwargs = {"model": MODEL, "max_tokens": MAX_TOKENS, "system": system_prompt,
              "output_config": {"effort": EFFORT}}
    if use_tools:
        kwargs["tools"] = [LOOKUP_LICENSE_TOOL]

    rounds, input_tokens, output_tokens, cost, tool_calls = 0, 0, 0, 0.0, []
    start = time.perf_counter()

    while True:
        rounds += 1
        response = client.messages.create(messages=messages, **kwargs)
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        cost += cost_usd(response.usage)

        if response.stop_reason != "tool_use" or rounds >= MAX_ROUNDS:
            break

        # Keep the assistant turn exactly as it came back (thinking blocks included).
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                result = run_tool(block)
                tool_calls.append({"name": block.name, "input": block.input, "result": result["content"]})
                results.append(result)
        messages.append({"role": "user", "content": results})

    return {
        "answer": text_of(response),
        "stop_reason": response.stop_reason,
        "rounds": rounds,
        "tool_calls": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "latency_ms": round((time.perf_counter() - start) * 1000),
    }


def main() -> None:
    client = get_client()
    questions = load_questions()
    handbook = HANDBOOK_FILE.read_text(encoding="utf-8")
    prompts = {
        "no_tool": handbook,
        "with_tool": handbook + "\n\n" + TOOLS_PROMPT_FILE.read_text(encoding="utf-8"),
    }
    RESULTS_DIR.mkdir(exist_ok=True)

    rows = []
    for condition in CONDITIONS:
        for q in questions:
            run = converse(client, prompts[condition], q["text"], use_tools=condition == "with_tool")
            mentions = any(term.casefold() in run["answer"].casefold() for term in q["must_mention_any"])
            row = {"condition": condition, "question_id": q["id"], "lang": q["lang"], **run,
                   "tool_calls_ok": len(run["tool_calls"]) == q["expected_tool_calls"],
                   "mentions_ok": mentions}
            rows.append(row)
            calls = ", ".join(c["input"]["install_code"] for c in run["tool_calls"]) or "-"
            print(f"{condition:9} {q['id']} {q['lang']}  rounds={run['rounds']} tools=[{calls}]  "
                  f"in={run['input_tokens']:5} out={run['output_tokens']:4} {run['latency_ms']:5} ms  "
                  f"mention={'ok' if mentions else 'MISS'}")

    path = RESULTS_DIR / "step3_runs.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    print(f"\nSaved {len(rows)} runs to {path.relative_to(ROOT)}")
    print_summary(rows)


def print_summary(rows: list[dict]) -> None:
    print("\ncondition   n  tool_calls_ok  mentions_ok  avg_rounds  avg_in  avg_out  avg_ms  cost_usd")
    for condition in CONDITIONS:
        g = [r for r in rows if r["condition"] == condition]
        n = len(g)
        print(f"{condition:9} {n:3}  {sum(r['tool_calls_ok'] for r in g):>9}/{n}  "
              f"{sum(r['mentions_ok'] for r in g):>8}/{n}  {sum(r['rounds'] for r in g) / n:10.1f}  "
              f"{sum(r['input_tokens'] for r in g) / n:6.0f}  {sum(r['output_tokens'] for r in g) / n:7.0f}  "
              f"{sum(r['latency_ms'] for r in g) / n:6.0f}  {sum(r['cost_usd'] for r in g):.6f}")
    print(f"\nTotal cost: ${sum(r['cost_usd'] for r in rows):.6f}")


if __name__ == "__main__":
    main()