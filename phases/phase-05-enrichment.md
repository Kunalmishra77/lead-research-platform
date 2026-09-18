# Phase 5 — Enrichment

## Goal
Pluggable enrichment modules with waterfall execution and research depths (Quick/Standard/Deep), adding registries, hiring and people-from-website data.

## Read before starting
docs/06 section 10, 08 (registries, ATS), 07 (title_normalize, company_summary), 11 (enrich meters)

## Tasks
- [ ] 5.1 Enrichment module contract, registry, waterfall runner with confidence target and budget
- [ ] 5.2 Modules: domain_finder, website_profiler (refactor from Phase 3), social_resolver, contact_extractor, tech_detector, email_verifier as modules
- [ ] 5.3 Registry matcher: Companies House, OpenCorporates, SEC EDGAR connectors; name+address matching to legal entity; status, founded, officers (as people with role, work-role data only)
- [ ] 5.4 Hiring monitor: ATS board connectors (Greenhouse, Lever, Ashby, Workable) + careers page JobPosting extraction; department classification; open_jobs_count
- [ ] 5.5 People from team/leadership pages; title normalization (rules + small model); persona tags
- [ ] 5.6 Company summary (AI, evidence-linked)
- [ ] 5.7 Depth definitions wired end-to-end (planner, credits, UI copy)
- [ ] 5.8 Bulk "Enrich selected" and "Re-research company" with cost preview
- [ ] 5.9 People page + People tab on company page; Hiring tab

## Acceptance criteria
- Deep research on 100 known companies adds registry data for >= 80% of UK/US entities and hiring data where ATS boards exist.
- Each module has fixtures and unit tests; waterfall stops at target confidence (test).
- No person contact appears unless found in source or provider; derived patterns labelled.
