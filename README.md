# support-triage

What does it take to turn a raw LLM call into a support assistant you can trust? This repo answers that for one real product, **SolarDesk** (a Windows desktop app for solar mounting structural calculations), by adding five Claude API features one at a time and measuring each: system prompt, structured output, tool use, streaming and prompt caching.

Model: `claude-haiku-5-5`, effort `low`. Every number below comes from the scripts in `src/triage/` and the files in `results/`. Total API spend for the whole week: about **$0.03**.

## Key findings

1. **A short system prompt made answers correct *and* cheaper.** Without it, 0 of 12 answers were correct; with a ~420-token handbook, 11 of 12 were. Output fell from 887 to 171 tokens (Turkish), so each call became **3.4× cheaper** and **2.6× faster**, even though input grew ~10×. Output tokens cost 5× input tokens, so stopping long generic answers paid for the prompt many times over.
2. **Structured output fixes the format, not the decision.** Enforcing the JSON schema in the API gave 16/16 parseable answers versus 15/16 with prompt-only JSON. Both methods classified the same 12/16 tickets fully correctly. The one prompt-only failure was the most urgent ticket in the set, wrapped in a markdown fence.
3. **A tool turned honest-but-useless into useful.** Without a license lookup, the model (correctly) said "I can't check that". With `lookup_license`, it answered 6/6 license questions with real data, called the tool only when needed (never for the Mac question or when no code was given) and checked two codes in parallel in one turn. Cost: 3.1× per question, still $0.0003.
4. **Streaming halves the wait, not the work.** Median time to first text fell from 1918 ms to 874 ms (Turkish). Total time did not change in any meaningful way. Two calls waited 20–32 s *before* the first token; streaming cannot hide that.
5. **Caching cut input cost by 74% — when traffic is dense.** The fixed prefix (tools + handbook) is 1201 tokens, over 90% of each request. With a 5-minute cache it was written once and read 19 times: total cost −57%, latency −9%. With sparse traffic (messages more than 5 minutes apart) the same setting would make every request **25% more expensive**.

## Results

### Step 1 — system prompt (handbook) vs none

Three license questions × TR/EN × 2 repeats, effort low.

| | none TR | handbook TR | none EN | handbook EN |
|---|---:|---:|---:|---:|
| Avg input tokens | 47 | 463 | 30 | 446 |
| Avg output tokens | 887 | 171 | 552 | 100 |
| Avg latency | 5528 ms | 2089 ms | 3980 ms | 1347 ms |
| Cost per call | $0.000448 | $0.000132 | $0.000279 | $0.000095 |

| Answer quality (12 answers each) | Correct | Generic | Wrong |
|---|---:|---:|---:|
| none | 0 | 4 | 8 |
| handbook v1 | 11 | 0 | 1 |

Without the handbook the model invented license keys, account pages and order numbers, and twice told the customer SolarDesk might be a scam and should be uninstalled. The one wrong handbook answer guessed ("No, your projects are not deleted") before saying it would forward the question. A forbidden-phrase check caught 4 of the 9 wrong answers (precision 80%, recall 44%).

### Step 2 — triage form: prompt-only JSON vs API-enforced schema

16 tickets (8 TR, 8 EN), same schema, same strict parser (no fence stripping).

| | prompt_json | structured |
|---|---:|---:|
| Valid JSON / valid schema | 15/16 | **16/16** |
| Category correct | 15/16 | 15/16 |
| Urgency correct | 13/16 | 14/16 |
| `needs_human` correct | 14/16 | 15/16 |
| All three correct | 12/16 | 12/16 |
| Avg input / output tokens | 1442 / 195 | 1527 / 158 |
| Avg latency (first call excluded) | 1897 ms | 1934 ms |
| First call | 2462 ms | 3236 ms |
| Cost (16 tickets) | $0.003869 | $0.003706 |

The API adds its own format instructions (+85 input tokens), the schema's first use is ~800 ms slower (grammar compilation), and every `needs_human` error went the safe way (escalating a ticket the handbook already answered).

### Step 3 — `lookup_license` tool, hand-written tool-use loop

Six questions: expiry, expired license, unknown code, no code given, unrelated question, two codes at once.

| | no tool | with tool |
|---|---:|---:|
| Right decision to call / not call the tool | 2/6 | **6/6** |
| Expected fact in the answer (keyword check) | 1/6 | **6/6** |
| Avg API calls per question | 1.0 | 1.7 |
| Avg input / output tokens | 549 / 84 | 2286 / 133 |
| Avg latency | 1390 ms | 1985 ms |
| Cost per question | $0.000097 | $0.000295 |

Just offering the tool adds ~690 input tokens per request, even when it is not called (tool schema, the API's tool system prompt, tool rules). A second round re-sends the whole conversation.

### Step 4 — streaming vs blocking

Three questions × TR/EN × 2 repeats × 2 modes, alternating order. Medians:

| | Time to first text | Total time | Time on an empty screen |
|---|---:|---:|---:|
| Blocking TR | 1918 ms | 1918 ms | 100% |
| Streaming TR | **874 ms** | 1400 ms | 62% |
| Blocking EN | 1197 ms | 1197 ms | 100% |
| Streaming EN | **711 ms** | 1144 ms | 62% |

After the first token the model writes roughly 250 tokens/s, so a 135-token answer finishes in ~0.5 s and the fixed ~0.8 s before the first token dominates. Tail latency: of 25 calls, 2 took 20–33 s and 2 more took 5–8 s. Both slow streaming calls spent almost all of that time before the first token.

### Step 5 — prompt caching (tools + system prefix)

The Step 3 conversations, twice each, without and with a 5-minute cache breakpoint at the end of the system prompt. 20 API calls per condition.

| | no cache | cache |
|---|---:|---:|
| Uncached input tokens | 27432 | 3412 |
| Cache write / read tokens | 0 / 0 | 1201 / 22819 |
| Share of input read from cache | 0% | 83% |
| Input cost | $0.002743 | $0.000720 (−74%) |
| Total cost | $0.003542 | $0.001540 (−57%) |
| Median latency | 1302 ms | 1185 ms (−9%) |

The cache write costs 1.25× (+$0.00003 here); each read saves ~$0.00011, so the cache pays for itself on the very next request.

## Design decisions

- **Prompts live in files** (`prompts/`), versioned like code. v1 and v2 of the handbook sit side by side so results stay reproducible. Prompts are in English (cheaper, see token-lab); answers follow the customer's language.
- **The handbook describes the real process and states what does not exist** (no license key, no account page, no deactivate button). Naming the usual hallucinations explicitly is what stopped them. Internal details (WhatsApp, Supabase, team members' names) stay out of customer-facing text.
- **"Do not guess" needs an honest way out.** v2 tells the model to say the team will review the question, and to promise follow-up only when `needs_human` is true, so the promise and the action live in the same field.
- **No `temperature`, no prefill, content read by block type.** Haiku 5.5 rejects sampling parameters and prefill, and a response can start with a thinking block.
- **One Pydantic model is the single source of truth** for the triage schema: it generates the JSON schema and validates the answer. Enum values are lower-cased before validation because the docs warn casing can drift.
- **The parser is strict on purpose.** Stripping markdown fences would have hidden the prompt-only failure rate. Measure first, patch later.
- **The model asks; code acts.** The model only requests `lookup_license`; plain Python reads the data, normalises the code, and computes `status` and `days_left`. Date arithmetic and business rules stay out of the model. The tool returns only what the answer needs (no customer names).
- **Examples are always fake.** An earlier tool description used a real record's code as an example and the model repeated it to another customer. Examples now use `ABCD-1234`.
- **The loop has a hard stop** (`MAX_ROUNDS = 5`) and tool errors return `is_error` with an instructive message instead of crashing.
- **Stream the reply, not the triage form.** Code needs the whole JSON; only the customer-facing text benefits from streaming.
- **Cache what never changes, at the end of what never changes.** The breakpoint sits after tools + system; the customer's message comes after it.
- **Caching depends on traffic, not prompt length.** With messages more than 5 minutes apart, a 5-minute cache costs 25% more than no cache. A 1-hour cache (2× write) pays off only if at least two more requests arrive within the hour. Ask the customer for request volume before turning it on.

## Limitations

- **Small samples** (3–16 items, 2 repeats). Differences of one or two items, and latency differences under ~200 ms, are within noise.
- **Answer labels in Step 1 are pre-labels by an LLM**, pending human review (`results/step1_labels.jsonl`, `labeler` field). Expected triage labels in Step 2 were also written before the run, and the `urgency` definition proved ambiguous ("medium" vs "low").
- **Keyword checks are a cheap alarm, not accuracy.** They produced both false positives (a code repeated back) and false negatives. An LLM-as-judge eval is the next step.
- **The handbook and triage rules were adjusted after seeing the same test items.** A separate validation set is needed before claiming generalisation.
- **The license data is synthetic** (`data/licenses.json`), a stand-in for the real Supabase table.
- **Latency was measured from one home connection on single days.** Tail latency (20–33 s) was observed but not handled: production needs a timeout on the first event, a retry and a "typing" indicator.
- **`needs_human` is not wired to anything yet**, so "the team will get back to you" is still a promise without an action behind it.
- **Prices** are the published Haiku 5.5 rates for prompts up to 100K tokens ($0.10 / $0.50 per MTok; cache write 1.25×, read 0.1×). Check the pricing page before reusing the numbers.

### Product notes for SolarDesk

- The installation code is shown only on first launch. Showing it on an About screen would let customers (and this assistant) look up their license later.
- A real escalation path (email to the team when `needs_human` is true) is needed before the assistant can promise follow-up.

## Run it

Requires Python 3.12 and an Anthropic API key.

```powershell
git clone https://github.com/bgulbol/support-triage.git
cd support-triage
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
"ANTHROPIC_API_KEY=sk-ant-..." | Out-File -Encoding utf8 .env
```

| Step | Command | Writes |
|---|---|---|
| 0 Smoke test | `python -m triage.smoke` | — |
| 1 System prompt | `python -m triage.step1_system_prompt` | `results/step1_runs.jsonl`, `step1_review.md` |
| 2 Structured output | `python -m triage.step2_structured` | `results/step2_runs.jsonl` |
| 3 Tool use | `python -m triage.step3_tools` | `results/step3_runs.jsonl` |
| 4 Streaming | `python -m triage.step4_streaming` | `results/step4_runs.jsonl` |
| 5 Prompt caching | `python -m triage.step5_caching` | `results/step5_runs.jsonl` |

Read individual answers with `python -m triage.show step2 t01 t11`. Each step costs well under one cent.

```
data/        questions, tickets, tool questions, synthetic license table
prompts/     system_v1, handbook_v2, triage_v1, tools_v1
results/     raw runs (JSONL) and review sheets
src/triage/  client (shared helpers, cost), schema, license_store, step scripts, show
```
