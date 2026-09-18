# 11 — Billing and Credits

All numbers are starting assumptions stored in config (`credit_rates` table or config file), never hardcoded.

## Meters and default rates
| Meter | Unit | Credits |
| --- | --- | --- |
| research_quick | delivered new lead | 1 |
| research_standard | delivered new lead | 3 |
| research_deep | delivered new lead | 8 |
| enrich_module | module run per record | 1-3 (per module config) |
| email_verify | email | 1 |
| ai_report | report | 5-25 |
| export_overage | 100 rows beyond plan limit | 1 |
| search_stored | query over stored data | 0 (rate limited) |

Not charged: duplicates of leads already in the workspace, leads only from restricted/failed sources, re-views, exports within plan limits.

## Ledger model
- `credit_ledger` is append-only; `organizations.credits_balance` is a cached value updated in the same transaction.
- Reasons: grant, purchase, subscription_renewal, reserve, consume, release, refund, expire, adjustment.
- Flow:
  1. Job create: estimate = rate x expected results (capped by `limits.max_credits`); insert `reserve` (-estimate) atomically with job row; 402 if balance < estimate.
  2. Worker completes unit: insert `usage_events` (unique unit_key).
  3. Credits consumer (API cron every 10 s or DB trigger) converts new usage_events into `consume` rows against the reservation (net zero on balance while reserved; tracked on job.credits_used).
  4. If credits_used reaches reservation: job -> `paused` + notify user to approve more.
  5. Job end: `release` unused reservation.
- Refunds: bounced "deliverable" emails reported within 30 days -> `refund` 1 credit each (Phase 8).
- Internal cost tracked separately in `usage_events.cost_micros` (for margin dashboards), never shown to customers.

## Plans (indicative, configurable)
| Plan | INR / month | USD / month | Credits | Seats | Key limits |
| --- | --- | --- | --- | --- | --- |
| Free | 0 | 0 | 50 | 1 | CSV 100 rows, no automation |
| Starter | 1,999 | 29 | 1,500 | 2 | Sheets export, 3 weekly saved searches |
| Growth | 5,999 | 79 | 6,000 | 5 | daily automation, CRM (HubSpot/Zoho/Pipedrive), API low rate, scoring models |
| Pro / Agency | 14,999 | 199 | 20,000 | 10 | client workspaces, Salesforce, webhooks, BYOK providers, priority queue |
| Enterprise | custom | custom | pool | custom | SSO, DPA, residency, SLA |
Top-up: 1,000 credits = INR 999 (valid 12 months). Extra seat INR 499 / USD 7. Annual -20%.

## Payments
- India: Razorpay Subscriptions + Orders for top-ups; GST invoice fields (GSTIN, place of supply).
- International: Stripe Billing + Checkout.
- Webhooks verify signatures, are idempotent (store provider event id), and grant credits via ledger.
- Plan entitlements in `plans` config: seats, credit allowance, automation frequency, integrations allowed, API rate limit, export row limit.
