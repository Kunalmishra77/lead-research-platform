# serp

Search results through **serper.dev** (ADR-0006). Its job in Phase 2 is narrow: find the website
of a business that Google Places knew about but had no `websiteUri` for. Without a website there
is nothing for the Phase 3 crawler to read, and the lead stops at a name and a phone number.

## Calls

| | |
| --- | --- |
| Search | `POST https://google.serper.dev/search` |
| Auth | `X-API-KEY` header |
| Body | `{"q": ..., "num": 1-20, "gl": <country>, "hl": "en"}` |
| Response | `organic[]` (`title`, `link`, `snippet`, `position`), sometimes `knowledgeGraph`, and `credits` |

Verified live on 2026-09-23.

## Cost and limits

- **1 credit per search at `num=10`**, confirmed from the `credits` field the API returns. The
  connector never asks for more than ten: vendors in this category often bill per block of ten,
  and asking for twenty could quietly cost two credits while we meter one. Every response's
  `credits` is compared against what we metered, and a mismatch is logged.
- **2,500 free queries** on a new account, stated on the vendor's landing page.
- **Rate limit 25**, observed in the `x-ratelimit-limit` response header for this account. The
  **window is not stated** — if that 25 is per minute rather than per second, a 15/s limiter is
  36x over it, and the likely answer is a 403 that stops the whole job as `access_restricted`.
  So the limiter is set to the safe reading, 15 per minute, until `x-ratelimit-remaining` has
  been watched across a burst and the window established.

`cost_per_call_micros = 1000` is an **estimate**. serper.dev publishes no per-credit price on any
page reachable without signing in, so the money value of a credit has to be read from the account
dashboard and corrected here. Until then this number is a placeholder, in the same position as
the model prices in `app/ai/config.py`.

## ToS class

**green** — a vendor API used with our own key. The vendor holds the relationship with the search
engine; we never query a search engine directly, which docs/08 and CLAUDE.md both rule out.
A 403 or a 429 is the shared HTTP client's decision, and this connector does not reinterpret it.

## What it stores

URLs, plus the result title (200 chars) and snippet (500 chars) kept as evidence — which is
what docs/08 permits: facts and short evidence snippets. No SERP HTML, no full pages, and the
title is never used as a business name: "Example Dental Studio | Facebook" is a page about a
business, and storing it as a company would invent one.

`sources.retention_days` is null for this source: a URL is a fact, and no licence here requires
us to forget it.

There is **no response cache** yet. Two identical queries in one job are two credits, and the
second is metered like the first, so the money is recorded correctly — but it is money that a
cache would not have spent. Phase 3 should add one keyed on the query; it is tracked in
PROGRESS.md rather than hidden here.

There is also **no job-wide spend ceiling here**, and that one matters more. `cost_cap_micros` is
the whole request's total and callers must not decrement it, so a single call cannot know what is
left; the check in `_query` therefore only refuses a cap smaller than one credit. Since
`resolve_website` runs once per business, a job's serp spend is bounded by its candidate count
rather than by its budget. The fix belongs in the shared client, over the same Redis ledger
`app/ai/spend.py` already uses for model calls, and applies to every connector at once — task
2.11, not a patch here.

## Picking a website: measured, not assumed

The rule is the interesting part, and ADR-0006 has the numbers. Against six real Pune clinics:

- Taking the **first result that is not on a directory list** picked `magicpin.in` and
  `kivihealth.com` for two of them — listing sites that no hand-written exclusion list would
  have contained. The tail of directories has no end.
- Matching **the business's own words against the domain** agreed with Places wherever Places had
  an answer, and declined where it did not.

Then two reviews asked what that rule says about domains it has **never seen**, and the answer
forced a redesign twice. ADR-0006 has all three tables; the short version:

- **Counting how much of the name is in the domain** scored `mosaic.in` a perfect 1.0 for "Om Sai
  Clinic" ("sai" is inside "mosaic") and accepted `apple.com` for "Apple Dental Care".
- **Counting both directions** killed those, and started declining the two commonest name shapes
  in the target market: "Dental Galaxy **Pvt Ltd**" scored 0.50 against its own `dentalgalaxy.in`,
  "**Dr.** Harshal**'s** Dental Clinic" 0.40 against `harshaldental.com`. It still accepted
  `sunpharmacy.in` for "Sun Pharma" (0.82), because a substring test never asks what the leftover
  letters of the domain are.

So `matching.py` now **segments**: the domain has to read, left to right, as the business's words
in order with **nothing left over**. `sunpharma` reads as "sun"+"pharma"; `sunpharmacy` leaves
"cy" and is rejected outright. Words the domain skips are free — "Pvt Ltd", "Clinic", "Dental
Care" — which is exactly what a company drops when it registers a domain. `MIN_CONFIDENCE = 0.6`
then decides on how much of the *distinctive* name the domain used, and below it `resolve_website`
returns **nothing**, a normal answer: about half the businesses tested genuinely have no site.

Measured across all three sets together — the original six, and every false accept and false
reject the two reviews found — **32 of 32 correct, every negative a hard 0.0**. They are
regression tests.

Details that each came from watching it fail:

- Titles and legal forms are removed before anything is measured (`DROPPED_WORDS`). Counting
  "Pvt Ltd" against a candidate is what made the flagship case decline its own website.
- Stripping generic words ("clinic", "dental", "studio") can empty a name — "AO Dentistry" lost
  everything and was missed. `name_tokens` falls back to the full name, and a domain that is the
  whole name scores 1.0 outright so short words still count there.
- A domain that uses one word of a longer name scores **zero** unless it is the whole name.
  "Apple Dental Care" has no claim on `apple.com`, and we cannot tell a claim from a coincidence.
- `registrable_stem` reads the registered name, not the leftmost label, and the directory guard
  reads **every** label. Before that, `in.linkedin.com` — the host LinkedIn serves to Indian
  users — stemmed to "in" and sailed past the guard, and `dentalgalaxy.justdial.com` scored a
  perfect 1.0 as a company's own website.
- Names are ASCII-folded, or "Café Müller" splits into "caf" and "ller" and can never match
  `cafemuller`.
- A search ranks inner pages, so `dentalgalaxy.in/our-team/` came first. `website` is a fact
  about the business, so `homepage_of` reduces it to the origin.

**What it cannot do:** two businesses with the same name in different cities are indistinguishable
to any rule that only reads strings. "Sharma Dental Clinic" in Pune matches a Delhi practice's
`sharmadental.com` perfectly. The city is in the query, so the search ranks with it, but the score
does not know it — that is resolution's problem (docs/06).

A wrong website is worse than none: it sends the crawler to another company and attributes their
phone number and address to this one, with provenance that looks entirely credible. That is why
the rule is precise rather than generous, and why the confidence it yields (0.6 × match) sits
below a Places value and well below anything crawled from the company's own site.

The value is stored as `derivation = derived_pattern`, not `found`: no source states this website,
a rule of ours chose it. Its `source_url` is the **search** we ran, not the site we picked —
pointing the evidence at the answer would prove nothing, and on a wrong pick it would vouch for
the wrong company.

## Fixtures

`tests/fixtures/serp/` carries the API's real shape with invented values: a normal result set, a
page of nothing but directories (the case that decided the design), a knowledge-graph answer whose
panel and top organic result disagree, a snippet containing the words "captcha" and "subscribe to
continue", an empty result, and four distinct error bodies. No test calls the vendor.

The error fixtures are split on purpose. `out_of_credits.json` and `payment_required.json` must
pause the job (`budget_exhausted`); `bad_request.json` and `error_without_detail.json` must fail
the one item (`invalid_input`). Until they were separate files, the one called `bad_request.json`
contained a credit error, so a regression that paused every job over a single malformed query
would have passed green.

The paywall-snippet fixture exists because the shared client scans HTML bodies for exactly those
words. A JSON API body is other people's prose quoted back at us, so it is not scanned; otherwise
a good result would be marked `access_restricted`, which is never retried.
