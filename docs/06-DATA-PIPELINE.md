# 06 — Data Pipeline (services/workers)

```text
ResearchSpec
 -> planner (task DAG with budgets)
 -> discovery connectors (Places, SERP, registries)        [jobs:discovery]
 -> candidate upsert (lightweight company/location rows)
 -> crawl frontier -> robots/politeness -> HTTP fetch -> (browser fallback)  [jobs:crawl_*]
 -> raw_documents (S3) -> page classifier -> extractors     [jobs:pipeline]
 -> field_values (provenance) -> normalizers
 -> entity resolution (block -> score -> adjudicate -> merge)
 -> verification -> confidence -> best values
 -> critic (missing/conflicts & budget?) -> follow-up tasks
 -> enrichment waterfall -> AI analysis (classify/summarize/signals) [jobs:llm]
 -> scoring -> leads (tenant) -> progress events -> export
```

## 1. ResearchSpec
JSON Schema in `packages/contracts/schemas/research-spec.schema.json`. Minimum shape:
```json
{
  "spec_version": 1,
  "entity": "company",
  "intent": "prospecting | market_map | competitor_scan | hiring_signal | single_company",
  "filters": {
    "industry": {"include": ["restaurant"], "exclude": [], "taxonomy_ids": []},
    "location": {"country": "IN", "states": [], "cities": ["Delhi"], "radius_km": null, "include_metro_area": true},
    "employee_count": {"gte": 50, "lte": null, "allow_estimate": true},
    "has_website": true,
    "contact": {"any_of": ["email", "phone"]},
    "social": {"instagram": {"required": true, "active_within_days": 30}},
    "technologies": {"include": [], "exclude": []},
    "hiring": {"required": false, "departments": []},
    "funding": {"last_round_within_days": null},
    "rating": {"gte": null, "min_reviews": null},
    "min_confidence": 0.6
  },
  "keywords": {"must": [], "should": [], "not": []},
  "fields": ["name", "website", "phone", "email", "instagram", "employee_band", "rating"],
  "custom_columns": [],
  "depth": "quick | standard | deep",
  "limits": {"max_results": 500, "max_credits": 2000},
  "exclude": {"lists": [], "existing_leads": true},
  "feasibility": [{"filter": "employee_count", "mode": "directly_searchable | post_filter | estimate_only", "note": ""}]
}
```

## 2. Planner
- Deterministic plan templates per intent (`orchestrator/templates/*.yaml`) + capability map `field -> connectors` with historical yield.
- LLM (small model) only generates query expansions (synonyms, sub-localities, regional terms) and orders sources.
- Geography tiling for Places: split city bounding box into tiles; subdivide a tile when a query returns the page cap.
- Output: `research_tasks` rows with type, input, parent, per-task credit budget.

## 3. Discovery connectors
Return candidates: `{source_key, external_id, name, url?, phone?, address?, geo?, raw_ref}`. Upsert into companies/company_locations as candidates with field_values; send candidate IDs downstream.

## 4. Crawler
1. Frontier: Redis ZSET per domain (`frontier:{domain}`) + global ready set; score = relevance x freshness.
2. Robots: fetch `/robots.txt` (cache 24 h, in Redis); disallow -> `access_restricted` for that path; honour crawl-delay.
3. Politeness: per-domain concurrency 1 (configurable), min delay 1-2 s, global concurrency limit per pool.
4. Target pages per company (Standard depth): homepage, contact, about, team/leadership, careers, sitemap-discovered product/services page. Max 6 pages; Deep: max 15.
5. HTTP fetch: httpx, HTTP/2, 15 s timeout, 5 MB max body, conditional requests with ETag/Last-Modified, honest User-Agent.
6. Browser fallback only if: HTML body text < 500 chars AND scripts indicate SPA. 20 s timeout; block images/media/fonts.
7. Restriction detection (stop, never evade): HTTP 401/403/407/429-with-challenge, CAPTCHA markers, login forms on target page, Cloudflare/challenge interstitials, paywall markers -> `access_status=restricted`, event logged, no retry.
8. SSRF guard: block private/link-local/metadata IPs after DNS resolution; only http/https; limit redirects to 5.
9. Store raw gzip in S3 key `raw/{yyyy}/{mm}/{url_hash}/{fetched_at}.html.gz`; skip storing when `content_hash` unchanged.

## 5. Extraction (cheapest first)
1. `extruct`: JSON-LD, microdata, OpenGraph -> Organization/LocalBusiness fields, sameAs social links, address, telephone, openingHours.
2. Regex/selectors: emails (with de-obfuscation of `[at]`, `(dot)`), `tel:` and phone patterns validated by phonenumbers, `mailto:`, WhatsApp links (`wa.me`), social URL patterns, footer address.
3. Tech detection: fingerprints on HTML, script src, headers, cookies, meta generator, DNS MX/TXT.
4. Page classifier (rules, then small model): contact/about/team/careers/product/blog.
5. LLM extraction ONLY for remaining fields (description, services, products, people on team page, custom columns). Input = trafilatura main text trimmed to token cap; output = JSON schema; every value must cite `evidence_text` that exists in the input (validator rejects otherwise).
Each extracted value -> `FieldValue(entity, field, value, source_id, source_url, raw_document_id, method, derivation, confidence, observed_at)`.

## 6. Normalization
| Field | Rule |
| --- | --- |
| phone | phonenumbers parse with default region from spec/company country -> E.164; type mobile/fixed |
| email | lowercase, strip mailto/params, validate syntax, role account flag (info@, sales@) |
| url / domain | lowercase host, strip www and tracking params, tldextract registrable domain; mark platform domains (wixsite, blogspot, linktr.ee, facebook.com, instagram.com, business.site, etc.) |
| name | trim, collapse whitespace, strip legal suffixes (pvt ltd, private limited, llp, ltd, inc, llc, gmbh) for `normalized_name`; unidecode for matching key |
| address | parse components, country ISO-2, city canonical via geo table; geocode from Places where available |
| social | canonical handle per platform (lowercase, no @, no query) |
| industry | map categories -> internal taxonomy via synonyms table, then embedding similarity, then LLM if unresolved |

## 7. Entity resolution
- Blocking keys: registrable domain (non-platform), phone E.164, email domain, `platform:handle`, geohash7 + first 3 chars of normalized name, registry id.
- Features: domain_equal, phone_equal, name_jw (Jaro-Winkler), name_token_set, address_distance_m, social_crosslink, category_sim, desc_embedding_cos.
- Rule score (MVP): weighted log-likelihood; thresholds: >= 0.92 auto-merge, 0.70-0.92 -> LLM adjudicator (Phase 6; before that -> `match_candidates` pending review), < 0.70 separate.
- Chains/branches: same brand + different addresses = separate location entities under one company when domain is shared; do not merge distinct legal entities.
- Merge: move field_values/contacts/socials to winner, log in `entity_merges`, leads re-pointed; undo supported.
- Person match: same company + normalized name (+nickname map) + title similarity; email/LinkedIn URL exact = strong.

## 8. Verification and confidence
| Check | Method | Status values |
| --- | --- | --- |
| Email | syntax, MX lookup, disposable list, verifier API in batches (no own SMTP probing) | deliverable, risky, catch_all, undeliverable, unknown |
| Phone | phonenumbers validity + type | valid, possible, invalid |
| Website | GET status, redirects, SSL, parked-page detection | active, redirect, parked, down |
| Company exists | registry match, Places business_status, website active, recent activity | confirmed, likely, unverified, closed |
| Social ownership | site links to profile or profile links to site = verified_link; same phone/email in bio = strong; name+city similarity = probable | verified_link, probable, unverified |
| Conflicts | normalized value disagreement across sources | agree, conflict |

Field confidence:
```
conf = 1 - Π_sources (1 - reliability(source, field) * exp(-age_days / half_life(field)))
if method == ai: conf *= 0.6
if unresolved conflict: conf = min(conf, 0.5)
```
Priors: registry 0.95, own website 0.90, Places 0.85, provider 0.75, directory 0.60, ai 0.50. Half-lives (days): phone 365, email 270, address 540, jobs 30, social activity 14, rating 60.
Record status: Verified >= 0.85 · Likely 0.60-0.85 · Unverified < 0.60 · Conflict if any required field conflicts.

## 9. Critic loop
- Rules: required fields from spec missing? filters marked post_filter unresolved? conflicts on required fields? -> create follow-up tasks from the capability map.
- Stop when: all required fields meet `min_confidence`, OR budget exhausted, OR no connector can supply the field (record `unsatisfiable`).

## 10. Enrichment modules
Contract: `name, inputs_required, fields_produced, cost_credits, ttl_days, run(entity) -> FieldValue[]`. Waterfall runs modules cheapest-first until target confidence reached. MVP modules: domain_finder, website_profiler, social_resolver, tech_detector, contact_extractor, email_verifier, registry_matcher, hiring_monitor. Later: firmographic_estimator, people_finder, reviews, news_funding, lookalikes, provider_waterfall (BYOK).

## 11. Scoring
Scoring model JSON (`packages/contracts/schemas/scoring-model.schema.json`): gates, criteria (`field, op, value, points`), `confidence_weighting`, optional `ai_fit_score {enabled, weight, icp_text}`. Output `score 0-100` and `score_breakdown` array. `website_quality` derived from SSL, mobile viewport, response time bucket, content freshness, broken links sample.

## 12. Leads materialization
For each resolved company passing filters: upsert `leads(workspace_id, company_id)`, apply scoring, emit progress, dedupe against existing workspace leads (not charged again).

## Idempotency
- Task idempotency key = `{research_job_id}:{task_type}:{hash(input)}`.
- Usage unit key = `{task_id}:{unit}` so retries never double-charge.
