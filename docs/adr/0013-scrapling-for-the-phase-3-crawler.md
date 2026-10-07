# ADR-0013: Scrapling supplies the Phase 3 parser, not the Phase 3 fetcher

- Status: Proposed
- Date: 2026-10-07

## Context

Phase 3 crawls a lead's own website and extracts the things a maps listing never carries: email
addresses, phone numbers, social profile links, technologies, team pages. Today the product has no
emails at all, and `docs/DEMO.md` records "Where are the email addresses?" as the first question a
demo gets. Phase 3 currently specifies httpx + Playwright + protego + extruct, all hand-wired.

Scrapling (0.4.15, BSD-3-Clause, Python >= 3.10 — our workers are 3.12) was proposed as the engine
for that phase. It is a real, widely used library and parts of it would replace plumbing we would
otherwise write. But its dependency set and its headline features are built around evading bot
detection, which this project's compliance rule forbids, so adopting it needs an explicit boundary
rather than a blanket yes or no.

What the evaluation found, by installing it and reading it rather than from its README:

- **Core is small and clean.** `scrapling` alone pulls 7 packages (lxml, cssselect, orjson, tld,
  w3lib, typing_extensions, click). The parser — adaptive element tracking, CSS/XPath, markdown
  conversion — needs nothing else.
- **The fetchers extra pulls two evasion libraries.** `scrapling[fetchers]` installs 22 packages
  including `curl_cffi` (TLS fingerprint impersonation) and `patchright` (a patched Playwright
  built to be undetected). The plain `Fetcher` cannot be imported without `curl_cffi`: its request
  types import from it directly.
- **Evasion is switchable.** `impersonate` and `stealthy_headers` are explicit per-request
  parameters alongside `headers`, so a request can be made that identifies itself honestly.
- **robots.txt lives in the spider layer, not the fetcher.** `robots_txt_obey` is handled in
  `spiders/engine.py`, `spiders/robotstxt.py`, `spiders/spider.py` and
  `spiders/templates/sitemap.py`. A bare `Fetcher.get()` consults nothing. So Scrapling's robots
  support arrives only if we also adopt its crawl engine.

That last point is the fork. Phase 3 task 3.1 is our own Redis crawl frontier with per-domain
politeness and per-pool concurrency, and task 3.2 is our own robots evaluation with protego.
Scrapling's spider engine would replace both — with its own frontier, its own checkpoints and its
own AutoThrottle, none of which know about job budgets, `usage_events`, the cancel flag (ADR-0008)
or the error taxonomy.

## Options considered

1. **Adopt Scrapling whole — spiders, fetchers, parser.**
   Pros: least code to write; robots, throttling, sitemaps and resumable crawls arrive together.
   Cons: its frontier replaces task 3.1, so crawling stops going through the metered client and
   stops honouring the cancel flag and the per-job credit budget — the two things that keep a
   research job from spending without limit. Its crawl state lives in its own checkpoints rather
   than in Redis beside every other job. And the evasion dependencies become load-bearing.

2. **Take the parser only; keep our own fetcher and frontier.**
   Pros: the 7-package core carries no evasion libraries at all, so the forbidden capability is
   not merely unused but absent. Fetching stays in `ConnectorHttpClient`, which already does the
   cancel check before a paid call, the spend reservation, and the restriction classification.
   robots stays task 3.2 with protego directly, which is what Phase 3 already specified.
   Cons: we still write the frontier, the sitemap walk and the page-choosing rules ourselves.

3. **Reject Scrapling; write the extraction layer on lxml + extruct as originally planned.**
   Pros: no new dependency, no boundary to maintain.
   Cons: gives up the one genuinely useful thing — adaptive element tracking, which relocates a
   selector after a site changes its markup. For a crawler pointed at tens of thousands of small
   business sites that break constantly, that is worth having.

## Decision

**Option 2.** Scrapling enters as `scrapling` with no extras: the parser, the adaptive selectors
and the markdown conversion. Fetching, robots, politeness and the frontier stay ours.

Concretely:

- `services/workers/pyproject.toml` depends on `scrapling` (no `[fetchers]`, no `[ai]`), so
  `curl_cffi` and `patchright` are not installed. A reviewer can verify the boundary by reading the
  lockfile rather than by reading our code and trusting it.
- Every HTTP request continues to go through `ConnectorHttpClient`, which is where the cancel check
  (ADR-0008), the `SpendLedger` reservation and the restriction classification already live.
- `StealthyFetcher`, `DynamicFetcher`, `FetcherSession` and the spider templates are not imported.
  Phase 3 task 3.5's browser fetcher stays plain Playwright, which is already in the stack.
- Task 3.4's restriction detector keeps its meaning: a CAPTCHA marker, a login form, a challenge
  page or a paywall marks the target `access_restricted` and the crawler stops. It does not retry,
  and nothing in the codebase tries to get past it.

### What this ADR does not permit, and why that is not negotiable

Scraping Google Search or Google Maps result pages, and scraping Instagram, Facebook, LinkedIn or
X, were both asked for and are both out. `docs/08-DATA-SOURCES.md` already settled this: row 50
stores social **URLs and handles only**, "found on company sites or in SERP results; no crawling of
the platforms", and line 5 reads "Default: never build red crawlers". Google's maps data has a
green, official route that is already built — the Places API.

The reason is not that a file says so. Those services put CAPTCHAs, bot challenges and login walls
in place deliberately; getting past them is unauthorised access to someone else's system, and the
liability lands on whoever ships the product, then on their customers. A separate and quieter cost:
a value obtained by defeating a bot check cannot carry honest provenance, and provenance is the one
thing this product has that a list-broker does not.

If a social platform's data is wanted, the route is the official one — Meta Graph API Business
Discovery, already in `docs/08` row 49 for Phase 11, which needs an app review and returns real
numbers under terms we can point at.

## Consequences

**Easier.** Extraction gets adaptive selectors, so a markup change on a crawled site degrades
instead of breaking. Markdown conversion gives the AI layer clean page text without another
dependency. The dependency is small and its licence (BSD-3-Clause) is compatible.

**Harder.** Tasks 3.1, 3.2, 3.3 and 3.7 are still ours to write; this ADR saves parsing work, not
crawling work. And the boundary needs guarding: the obvious next step for anyone reading
Scrapling's docs is `StealthyFetcher`, which is one import away and would be a compliance breach
rather than a style disagreement. A test asserting that `curl_cffi` and `patchright` are absent
from the workers environment is cheap and makes the breach fail loudly instead of silently.

**Follow-up.** `docs/02-TECH-STACK.md` gains the dependency with the no-extras note.
`phases/phase-03-extraction.md` task 3.8 names Scrapling for parsing alongside extruct for
structured data. The free-source expansion that was asked for in the same breath — OpenStreetMap
Overpass for area-wise discovery at zero cost, using the city bounding boxes already seeded in
`db/seeds/data/geo-in.json` — is a separate decision and gets its own ADR.
