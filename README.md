# support-triage

What does it take to turn a raw LLM call into a support assistant you can trust, and how do you *know* it works? This repo answers both for one real product, **SolarDesk** (a Windows desktop app for solar mounting structural calculations).

- **Week 2** adds five Claude API features one at a time and measures each: system prompt, structured output, tool use, streaming, prompt caching.
- **Week 3** builds the evals: a golden set with a holdout split, code-based scoring, an LLM judge calibrated against human labels, an NL2SQL eval scored by executing queries, and CI with a quality gate.

Model: `claude-haiku-5-5`. Every number below comes from the scripts in `src/triage/` and the files in `results/`. API spend: about **$0.03** in Week 2 and **$0.05** in Week 3.

## Week 3: Prompt engineering and evals

Week 2 checked 24 answers by reading them. That stops working the first time you change a comma in the prompt. Week 3 replaces reading with measuring: three kinds of grading for three kinds of output, and a CI pipeline that runs them.

| Output | How it is graded | Why |
|---|---|---|
| Triage form (category, urgency, needs_human) | Code: exact match against a golden set | There is one right answer per field |
| Customer reply (free text) | LLM judge with a rubric, calibrated on human labels | Many right answers, none comparable with `==` |
| SQL from a question | Execute both queries, compare the rows | The database is an exact referee |

Effort `low` unless stated. Repeats: 3 per case, so flaky cases show up.

### Key findings

1. **A failing eval is not always a failing model.** The first baseline scored 69%. 7 of the 10 failing cases had golden labels drafted with an AI assistant that I had not reviewed yet. Fixing labels alone, re-scored from saved answers at $0, moved the score to 73%. One case (selling a laptop) showed the model was right and the label wrong: a sold computer keeps an active code unless the team turns it off.
2. **Most errors were systematic, not random.** 9 of 10 failing cases gave the same wrong answer in all 3 repeats. That pointed at the prompt: urgency was defined by "does the team have to act?" and the model applied it inconsistently. Defining urgency by customer impact took the dev split from **70% to 93%** (5 cases fixed, 1 flaky).
3. **The holdout caught a regression the dev split could not.** On the holdout the total went from 80% to 90%, but both gains were cases the new rules were written from. On the 6 untouched cases v2 was **worse** (5/6 vs 6/6): a rule meant for "my license expires next week" was applied to "when does my license expire?". Without a holdout, v2 would have shipped as "93% accurate".
4. **Fluent replies hide factual errors from a human reader too.** On a first read I passed all 20 replies I looked at. Checking each sentence against the handbook found 2 failures: a reply claiming a code "was received" (the customer only said they sent it) and a reply promising an answer "before the end of the month".
5. **The judge agrees with the human 94% of the time, but the headline kappa needs context.** Over 36 replies (30 human-labelled + 6 known-bad), Cohen's kappa is **0.84** and the judge caught **6/6** known-bad replies, including two subtle ones. On the 30 human-labelled replies alone kappa is only **0.46**: with 28 passes and 2 fails, one miss moves kappa a lot. The judge missed one bad reply (the "end of the month" promise). It can filter replies for human review; it cannot replace it yet.
6. **String-matching SQL is almost useless.** Execution accuracy on answerable questions was **96%**; exact SQL text match was **15%**. Most remaining "failures" were the scorer's fault: correct rows plus a column nobody asked for. With a lenient rule (extra columns allowed, missing columns never) accuracy is **94%** vs 89% strict.
7. **The worst NL2SQL error raises no error.** Asked "how many projects does Güneş Enerji have?", the model counted its licenses and answered 3, with no warning, in 2 of 3 runs. There is no projects table. A query that runs and answers a different question is more dangerous than one that crashes.
8. **More thinking did not help SQL.** Effort `medium` fixed the projects question but broke a date range ("this month" without a lower bound). Same accuracy, 25% more cost.
9. **CI found a hidden dependency on day one.** The eval failed in CI with `401` while working locally: the local key came from a Windows environment variable, not from `.env`. A clean machine is the cheapest way to find what "works on my machine" depends on.

### Results

#### Step 1 & 2: triage golden set, label review, prompt v2

30 tickets (16 from Week 2 + 14 new edge cases: deadlines, injection, questions the handbook does not cover, multi-topic). 20 `dev`, 10 `holdout`. Each case × 3 repeats.

| Run | Split | all_ok | category | urgency | needs_human | Cost |
|---|---|---:|---:|---:|---:|---:|
| v1 baseline | all (90 rows) | 69% | 97% | 76% | 93% | $0.0097 |
| v1, labels reviewed (re-scored, no API) | all | 73% | 97% | 83% | 93% | $0 |
| v1, labels reviewed | dev | 70% | 95% | 80% | 95% | |
| **v2** | **dev** (60 rows) | **93%** | 98% | 95% | 100% | $0.0067 |
| v1, labels reviewed | holdout | 80% | 100% | 90% | 90% | |
| **v2** | **holdout** (30 rows) | **90%** | 100% | 90% | 100% | $0.0035 |

Holdout, split by whether the v2 rules were written from the case:

| Holdout cases | v1 | v2 |
|---|---:|---:|
| Rules written from them (t03, t30) | 0/2 | 2/2 |
| Labels changed, no new rule (t21, t27) | 2/2 | 2/2 |
| **Untouched** (t06, t09, t12, t15, t18, t24) | **6/6** | **5/6** (t15 regressed) |

Cache read share 95–96% in every run. p50 latency 1.5–1.9 s, p95 2.4–3.3 s, no tail above 5 s.

#### Step 3: LLM-as-judge for replies

Rubric: five pass/fail rules (R1 no invented facts, R2 no guessing, R3 promise matches `needs_human`, R4 language and length, R5 answers the message). Judge: Haiku 5.5, effort `medium`, sees the same instructions as the assistant, writes its reasoning before the verdict.

| Set | n | Human pass | Judge pass | Agreement | Kappa | False pass | False fail |
|---|---:|---:|---:|---:|---:|---:|---:|
| Human-labelled v2 replies | 30 | 93% | 93% | 93% | 0.46 | 1 (t20) | 1 (t05) |
| Known-bad replies from Week 2 | 6 | 0% | 0% | 100% | | 0 | 0 |
| **All** | 36 | 78% | 78% | **94%** | **0.84** | 1 | 1 |

Cost $0.0066 for 36 grades ($0.00018 each), 264 output tokens on average (thinking included).

The two disagreements are different in kind. **t20** is a judge error: it did not see "before the end of the month" as a timeline promise. **t05** is a gap between human and rubric: the reply lists calculation inputs that are not in the handbook; I passed it because the real app uses them, the judge can only see the handbook.

#### Step 4: NL2SQL, execution accuracy

Fake license database (12 customers, 20 licenses, fixed "today" 2026-11-06), 30 questions in Turkish and English, 4 of which must be refused (data changes, data not in the tables). 3 repeats.

| Effort | Accuracy (lenient) | Strict | Exec. accuracy, answerable | Exact SQL match | Refused correctly | Cost | Output tokens | p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| low | **94%** | 89% | 96% | 15% | 10/12 | $0.0075 | 134 | 1168 ms |
| medium | **94%** | 89% | 94% | 17% | 12/12 | $0.0094 | 179 | 1280 ms |

Remaining failures: q30 in both (company and date returned, the license code not; my question was ambiguous), q27 in `low` (projects counted as licenses), q21 in `medium` (lost the lower date bound).

#### Step 5: CI

| Workflow | Trigger | What it runs | Cost |
|---|---|---|---|
| `tests` | every push and PR | 27 unit tests (scorers, SQL safety, gate) | $0 |
| `eval` | changes to `prompts/`, `data/golden/`, `src/triage/`, or by hand | triage dev split + NL2SQL, 1 repeat, quality gate | ~$0.005 |

Gate: triage `all_ok` ≥ 85% on dev, NL2SQL accuracy ≥ 90%.

### Design decisions

- **Golden labels are reviewed by the person who knows the process.** Drafted labels carry `reviewed: false` until checked. Some cases accept more than one answer (`expected_urgency: ["medium", "high"]`) when the ticket is genuinely ambiguous, instead of forcing one.
- **Generating and grading are separate.** Every run saves the raw answers. `rescore` re-grades them with new labels and the NL2SQL runner re-executes saved SQL, both without API calls. A label fix and a prompt change are never measured in the same step.
- **A holdout split, opened once.** Prompt changes are judged on `dev` only. The holdout ran once per prompt version, and its result was recorded, not acted on.
- **Repeats, not single runs.** 3 repeats per case separate systematic errors (same wrong answer every time) from flaky ones (answer changes).
- **Prompt versions are hashed.** Every summary stores a hash of the exact prompt text, so a number always points to the prompt that produced it.
- **The judge reads the same instructions as the assistant.** With the handbook alone, it would have flagged business rules from the triage prompt as invented facts.
- **The judge reasons before it decides.** In the structured output, `reasoning` comes before `verdict`, and thinking is on.
- **The judge is tested on known-bad replies.** With only 2 failures among 30 human labels, a judge that always says "pass" would score 93% agreement. Real bad replies from Week 2 test whether it catches anything.
- **False passes are counted separately from false fails.** A missed bad reply reaches a customer; a false alarm only costs a review.
- **NL2SQL is scored by executing, with an exact referee.** Rows are compared as multisets; column order and `29700.0` vs `29700` are ignored; for ordered questions, rows tied on the sort key may swap, because SQL leaves that undefined.
- **Lenient scoring is a product decision, and it stays visible.** Extra columns count as correct (they do not mislead the team); missing columns never do. Strict accuracy is reported next to it, and `correct_extra_columns` is its own outcome.
- **"Today" is fixed and the data is rebuilt from code.** With `date('now')` a correct answer would turn wrong overnight.
- **Two locks on the database.** A SELECT-only filter rejects writes before they run, and the connection is opened read-only, so a write that slips past the filter still fails.
- **The CI gate is a threshold, not "no regressions".** With 1 repeat in CI, a flaky case would break the build at random, and nobody trusts a pipeline that cries wolf. The per-case comparison is printed as a report; the gate decides.
- **The holdout does not run in CI.** Running it on every prompt change would turn it into a second dev set.

### Limitations

- **Small sets.** 30 triage cases, 36 judged replies with only 8 failures, 30 SQL questions. One case moves a score by 3–10 points. Conclusions are about direction, not exact rates.
- **The holdout is partly contaminated.** Four holdout labels were changed after seeing the baseline output, and one v2 rule came from a holdout case. Only 6 of 10 holdout cases are a clean measurement.
- **Two human fail labels came out of an AI-assisted fact-check.** The assistant pointed out which sentences were not in the handbook; I made the call. The judge's agreement on those cases is not fully independent of an LLM, and one rubric sentence (about claiming something already happened) was added after the human label.
- **One judge model, one run.** The judge was not compared with a larger model or repeated; its own flakiness is unmeasured.
- **The golden data is partly synthetic.** Tickets, the license table and SQL questions were written for this repo, not taken from real traffic.
- **v2 has a known regression (t15)** that is recorded, not fixed. The fix belongs in a v3 with new dev cases and a fresh holdout.
- **CI stops on errors only through the gate.** An API outage shows up as "accuracy 0%". A separate check on the error rate would say what actually broke.

#### Product notes for SolarDesk

- **Handbook gaps found by the eval:** Excel export (Metraj → Bağlantı Elemanları → Excel), downloading the latest installer from the website, what to do about the Windows SmartScreen warning. With these, three tickets would need no human.
- **An in-app "email support" button** would send the installation code and version with the message.
- **Show the SQL with the answer** when the team queries license data in Week 8 (MCP), so a wrong reading like "projects = licenses" is visible.

### Run it (Week 3)

The API key can come from `.env` or from a system environment variable; a system variable wins.

```powershell
pip install -e ".[dev]"
python -m pytest
```

| Step | Command | Cost |
|---|---|---|
| Triage eval | `python -m triage.evals.run_triage_eval --name v2_dev --triage triage_v2 --split dev --reps 3` | ~$0.007 |
| Re-score with new labels | `python -m triage.evals.rescore --source v1_baseline --name v1_relabeled` | $0 |
| Compare two runs | `python -m triage.evals.compare --baseline v1_relabeled --candidate v2_dev` | $0 |
| Reply sheet for labelling | `python -m triage.evals.export_replies --runs v2_dev v2_holdout --name v2` | $0 |
| LLM judge | `python -m triage.evals.judge_replies --runs v2_dev v2_holdout --labels v2 --name judge_haiku` | ~$0.007 |
| NL2SQL eval | `python -m triage.nl2sql.run_nl2sql_eval --name sql_low --reps 3` | ~$0.008 |
| NL2SQL re-score | `python -m triage.nl2sql.run_nl2sql_eval --name sql_low_lenient --rescore sql_low` | $0 |

CI needs one repository secret, `ANTHROPIC_API_KEY`. Results of every run are in `results/evals/` (`<name>.jsonl` per row, `<name>.summary.json` with config and metrics).

```
data/golden/        triage.jsonl, reply_labels_v2.jsonl, reply_negatives.jsonl, nl2sql.jsonl
prompts/            triage_v2, reply_rubric, nl2sql_v1 (+ Week 2 prompts)
src/triage/evals/   run_triage_eval, scorers, rescore, compare, export_replies, judge_replies, gate
src/triage/nl2sql/  db (fake license DB, read-only runner), run_nl2sql_eval
tests/              scorer, SQL safety and gate tests
.github/workflows/  tests.yml, eval.yml
```

## Week 2: Claude API features

Five Claude API features, added one at a time. Effort `low`. API spend: about $0.03.

### Key findings

1. **A short system prompt made answers correct *and* cheaper.** Without it, 0 of 12 answers were correct; with a ~420-token handbook, 11 of 12 were. Output fell from 887 to 171 tokens (Turkish), so each call became **3.4× cheaper** and **2.6× faster**, even though input grew ~10×. Output tokens cost 5× input tokens, so stopping long generic answers paid for the prompt many times over.
2. **Structured output fixes the format, not the decision.** Enforcing the JSON schema in the API gave 16/16 parseable answers versus 15/16 with prompt-only JSON. Both methods classified the same 12/16 tickets fully correctly. The one prompt-only failure was the most urgent ticket in the set, wrapped in a markdown fence.
3. **A tool turned honest-but-useless into useful.** Without a license lookup, the model (correctly) said "I can't check that". With `lookup_license`, it answered 6/6 license questions with real data, called the tool only when needed (never for the Mac question or when no code was given) and checked two codes in parallel in one turn. Cost: 3.1× per question, still $0.0003.
4. **Streaming halves the wait, not the work.** Median time to first text fell from 1918 ms to 874 ms (Turkish). Total time did not change in any meaningful way. Two calls waited 20–32 s *before* the first token; streaming cannot hide that.
5. **Caching cut input cost by 74% — when traffic is dense.** The fixed prefix (tools + handbook) is 1201 tokens, over 90% of each request. With a 5-minute cache it was written once and read 19 times: total cost −57%, latency −9%. With sparse traffic (messages more than 5 minutes apart) the same setting would make every request **25% more expensive**.

### Results

#### Step 1 — system prompt (handbook) vs none

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

#### Step 2 — triage form: prompt-only JSON vs API-enforced schema

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

#### Step 3 — `lookup_license` tool, hand-written tool-use loop

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

#### Step 4 — streaming vs blocking

Three questions × TR/EN × 2 repeats × 2 modes, alternating order. Medians:

| | Time to first text | Total time | Time on an empty screen |
|---|---:|---:|---:|
| Blocking TR | 1918 ms | 1918 ms | 100% |
| Streaming TR | **874 ms** | 1400 ms | 62% |
| Blocking EN | 1197 ms | 1197 ms | 100% |
| Streaming EN | **711 ms** | 1144 ms | 62% |

After the first token the model writes roughly 250 tokens/s, so a 135-token answer finishes in ~0.5 s and the fixed ~0.8 s before the first token dominates. Tail latency: of 25 calls, 2 took 20–33 s and 2 more took 5–8 s. Both slow streaming calls spent almost all of that time before the first token.

#### Step 5 — prompt caching (tools + system prefix)

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

### Design decisions

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

### Limitations

- **Small samples** (3–16 items, 2 repeats). Differences of one or two items, and latency differences under ~200 ms, are within noise.
- **Answer labels in Step 1 are pre-labels by an LLM**, pending human review (`results/step1_labels.jsonl`, `labeler` field). Expected triage labels in Step 2 were also written before the run, and the `urgency` definition proved ambiguous ("medium" vs "low").
- **Keyword checks are a cheap alarm, not accuracy.** They produced both false positives (a code repeated back) and false negatives. An LLM-as-judge eval is the next step.
- **The handbook and triage rules were adjusted after seeing the same test items.** A separate validation set is needed before claiming generalisation.
- **The license data is synthetic** (`data/licenses.json`), a stand-in for the real Supabase table.
- **Latency was measured from one home connection on single days.** Tail latency (20–33 s) was observed but not handled: production needs a timeout on the first event, a retry and a "typing" indicator.
- **`needs_human` is not wired to anything yet**, so "the team will get back to you" is still a promise without an action behind it.
- **Prices** are the published Haiku 5.5 rates for prompts up to 100K tokens ($0.10 / $0.50 per MTok; cache write 1.25×, read 0.1×). Check the pricing page before reusing the numbers.

#### Product notes for SolarDesk

- The installation code is shown only on first launch. Showing it on an About screen would let customers (and this assistant) look up their license later.
- A real escalation path (email to the team when `needs_human` is true) is needed before the assistant can promise follow-up.

### Run it (Week 2)

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
