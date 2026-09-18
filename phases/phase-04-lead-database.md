# Phase 4 — Lead Database, Entity Resolution, Verification

## Goal
Turn raw candidates into a deduplicated, verified company graph and give users a real workspace: leads, results grid, company page, lists, tags, notes, status.

## Read before starting
docs/04 (full), 05 (leads/lists endpoints), 06 sections 7-8 and 12, 09 (ResultsGrid, company page), 13 (quality gates)

## Tasks
- [ ] 4.1 Remaining global tables: company_domains, contacts, social_profiles, websites, match_candidates, entity_merges, suppression_list
- [ ] 4.2 Blocking + feature computation + rule-based match scoring; thresholds config
- [ ] 4.3 Merge service with undo; branch/chain handling; lead re-pointing
- [ ] 4.4 Labelled pair set (200) + resolution quality report
- [ ] 4.5 Verification: email syntax/MX/disposable/role + verifier API connector (batched), phone validity, website status/parked/SSL, social ownership rules, conflict detection
- [ ] 4.6 Confidence computation per field and record status
- [ ] 4.7 Leads materialization per workspace with dedupe (no double charge) and suppression checks
- [ ] 4.8 Tenant tables: leads, lead_events, lists, list_items, tags, lead_tags, notes (+ RLS + isolation tests)
- [ ] 4.9 API: leads search/filter (Postgres FTS + trigram + filters), lead detail, bulk actions, company detail + provenance endpoint, lists/tags/notes CRUD, match feedback
- [ ] 4.10 Web: full ResultsGrid (virtualized, server-side sort/filter/pagination, column chooser, saved views, bulk bar), company page tabs (Overview, Contacts, Socials, Tech, Locations map, Provenance, Activity), lists pages, conflict chooser, merge/not-same actions
- [ ] 4.11 CSV import of user company lists (domain/name) into workspace as tenant-private leads -> queue research
- [ ] 4.12 Admin: resolution review queue (pending/unsure pairs), suppression list management

## Acceptance criteria
- 1,000 test leads across 5 Indian cities: duplicate rate < 5%, website fill >= 70%.
- Resolution on labelled pairs: precision >= 0.97 at auto-merge threshold.
- Email verification statuses shown; suppressed values never displayed or exported (test).
- Grid handles 50,000 leads smoothly (scroll + sort < 500 ms server response).
- Private beta ready: 5-10 users can run research and organise leads.
