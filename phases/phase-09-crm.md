# Phase 9 — CRM Integrations

## Goal
Push selected leads (with source info) into HubSpot, Zoho CRM, Pipedrive and Salesforce, avoid duplicates, and pull back statuses.

## Read before starting
docs/14 (CRM section), 10 (token encryption), 09 (integrations page)

## Tasks
- [ ] 9.1 CrmAdapter base, sync records, retry/idempotency, per-row error reporting
- [ ] 9.2 Field mapping engine + mapping profiles + preview UI
- [ ] 9.3 HubSpot adapter (OAuth app, batch upsert, associations, notes, custom properties)
- [ ] 9.4 Zoho CRM adapter (regional data centres, bulk write)
- [ ] 9.5 Pipedrive adapter
- [ ] 9.6 Salesforce adapter (Connected App OAuth, Composite + Bulk API 2.0)
- [ ] 9.7 Conflict strategies (skip / fill empty / overwrite)
- [ ] 9.8 Status pull-back job -> lead status + "already customer" exclusion in research
- [ ] 9.9 Automation action: auto-push leads above score X from saved searches
- [ ] 9.10 "In CRM" badges and filters in grid

## Acceptance criteria
- Each CRM: push 1,000 leads with no duplicates on re-push; source properties populated; sandbox-account manual test recorded.
- Revoked tokens mark integration disconnected and notify without crashing jobs.
