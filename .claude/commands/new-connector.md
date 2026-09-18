---
description: Scaffold a new data-source connector with fixtures and tests
argument-hint: <connector_key e.g. google_places>
---
Create connector `$ARGUMENTS`.

1. Read `docs/08-DATA-SOURCES.md` (contract + source policy) and `docs/06-DATA-PIPELINE.md`.
2. Before coding, tell me: official API available? auth, rate limits, cost per call, ToS class (green/amber/red), fields provided, freshness TTL. Verify against the provider's current official docs. If the source is red or requires bypassing protections, stop and say so.
3. Scaffold `services/workers/app/connectors/$ARGUMENTS/` with: `connector.py` (implements BaseConnector), `models.py` (Pydantic raw + mapped models), `mapping.py` (raw -> FieldValue list with provenance), `README.md` (limits, ToS notes), `tests/` with recorded fixtures (no live calls).
4. Register it in the connector registry and seed a `sources` row.
5. All HTTP goes through the shared metered, rate-limited client.
6. Run tests and show results.
