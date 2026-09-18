# 09 — UI / UX

## Design principles
1. One core loop: describe -> review plan -> watch results -> refine -> save/export. Every screen shortens it.
2. Show the machine's work: plan, sources, progress, costs, and provenance are visible, never hidden.
3. Data density with calm visuals: table-first, compact rows, strong typography, restrained colour used for status only.
4. Honest labelling: found vs derived vs AI is always distinguishable.
5. Cost before commit: credit estimate shown before any action that spends credits.
6. Keyboard-friendly, fast (virtualized tables, optimistic updates).

## Visual system
- shadcn/ui on Tailwind; light + dark themes via CSS variables.
- Font: Inter (UI), JetBrains Mono (IDs, code, API keys).
- Colour tokens: `--primary` (brand indigo), neutrals (zinc), status: success green, warning amber, danger red, info blue, ai violet.
- Spacing scale 4px; radius 8px; table row height 36px (compact) / 44px (comfortable).
- Provenance badges (used everywhere a data value appears):
  - Found in source: solid dot (neutral)
  - Derived by rule: dashed outline dot
  - AI inferred: violet sparkle icon
  - Conflict: amber "2 values" chip
  - Verification: green check (verified), amber (risky/likely), grey (unverified), red (invalid)
- Confidence meter: 5-segment bar with tooltip value.
- Empty states: explain the next action with one primary button.
- Accessibility: WCAG 2.1 AA contrast, focus rings, aria labels, never colour-only status.

## App shell
- Left sidebar: Dashboard, New Research, Search, Lists, Saved Searches, Exports, Integrations; bottom: Billing, Settings, workspace switcher, credits pill.
- Top bar: global search (Cmd/Ctrl+K command palette), running jobs indicator, notifications, user menu.

## Pages and key components
| Route | Components | Phase |
| --- | --- | --- |
| /login, /signup | Auth form, Google button, magic link, verification screen | 1 |
| /onboarding | Org name, use case, target geos, industries -> creates default scoring model | 1 (basic) / 6 (scoring) |
| /dashboard | Credits card, running jobs, recent research, new leads from automations, signal feed | 2+ |
| /research/new | PromptBox with templates; SpecChips (editable filters); FeasibilityBadges; DepthSelector (Quick/Standard/Deep); FieldPicker; CreditEstimate; Run button | 2 |
| /research/[id] | StageProgress (planning, discovery, crawling, extracting, resolving, verifying, enriching, scoring); counters; credits used; SkippedSources log; ResultsGrid streaming | 2-3 |
| /research/history | Table of past jobs: query, depth, results, credits, status, rerun, clone | 2 |
| /search | Search stored leads (no crawl), FacetPanel, ResultsGrid | 4 |
| /companies/[id] | Header (logo, name, score, status badges, actions); tabs: Overview, Contacts, People, Socials, Tech, Hiring, Signals, Locations (map), Provenance, Activity | 4-5 |
| /people/[id] | Title, company, persona tags, contacts with badges, evidence list, notes | 5 |
| /lists, /lists/[id] | Lists table; list detail = ResultsGrid scoped | 4 |
| /saved-searches | Schedule editor (cron presets), mode, actions, last run, new count | 8 |
| /exports | Jobs table, progress, download (signed URL), Sheets link, retry | 7 |
| /integrations | Cards: Google Sheets, HubSpot, Zoho, Pipedrive, Salesforce, Slack, Webhooks; connect/disconnect; mapping editor | 7-9 |
| /api-keys | Create (show once), scopes, revoke, usage chart | 8 |
| /billing | Plan, balance, usage by meter chart, top-up, invoices | 7 |
| /settings/* | Profile/2FA, workspace (defaults, retention, allowed sources), team (invite, roles, credit caps), privacy (suppression upload) | 1-7 |
| /privacy/request (public) | DSR form with email verification | 7 |

## New Research screen (detail)
1. Prompt box (multiline) + example chips: "Restaurants in Delhi with website and Instagram", "SaaS companies in Bengaluru hiring developers".
2. On "Understand" -> call `/app/research/parse` -> render SpecChips grouped: Industry, Location, Size, Web & Social, Contact, Tech, Hiring, Keywords. Each chip editable via popover; add-filter button.
3. Feasibility badges per chip: "Searchable", "Checked after enrichment", "Estimated".
4. Depth selector with what each depth includes and credits per lead.
5. Max results slider + credit estimate range (min-max) + balance after run.
6. Advanced: Boolean keyword builder, exclude lists/existing leads, min confidence, custom AI columns (Phase 6).
7. Run -> navigate to /research/[id].

## ResultsGrid (detail)
- Default columns: select, score, company (logo+name), category, city, website (status dot), phone, email (verification badge), socials (icons; muted if unverified), employee band, hiring, rating, confidence, freshness, tags, status.
- Every cell: provenance badge; click -> popover with values, sources (link), observed date, method, confidence; conflict chooser.
- Toolbar: search within results, filters, column chooser, saved views, density toggle, group by, map toggle, export.
- Bulk bar (on selection): add to list, tag, assign, status, enrich (with cost), verify emails (with cost), export, push to CRM, find lookalikes, exclude.
- Row click -> right-side detail drawer; Cmd/Ctrl+click -> full page.
- Virtualized; server-side sorting/filtering/cursor pagination; streaming rows animate in during running jobs.
- Keyboard: j/k navigate, x select, e export, l add to list, / search.

## States to design for every data view
loading (skeleton), empty, partial (job running), error (with retry and error code), restricted source notice, insufficient credits (402 -> top-up dialog).

## Frontend implementation rules
- Server state only via TanStack Query; no global store for server data. Local UI state via React state or Zustand for grid view settings.
- API types generated from OpenAPI; no hand-written duplicates.
- All forms Zod-validated; errors inline.
- Components in `features/<feature>/components`; shared primitives only in `components/ui`.
- Numbers and dates formatted with Intl (en-IN default, user locale configurable); currency INR/USD by org region.
