---
description: Scaffold a new data-source connector with fixtures and tests
argument-hint: <connector_key e.g. google_places>
---
Create connector `$ARGUMENTS`.

1. Read `docs/08-DATA-SOURCES.md` (contract + source policy) and `docs/06-DATA-PIPELINE.md`.
2. Before coding, tell me: official API available? auth, rate limits, cost per call, ToS class (green/amber/red), fields provided, freshness TTL. Verify against the provider's current official docs. If the source is red or requires bypassing protections, stop and say so.
3. Scaffold `services/workers/app/connectors/$ARGUMENTS/` with: `connector.py` (subclasses `BaseConnector`, declares key/tos_class/auth/fields_provided/default_ttl_days/rate_limit/cost_per_call_micros), `models.py` (Pydantic raw + mapped models), `mapping.py` (raw -> `FieldValue` list, every value with provenance), `README.md` (limits, ToS notes, how the fixtures were recorded).
4. Record fixtures in `services/workers/tests/fixtures/$ARGUMENTS/` — at least a success, an empty result set, a rate-limited and a restricted/error response, with keys and personal data stripped — and write `services/workers/tests/test_connector_$ARGUMENTS.py` against them with respx. Never call the live API from a test.
5. Register it in the connector registry and seed a `sources` row.
6. All HTTP goes through `ConnectorHttpClient` (metered, rate-limited, restriction-aware). Never add retry or block-handling logic inside the connector.
7. Run `uv run ruff check . && uv run mypy && uv run pytest` and show results.
