# 00 — Product Brief

## One-line
Describe the leads you need in plain language; get a deduplicated, verified, scored, source-cited dataset in Google Sheets, Excel, CSV or your CRM.

## Positioning
Not another static contact database. LeadForge researches on demand across legitimate public sources and official APIs, and every research run grows a shared, deduplicated company graph that makes later research faster and cheaper.

## Target users (V1)
- Indian SMB sales teams, agencies, consultants and B2B founders
- Use cases: local business prospecting (Google Maps + websites), SaaS/startup prospecting, hiring-signal prospecting, agency client research

## Core loop
Describe -> review plan (editable filters + credit estimate) -> watch results stream -> refine -> save to list / export / push to CRM -> optionally schedule to repeat.

## Differentiators
1. Natural-language research compiled into a visible, editable ResearchSpec.
2. Custom research columns ("Does the site offer online booking?") answered with evidence.
3. Provenance + confidence on every cell; conflicts visible and resolvable.
4. India-first coverage and billing (INR, GST, Razorpay, Zoho CRM).
5. Living lists: saved searches, signals, refresh.
6. Compliance by design: source policy engine, suppression list, privacy request portal.
7. Agency mode: per-client workspaces (later phase).
8. Depth-based credits: Quick / Standard / Deep.

## Data posture
- Company-first. Person data limited to work-role info from company websites, public registries and licensed providers.
- No scraping of logged-in or protected views (LinkedIn, Instagram, Facebook, Threads, X). Social profiles are stored as URLs/handles found on company sites or via search APIs; metrics only through official APIs.
- Business contact data still treated as personal data (GDPR, DPDP, CCPA).

## MVP definition of done (end of Phase 7)
- A user signs up, runs an NL research query for a category + city, sees live results with website, phone, email (verified), socials, tech and confidence, saves them to a list, and exports to CSV/Excel/Google Sheets, paying with credits.
- Quality gates on 1,000 test leads across 5 Indian cities: website fill rate >= 70%, duplicate rate < 5%, email bounce < 8%.
- 10 paying beta customers.

## North-star metric
Cost and time to a qualified, verified lead. Targets: first 50 results < 2 minutes; internal cost per Standard lead < USD 0.02 (planning assumption, validate in Phase 3).

## Glossary
- **ResearchSpec**: typed, validated representation of a research request (see `packages/contracts`).
- **Company graph**: global, deduplicated `companies/people/contacts/...` tables shared across tenants.
- **Lead**: a tenant's pointer to a company (and optionally a person) with score, status, notes.
- **FieldValue**: one observed value for one field of one entity, with provenance.
- **Connector**: module that talks to one source.
- **Depth**: Quick (discovery fields), Standard (+website profile, socials, tech, contacts), Deep (+people, hiring, news, AI summary, multi-source verification).
