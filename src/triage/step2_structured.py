"""Step 2: triage support tickets into a typed form.

Method A (prompt_json): the schema is only described in the system prompt.
Method B (structured):  the same schema is enforced by the API (output_config.format).
Both answers go through the same parser, so only the method differs.
"""
import json
from pathlib import Path

from anthropic import transform_schema
from pydantic import ValidationError

from triage.client import MODEL, cost_usd, get_client, text_of, timed_create
from triage.schema import TriageResult

ROOT = Path(__file__).resolve().parents[2]
TICKETS_FILE = ROOT / "data" / "tickets.jsonl"
HANDBOOK_FILE = ROOT / "prompts" / "handbook_v2.md"
TRIAGE_FILE = ROOT / "prompts" / "triage_v1.md"
RESULTS_DIR = ROOT / "results"

METHODS = ["prompt_json", "structured"]
EFFORT = "low"
MAX_TOKENS = 1024


def load_tickets() -> list[dict]:
    with TICKETS_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_system_prompt() -> str:
    handbook = HANDBOOK_FILE.read_text(encoding="utf-8")
    triage = TRIAGE_FILE.read_text(encoding="utf-8")
    return f"{handbook}\n\n{triage}"


def json_instruction(schema: dict) -> str:
    return (
        "\n\n## Output format\n\n"
        "Respond with only one JSON object that matches this JSON schema. "
        "Do not add any other text.\n\n"
        f"{json.dumps(schema, indent=2)}"
    )


def call(client, method: str, system_prompt: str, schema: dict, ticket_text: str):
    kwargs = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": ticket_text}],
    }
    if method == "prompt_json":
        kwargs["system"] = system_prompt + json_instruction(schema)
        kwargs["output_config"] = {"effort": EFFORT}
    else:
        kwargs["system"] = system_prompt
        kwargs["output_config"] = {
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        }
    return timed_create(client, **kwargs)


def parse(text: str) -> tuple[bool, bool, TriageResult | None, str]:
    """Return (valid_json, valid_schema, result, error). Strict: no fence stripping."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return False, False, None, f"json: {e.msg}"
    try:
        return True, True, TriageResult.model_validate(data), ""
    except ValidationError as e:
        return True, False, None, f"schema: {e.errors()[0]['loc']} {e.errors()[0]['msg']}"


def main() -> None:
    client = get_client()
    tickets = load_tickets()
    system_prompt = build_system_prompt()
    schema = transform_schema(TriageResult)
    RESULTS_DIR.mkdir(exist_ok=True)

    rows = []
    for method in METHODS:
        for ticket in tickets:
            response, ms = call(client, method, system_prompt, schema, ticket["text"])
            text = text_of(response)
            valid_json, valid_schema, result, error = parse(text)
            row = {
                "method": method,
                "ticket_id": ticket["id"],
                "lang": ticket["lang"],
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "latency_ms": round(ms),
                "cost_usd": cost_usd(response.usage),
                "stop_reason": response.stop_reason,
                "valid_json": valid_json,
                "valid_schema": valid_schema,
                "error": error,
                "category_ok": bool(result) and result.category in ticket["expected_category"],
                "urgency_ok": bool(result) and result.urgency == ticket["expected_urgency"],
                "needs_human_ok": bool(result) and result.needs_human == ticket["expected_needs_human"],
                "result": result.model_dump() if result else None,
                "raw": text,
            }
            rows.append(row)
            got = f"{result.category}/{result.urgency}/human={result.needs_human}" if result else error
            print(f"{method:11} {ticket['id']} {ticket['lang']}  in={row['input_tokens']:4} "
                  f"out={row['output_tokens']:4} {row['latency_ms']:5} ms  {got}")

    save(rows)
    print_summary(rows)
    print_mismatches(rows, tickets)


def save(rows: list[dict]) -> None:
    path = RESULTS_DIR / "step2_runs.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(rows)} runs to {path.relative_to(ROOT)}")


def print_summary(rows: list[dict]) -> None:
    print("\nmethod       n  json  schema  category  urgency  human  avg_in  avg_out  avg_ms  first_ms  cost_usd")
    for method in METHODS:
        g = [r for r in rows if r["method"] == method]
        n = len(g)

        def count(key: str) -> str:
            return f"{sum(r[key] for r in g)}/{n}"

        print(f"{method:11} {n:2}  {count('valid_json'):>5} {count('valid_schema'):>6}  "
              f"{count('category_ok'):>8} {count('urgency_ok'):>8} {count('needs_human_ok'):>6}  "
              f"{sum(r['input_tokens'] for r in g) / n:6.0f}  {sum(r['output_tokens'] for r in g) / n:7.0f}  "
              f"{sum(r['latency_ms'] for r in g[1:]) / (n - 1):6.0f}  {g[0]['latency_ms']:8}  "
              f"{sum(r['cost_usd'] for r in g):.6f}")
    refusals = sum(1 for r in rows if r["stop_reason"] == "refusal")
    print(f"\nTotal cost: ${sum(r['cost_usd'] for r in rows):.6f}   refusals: {refusals}"
          "   (avg_ms excludes the first call; first_ms shows it separately)")


def print_mismatches(rows: list[dict], tickets: list[dict]) -> None:
    expected = {t["id"]: t for t in tickets}
    print("\nMismatches against expected labels:")
    for r in rows:
        if r["category_ok"] and r["urgency_ok"] and r["needs_human_ok"]:
            continue
        t = expected[r["ticket_id"]]
        got = r["result"] or {}
        print(f"  {r['method']:11} {r['ticket_id']}  expected {'|'.join(t['expected_category'])}/"
              f"{t['expected_urgency']}/human={t['expected_needs_human']}  got "
              f"{got.get('category')}/{got.get('urgency')}/human={got.get('needs_human')}  {r['error']}")


if __name__ == "__main__":
    main()