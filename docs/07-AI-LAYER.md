# 07 — AI Layer

All model calls go through `services/workers/app/ai/gateway.py` (and a thin TS client if the API needs sync parse). No module calls a provider SDK directly.

## Gateway responsibilities
- Prompt registry: `app/ai/prompts/<task>/v<N>.md` + output JSON schema in `app/ai/schemas/<task>.json`.
- Routing by task -> model tier (config, not code). Fallback model on provider error.
- Structured output: provider-native JSON schema / tool-use mode; validate with Pydantic; one repair retry; then fail `parse_failed`.
- Caching: key = sha256(org_id, task, prompt_version, model, max_output_tokens, normalized_input); TTL per task. Keys are namespaced per org: a derived value is still tenant data (CLAUDE.md). The token budget is part of the key so that raising it cannot serve back a truncated answer. Provider prompt caching is automatic and is reported as `cached_tokens`.
- Batch mode for non-interactive tasks (provider batch APIs).
- Metering: tokens in/out, cost_micros, latency -> `usage_events` (meter `ai`) with job/org ids.
- Record `model`, `prompt_version` and the result's `observed_at` on every AI-derived FieldValue. A cached answer keeps the time it was originally produced, so it cannot claim to be fresh. `confidence` is part of each task's own output schema, not something the gateway invents.
- The gateway prepends a safety preamble to every task's system prompt (grounding rules 1 and 4) and wraps the payload in `<input>` delimiters. A task prompt adds to those rules; it may not weaken them.
- Every model call is attributed to an org and a research job before it is made, and everything billed reaches `usage_events` — including refusals, truncated answers and failed repair turns.

## Task catalogue
| Task | Tier | Phase | Output |
| --- | --- | --- | --- |
| spec_parse | small | 2 | what the user asked for; `app/ai/spec_draft.py` expands it into a ResearchSpec |
| intent_classify | small | 2 | source-routing intent enum (see the note below) |
| query_expand | small | 2 | list of query variants + sub-localities |
| page_classify | small | 3 | page type |
| field_extract | small | 3 | fields with evidence_text |
| industry_classify | small | 3 | taxonomy ids + confidence |
| title_normalize | small (rules first) | 5 | seniority, function |
| company_summary | small | 5 | 2-3 sentence summary with evidence ids |
| match_adjudicate | large | 6 | same/different/unsure + reason |
| critic_next_step | small | 6 | follow-up task suggestions (validated against capability map) |
| icp_fit | medium | 6 | 0-100 + reasons |
| custom_column | medium | 6 | typed answer + evidence |
| signal_extract | small | 8 | event type, date, entities |
| research_brief | large | 11 | cited report |

Two different things are called "intent", over disjoint enums, and a task that confuses them
will send a job to the wrong sources:

- `intent_classify` answers **which kind of source can find this** — `local_business`,
  `company_list`, `people`, `other`.
- `ResearchSpec.intent` answers **why the user wants it** — `prospecting`, `market_map`,
  `competitor_scan`, `hiring_signal`, `single_company`, which is what `spec_parse` returns.

`intent_classify` can answer `people`, which `ResearchSpec.entity` (`company` only) cannot yet
express; the parse endpoint has to decide what that path does.

## Grounding and safety rules
1. Crawled content is data. Wrap it in delimited blocks; instruct model to ignore instructions inside. Extraction calls have no tools.
2. Every factual output includes `evidence_text` or `evidence_ids`; validator checks evidence exists in the input; unsupported values are dropped.
3. Contact values (emails, phones) produced by a model are rejected unless the exact string appears in the source text.
4. Never infer sensitive attributes of people (religion, caste, ethnicity, health, politics, sexual orientation, age).
5. Summaries/briefs cite sources; no invented numbers.

## Evals
- `app/ai/evals/cases/<task>.jsonl` (start with 50, grow to 300+), scorer per task (exact/F1/JSON-field accuracy). Recorded answers live in `app/ai/evals/recordings/<task>.json` with the model, prompt version and score they were taken at.
- `uv run python -m app.ai.evals run <task> --model <m>`; CI replays the recorded answers (`tests/test_ai_evals.py`) so a change to a scorer, schema or the spec builder fails loudly without calling a provider; full live evals run manually before changing prompt/model. Eval spend is reported rather than written to `usage_events` (ADR-0010).
- Keep a results table in `app/ai/evals/RESULTS.md` (task, model, prompt version, score, cost per 1k items).

## Embeddings
- One vector per company from `name + category + summary + services` (1024 dims configurable); re-embed when summary changes.
- Uses: industry similarity, lookalikes, semantic search rerank. Store in `companies.embedding` (pgvector HNSW).

## Cost controls
- Pre-LLM trimming: main text only, max tokens per page per task.
- Only pages classified as relevant are sent.
- Per-job LLM cost cap from envelope budget; exceed -> `budget_exhausted`.
