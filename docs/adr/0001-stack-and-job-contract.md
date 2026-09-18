# ADR-0001: Stack split and TS <-> Python job contract

- Status: Accepted (revisit at end of Phase 1)
- Date: 2026-09-17

## Context
The web app and API benefit from TypeScript (shared types with frontend, Next.js ecosystem). Crawling, parsing, entity resolution, normalization and AI extraction have far stronger libraries in Python. We need a reliable, language-neutral way to hand work between them.

## Options considered
1. All TypeScript (Node crawler + BullMQ) — single language, weaker data/NLP libraries.
2. All Python (FastAPI + Celery) — strong data libs, loses TS type sharing with the frontend.
3. Laravel API + Python workers — matches team PHP experience; viable, but fewer shared types with Next.js.
4. NestJS API + Python workers over Redis Streams with JSON Schema contracts — best libraries on both sides, explicit contract.
5. Temporal from day one — durable workflows, more infra and learning curve for MVP.

## Decision
Option 4. `packages/contracts` holds JSON Schemas (ResearchSpec, job envelope, progress event, scoring model, field catalogue) and generates TS and Pydantic types. API publishes envelopes with XADD; Python pools consume via consumer groups; retries via a delayed ZSET; DLQ streams. Workers persist state in Postgres and publish progress over Redis pub/sub. Temporal is reconsidered in Phase 10.

If the team prefers Laravel for the API, replace only `apps/api`; the contract, DB schema (migrations would then move to Laravel), workers and web stay the same. Record that as ADR-0002 before Phase 1 coding.

## Consequences
- Two toolchains in CI.
- Contract changes require regenerating types and bumping `spec_version`/envelope version.
- Need our own small reliability layer on Redis Streams (claiming stale pending messages with XAUTOCLAIM, retry counts, DLQ).
