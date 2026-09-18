# Phase 8 — Automation, Signals, Public API

## Goal
Saved searches that run on schedules and notify on new leads, first signals, and a public API with keys, rate limits and webhooks.

## Read before starting
docs/05 (public API, webhooks), 09 (saved searches, api keys), 06 (signals), 14 (Sheets append for automations), 11

## Tasks
- [ ] 8.1 Saved searches: schedule (cron + tz), modes new_only/refresh, credit cap per run, pause rules
- [ ] 8.2 Scheduler service (`jobs:schedule`), next_run_at computation, missed-run policy
- [ ] 8.3 Diffing via saved_search_seen; actions: notify (email/in-app), add to list, append to Sheet
- [ ] 8.4 Signals: hiring (new job IDs), tech change, website content change; `signals` table; signal feed UI; scoring hooks
- [ ] 8.5 News/RSS signal extraction (funding, launch, leadership) with AI event extraction (labelled AI)
- [ ] 8.6 API keys (create/show once/scopes/revoke), key auth guard, per-key rate limiting, usage logging
- [ ] 8.7 Public `/v1` endpoints per docs/05, OpenAPI docs site, idempotency keys
- [ ] 8.8 Outgoing webhooks: registration, HMAC signing, retries, delivery log, replay; SSRF guard
- [ ] 8.9 Slack notification integration (incoming webhook or app)
- [ ] 8.10 Email bounce refund flow

## Acceptance criteria
- Weekly saved search runs on schedule, reports only genuinely new companies (test with fixture changes), respects credit cap.
- Public API: full flow start research -> webhook -> fetch results works from a sample script in `docs/api-examples/`.
- Rate limits and scopes enforced (tests); webhook signatures verifiable with documented algorithm.
