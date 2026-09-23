# ADR-0006: SERP vendor is serper.dev, and a SERP result is a candidate, not a fact

- Status: Accepted
- Date: 2026-09-23

## Context

Google Places gives us a business and, often, its website. Often is not always: in a live search
for dental clinics in Pune, 3 of 8 results had no `websiteUri`. Without a website there is nothing
for Phase 3 to crawl, so those leads stop at a name and a phone number.

docs/08 allocates one SERP vendor to Phase 2 for exactly this — "URLs for websites, social
profiles, directory pages, news" — and asks that the vendor be chosen after a small cost/quality
test, recorded here.

## The test

Six real businesses from that Places search, including the ones Places had no website for. Each
was queried as `"<name>" <city>`, and two ways of picking a website from the ten organic results
were compared against what Places itself held, where it held anything.

| Business | First non-directory result | Domain-similarity | Places had |
| --- | --- | --- | --- |
| Dr. Sanap's Clinic | `magicpin.in` | *(declined)* | — |
| Dr. Harshal's dental Clinic | `kivihealth.com` | *(declined)* | — |
| 32Smiles | `32smiles.co.in` | `32smiles.co.in` | `32smiles.co.in` |
| AO Dentistry | `aodentistry.com` | *(declined)* | `aodentistry.com` |
| Dental Galaxy | `dentalgalaxy.in` | `dentalgalaxy.in` | `dentalgalaxy.in` |
| Deccan Dental Clinic | — | *(declined)* | — |

What that shows, and it is the reason this ADR exists:

- **Taking the first non-directory result is wrong about 40% of the time.** `magicpin.in` and
  `kivihealth.com` are listing sites that no hand-written exclusion list would have contained;
  the long tail of directories is effectively infinite.
- **Matching the business's own words against the domain is precise but shy** *(on these six —
  see below, where it proved not to be precise at all off them)*. It agreed with
  Places wherever Places had an answer, and declined rather than guess where it did not. It also
  missed `aodentistry.com`, because stripping generic words ("dental", "dentistry") from "AO
  Dentistry" left nothing to match on — fixed in the connector by falling back to the full name
  when the stripped form is empty.
- A wrong website is worse than none. It sends the crawler to another company and attributes that
  company's phone number and address to this one, with provenance that looks entirely credible.

Cost of the whole test: 6 credits of the 2,500 free.

## The matching rule, corrected twice

The rule above was written against six businesses that all had an obvious answer — the domain was
the name, or the page was plainly a directory. Two reviews asked the question the test could not:
what does it say about a domain it has never seen? Both times the answer was bad enough to force a
redesign, and the second time it was bad in the opposite direction from the first.

**First rule — how many of the business's words appear in the domain.** A substring test. It never
asks what the *rest* of the domain is, so:

| Business | Domain stem | Score | |
| --- | --- | --- | --- |
| Om Sai Clinic | `mosaic` | **1.00** | "sai" is inside "mosaic" |
| Apple Dental Care | `apple` | **0.50** | |
| AO Dentistry | `chaos` | **0.50** | "ao" is inside "chaos" |
| Sun Pharma | `sunglasseshut` | **0.50** | |

Every one cleared the 0.5 threshold, and an unrelated domain scored a **perfect** 1.00.

**Second rule — the weaker of two coverages**, name→domain and domain→name, at 0.6. It killed
those four. It also broke the cases the ADR was built on, because the denominator counted words
that can never appear in a domain:

| Business | Its own domain | Score | |
| --- | --- | --- | --- |
| Dental Galaxy **Pvt Ltd** | `dentalgalaxy` | **0.50** | declined — and Places returns names in this shape routinely |
| **Dr.** Harshal**'s** Dental Clinic | `harshaldental` | **0.40** | declined |
| **Dr.** Sanap**'s** Clinic | `sanapclinic` | **0.50** | declined |
| 32 Smiles **Dental Care** | `32smiles` | **0.25** | declined |

and it still accepted domains that merely *contain* the name, because a substring test was still
underneath it:

| Business | Unrelated domain | Score | |
| --- | --- | --- | --- |
| Sun Pharma | `sunpharmacy` | **0.82** | a chemist, not the pharmaceutical company |
| Ortho Care | `orthocareers` | **0.75** | a jobs board; "care" ⊂ "careers" |
| Smile Smiles | `smiles` | **1.00** | overlapping matches double-counted |
| Dental Galaxy | `dentalgalaxy.justdial.com` | **1.00** | a directory, read as the company's own site |

**Third rule — segmentation, and it is the one that shipped.** The domain must be readable, left
to right, as the business's words in order, with **nothing left over**. Words it skips are free;
characters it cannot account for are fatal. `sunpharma` reads as "sun"+"pharma"; `sunpharmacy`
leaves "cy" and is rejected outright, not narrowly. Titles and legal forms (`Dr`, `Pvt`, `Ltd`)
are removed before anything is measured. The score is then how much of the *distinctive* part of
the name the domain used, and a domain using one word of a longer name scores zero unless it is
the whole name — which is what keeps `apple.com` away from "Apple Dental Care".

Two structural bugs were fixed with it, both from reading the leftmost label of the host as the
domain: `in.linkedin.com` (the host LinkedIn serves to Indian users) stemmed to `"in"` and walked
past the directory guard, and `dentalgalaxy.justdial.com` scored 1.00 as a company website. The
stem is now the registrable name, and the guard reads every label.

Measured on all three sets at once — the original six, the first rule's four false accepts, the
second rule's four false rejects and four false accepts, plus the subdomain cases: **32 of 32
correct, and every negative scores a hard 0.0** rather than a near miss. They are regression tests
in `tests/test_connector_serp.py`.

**What it still cannot do.** Two businesses with the same name in different cities are
indistinguishable to any rule that only reads strings: "Sharma Dental Clinic" in Pune matches a
Delhi practice's `sharmadental.com` perfectly. The city is in the query, so the search engine ranks
with it, but the score does not know it. That is resolution's problem (docs/06), and it is part of
why a value from here is capped at 0.6 confidence.

The lasting lesson is about the test, not the rule: a matching rule validated only on the cases it
was designed from measures nothing. Both broken versions passed a green suite.

Cost of the whole exercise: 6 credits of the 2,500 free, all in the original live test.

## Options considered

1. **serper.dev.** Google results through a vendor API; the owner supplied a key. Verified live:
   `POST https://google.serper.dev/search`, `X-API-KEY` header, 1 credit per search, 2,500 free
   queries, and an observed rate limit of 25 (`x-ratelimit-limit: 25`).
2. **SerpAPI, ScaleSERP, Bright Data and similar.** Comparable products. Not tested: each needs
   its own account and key, and none is available here. Their prices are higher than serper's
   published positioning, but this ADR should not pretend to a comparison that was not run.
3. **Query a search engine ourselves.** Ruled out by docs/08 and CLAUDE.md: automated querying of
   a search engine is against its terms, and we do not build sources that need terms bypassed.

## Decision

**serper.dev**, with the vendor boundary drawn deliberately: we buy results from a vendor that
takes on the relationship with the search engine. We never query a search engine directly.

And, more importantly than the vendor: **a SERP result is a candidate, not a fact.** The connector
returns a website with a confidence derived from how well the domain matches the business name,
and declines when nothing matches well. Resolution (docs/06) decides what to do with it; a value
that came from a search result never outranks one crawled from the company's own site.

Storage: URLs and short evidence snippets only, per docs/08. No full result pages, no cached SERP
HTML.

## Consequences

- `cost_per_call_micros` is **unverified**. serper.dev does not publish per-credit pricing on any
  page reachable without signing in; 1 credit per search and the free allowance are confirmed from
  the API and the landing page. The constant carries an estimate flagged in the connector README,
  like the model prices in `app/ai/config.py`, and must be corrected from the account dashboard.
- The rate limit is set below the observed 25, which is itself an observation of one account's
  headers rather than a documented figure.
- The matching rule is the connector's real interface, more than the vendor is. Any change to it
  needs every table above re-run, positives and negatives together — that is exactly the discipline
  whose absence let two broken versions through.
- `MULTI_LABEL_SUFFIXES` is a hand-written stand-in for the Public Suffix List, covering the
  geographies we sell to. An unlisted suffix costs a conservative decline, never a wrong answer,
  but a source operating outside those geographies should bring a real PSL with it.
- A declined website is a normal outcome and must not read as a failure: roughly half the
  businesses in the test genuinely have no site. The connector returns nothing rather than a
  low-confidence guess.
- If the vendor is ever changed, the ADR to write is about the same test on the same six
  businesses: the interface (`SerpConnector.search`, `resolve_website`) is vendor-shaped only in
  `models.py` and one URL.
