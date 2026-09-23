# ADR-0009: AI provider is OpenAI

- Status: Accepted
- Date: 2026-09-23

## Context

docs/07 requires every model call to go through one gateway with provider-native JSON-schema
output, a repair retry, caching, and metering into `usage_events`. It deliberately does not name a
provider. Task 2.2 has to pick one, because the adapter, the model tier table and the price table
are all provider-specific.

The owner supplied a paid OpenAI API key and asked for OpenAI rather than the Anthropic API that
the first draft of the gateway targeted.

## Options considered

1. **Anthropic (Claude) API** — native JSON-schema output through `output_config`, explicit prompt
   caching with `cache_control`, batch API. This is what the gateway was first written against.
   No account on this project, so nothing could be verified end to end.
2. **OpenAI Responses API** — native JSON-schema output through `text.format`, automatic prompt
   caching (reported back as `input_tokens_details.cached_tokens`), batch API. A working paid key
   exists, so the adapter, the model IDs and the token accounting can all be checked live.
3. **Both behind the adapter interface from day one** — maximum flexibility, but two adapters to
   keep correct and two price tables to keep honest while only one of them is ever exercised.

## Decision

Use **OpenAI** through the Responses API (`client.responses.create`) as the only provider for now.
The `ModelProvider` protocol in `app/ai/providers/base.py` stays provider-neutral, so a second
adapter is a new file and a config entry, not a change at any call site.

Verified against the live account rather than assumed (`client.models.list()`, 138 models):

| Tier | Model | Used by |
| --- | --- | --- |
| small | `gpt-5.4-mini-2026-03-17` | spec_parse, intent_classify, query_expand, page_classify, field_extract |
| medium | `gpt-5.5-2026-04-23` | icp_fit, custom_column |
| large | `gpt-5.5-pro-2026-04-23` | match_adjudicate, research_brief |

Dated snapshots, not floating aliases: a silent model change would move eval scores and costs
underneath us. `AI_MODEL_SMALL` / `_MEDIUM` / `_LARGE` override a tier without a code change.

## Consequences

- The gateway targets the Responses API. Structured output is `text.format = {type: json_schema,
  strict: true}`; refusals and `incomplete_details.reason == "max_output_tokens"` are distinct
  failures, classified `parse_failed`.
- Prompt caching is automatic rather than opt-in, so there is no `cache_control` to place; the
  gateway reports `cached_tokens` and prices them separately.
- The price table in `app/ai/config.py` is the one thing that cannot be read from the API. The
  figures there are estimates and are marked unverified: they drive internal cost reporting and
  the per-job cost cap, never what a customer is charged (docs/11). Confirming them against the
  billing dashboard is an open item in `PROGRESS.md`.
- `anthropic` was removed from `services/workers` dependencies; `openai>=3.19,<4` replaces it.
- Evals (task 2.3) and their `RESULTS.md` record the exact dated model, so a model rotation is a
  visible, re-scored decision.
