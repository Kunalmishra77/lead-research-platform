# Phase 7 — Export, Google Sheets, Billing -> Paid Beta

## Goal
Users can export to CSV/Excel/JSON and Google Sheets, pay via Razorpay/Stripe, see usage, and exercise privacy rights. Launch paid beta.

## Read before starting
docs/14 (exports, Sheets), 11 (full), 10 (encryption, export safety, privacy mechanisms), 09 (exports, billing, integrations pages)

## Tasks
- [ ] 7.1 Export engine: selection snapshot, chunked streaming, CSV/XLSX/NDJSON writers, formula-injection escaping, S3 multipart, signed URLs, resumable cursor, notifications, audit
- [ ] 7.2 Envelope encryption module (per-org DEK, master key via env locally, KMS interface for prod)
- [ ] 7.3 Google OAuth (drive.file) connect/disconnect, token storage, Picker integration
- [ ] 7.4 Sheets writer: create spreadsheet/worksheet, headers, batched append, update mode with lead_id diff, quota backoff, continuation spreadsheet, error recovery
- [ ] 7.5 Plans/entitlements config; Razorpay subscriptions + top-ups (GST fields); Stripe billing; webhook handlers idempotent; credit grants via ledger
- [ ] 7.6 Billing page (plan, balance, usage by meter chart, invoices, top-up), 402 top-up dialog across app
- [ ] 7.7 Usage limits per role (member credit cap, export row cap)
- [ ] 7.8 Privacy request portal + admin DSR queue + erasure propagation + suppression
- [ ] 7.9 Legal pages placeholders (ToS, Privacy, AUP, DPA) wired; cookie consent if analytics used
- [ ] 7.10 Admin: org credits grant, usage and internal cost per org, export anomaly alert
- [ ] 7.11 E2E Playwright: signup -> research -> list -> export CSV -> Sheets export (mocked Google) -> top-up (test mode)

## Acceptance criteria
- 200k-row CSV and XLSX exports complete without memory growth beyond a fixed bound; resumed after forced worker kill.
- Sheets export of 20k rows succeeds within quotas using backoff (integration test with mock + one manual real test).
- Payments in test mode grant correct credits; duplicate webhook does not double-grant.
- Erasure request removes the person's data from DB, index, vectors and blocks re-collection (test).
- MVP definition of done in docs/00 met; 10 paying beta customers target begins.
