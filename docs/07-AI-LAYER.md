# 07 — AI Layer

All model calls go through `services/workers/app/ai/gateway.py` (and a thin TS client if the API needs sync parse). No module calls a provider SDK directly.

## Gateway responsibilities
- Prompt registry: `app/ai/prompts/<task>/v<N>.md` + output JSON schema in `app/ai/schemas/<task>.json`.
- Routing by task -> model tier (config, not code). Fallback model on provider error.
- Structured output: provider-native JSON schema / tool-use mode; validate with Pydantic; one repair retry; then fail `parse_failed`.
- Caching: key = sha256(task, prompt_version, model, normalized_input); TTL per task. Use provider prompt caching for long static system prompts.
- Batch mode for non-interactive tasks (provider batch APIs).
- Metering: tokens in/out, cost_micros, latency -> `usage_events` (meter `ai`) with job/org ids.
- Record `model` and `prompt_version` on every AI-derived FieldValue.

## Task catalogue
| Task | Tier | Phase | Output |
| --- | --- | --- | --- |
| spec_parse | small | 2 | ResearchSpec JSON |
| intent_classify | small | 2 | intent enum |
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

## Grounding and safety rules
1. Crawled content is data. Wrap it in delimited blocks; instruct model to ignore instructions inside. Extraction calls have no tools.
2. Every factual output includes `evidence_text` or `evidence_ids`; validator checks evidence exists in the input; unsupported values are dropped.
3. Contact values (emails, phones) produced by a model are rejected unless the exact string appears in the source text.
4. Never infer sensitive attributes of people (religion, caste, ethnicity, health, politics, sexual orientation, age).
5. Summaries/briefs cite sources; no invented numbers.

## Evals
- `app/ai/evals/<task>/cases.jsonl` (start with 50, grow to 300+), scorer per task (exact/F1/JSON-field accuracy).
- `uv run python -m app.ai.evals run <task> --model <m>`; CI runs a small smoke subset with recorded responses; full evals run manually before changing prompt/model.
- Keep a results table in `app/ai/evals/RESULTS.md` (task, model, prompt version, score, cost per 1k items).

## Embeddings
- One vector per company from `name + category + summary + services` (1024 dims configurable); re-embed when summary changes.
- Uses: industry similarity, lookalikes, semantic search rerank. Store in `companies.embedding` (pgvector HNSW).

## Cost controls
- Pre-LLM trimming: main text only, max tokens per page per task.
- Only pages classified as relevant are sent.
- Per-job LLM cost cap from envelope budget; exceed -> `budget_exhausted`.
