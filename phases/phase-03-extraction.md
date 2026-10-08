# Phase 3 — Crawling & Extraction

## Goal
For every candidate with a website, crawl the key pages legally and extract description, contacts, socials, tech and team info into provenance-tracked, normalized field values.

## Read before starting
docs/06 sections 4-6, 07 (field_extract, page_classify, grounding), 08 (policy), 10 (SSRF), 13 (golden datasets)

## Tasks
- [x] 3.1 Crawl frontier (Redis), per-domain politeness, global concurrency per pool — `app/crawl/frontier.py`; the claim is one Lua script, which is what makes per-host concurrency 1 true across workers rather than only within one
- [x] 3.2 robots.txt fetch/cache/evaluate with protego; crawl-delay support
- [x] 3.3 HTTP fetcher: httpx HTTP/2, timeouts, body cap, conditional GET, redirects cap, SSRF guard, honest UA — `app/crawl/fetcher.py` over `ConnectorHttpClient` (ADR-0013), with redirects followed by hand so robots and the SSRF guard run on every hop
- [x] 3.4 Restriction detector (401/403, challenge pages, CAPTCHA markers, login forms, paywalls) -> `access_restricted`, no retry, counted in metrics — built in Phase 2 as `app/connectors/restrictions.py` (strong interstitial markers, weak login/captcha/paywall markers needing corroboration) and counted per connector by `app.admin_connector_health` (task 2.13)
- [ ] 3.5 Browser fetcher (Playwright pool) used only on SPA-shell heuristic; resource blocking; timeouts
- [ ] 3.6 Raw store to S3 with content-hash dedupe; raw_documents rows (partitioned)
- [x] 3.7 Page discovery: homepage links + sitemap -> choose contact/about/team/careers/services pages (rules) — `app/crawl/discovery.py`; anchor text beats an opaque URL, the sitemap is only read for roles the homepage left unfilled
- [x] 3.8 Extractors: JSON-LD/microdata/OpenGraph (extruct), contacts (emails incl. de-obfuscation, phones, WhatsApp), social links, address from footer — `app/crawl/structured.py` (nodes ranked, so a breadcrumb trail is not the company name) and `app/crawl/contacts.py` (a `mailto:`/`tel:` href outranks text; a phone must be valid per `phonenumbers`, not merely possible)
- [ ] 3.9 Tech detector with open fingerprint set + DNS MX/TXT
- [ ] 3.10 Page classifier (rules + small model fallback)
- [ ] 3.11 LLM field extraction for description/services/products/team members with evidence validation
- [ ] 3.12 Normalizers: phone, email, URL/domain (+platform domains list), name, social handles, address basic, industry mapping
- [ ] 3.13 Best-value computation for companies after each batch
- [ ] 3.14 Golden dataset (50 saved sites) + quality report command
- [ ] 3.15 Web: results grid shows website, emails, phones, socials, tech with provenance badges and popover; company drawer basic
- [ ] 3.16 Metrics: crawl success ratio, restricted count, stage durations, cost per candidate

## Acceptance criteria
- Golden set: email precision >= 95%, phone precision >= 95%, social link precision >= 95%, description present >= 85%.
- Robots disallow and restricted pages are never fetched/retried (tests prove it).
- SSRF tests: private IPs, metadata IP, file:// all blocked.
- Browser used on < 15% of fetched pages on a 500-site sample.
- Cost per Standard-depth candidate measured and recorded in PROGRESS.md.
