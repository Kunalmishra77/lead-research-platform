# google_places

Google Places API (New). The Phase 2 discovery source: it answers "which businesses of this kind
are in this area", with enough detail to resolve each one and a website to crawl later.

Everything below was verified against Google's official documentation on **2026-09-23**. Check it
again before changing the field masks or the cost table — the masks are the price at this API.

## Calls

| | |
| --- | --- |
| Text Search | `POST https://places.googleapis.com/v1/places:searchText` |
| Place Details | `GET https://places.googleapis.com/v1/places/{PLACE_ID}` |
| Auth | `X-Goog-Api-Key` header, API key with billing enabled |
| Fields | `X-Goog-FieldMask` header (note: no dash between "Field" and "Mask"). There is no default; a request without a mask is rejected |
| Paging | `pageSize` 1–20, `pageToken`/`nextPageToken`, **60 results maximum per query across all pages** |

## Cost

Priced per SKU, decided by the field mask, and **billed at the highest tier any requested field
belongs to**. Both of our masks ask for a website and a phone number, which are Enterprise
fields, so both calls are Enterprise.

| SKU | Price / 1000 | Free per month | Ours |
| --- | --- | --- | --- |
| Text Search Enterprise | $35.00 | 1,000 | **the search** |
| Place Details Enterprise | $20.00 | 1,000 | **the details call** |
| Text Search Pro / Place Details Pro | $32.00 / $17.00 | 5,000 | — |
| Essentials (IDs only) | $0.00 | unlimited | id refresh |

Enterprise fields, verified against Google's field-to-SKU tables on 2026-09-23:
`nationalPhoneNumber`, `internationalPhoneNumber`, `websiteUri`, `rating`, `userRatingCount`,
`regularOpeningHours`. Everything else in our masks (`displayName`, `formattedAddress`,
`addressComponents`, `location`, `types`, `primaryType`, `businessStatus`, `googleMapsUri`) is
Pro or below.

`cost_per_call_micros` is therefore 35,000 for a search and 20,000 for a details call, and
`test_the_field_masks_are_priced_where_the_cost_constants_say` pins the two together.

Keeping the Enterprise fields in the *search* mask is deliberate. Dropping them would save $3
per 1000 searches and cost $20 per 1000 details calls, because the only other way to learn
whether a business has a website is to ask for the place in full.

Refreshing a stale place ID is free: a Details call whose mask contains only `id`.

## Rate limits

Google does not publish a default QPM; the quota is **per method, per project**, and the real
number is in the Cloud Console under APIs & Services → Places API (New) → Quotas. Until someone
reads it for this project, the connector is deliberately conservative:

```
RateLimit(requests=10, per_seconds=1.0, concurrency=4)
```

Raise it only against the number shown in the console for the project in use.

## Terms: what may be kept, and for how long

This is the part that shapes the connector. ADR-0011 has the decision; the rules are:

- **Place IDs** are "exempt from the caching restrictions" and may be stored indefinitely. Google
  recommends refreshing an ID older than 12 months.
- **Latitude and longitude** from the Places API may be cached "for up to 30 consecutive calendar
  days, after which Customer must delete the cached latitude and longitude values".
- **Everything else** (name, address, phone, website, rating, hours, business status) is Google
  Maps Content under §3.2.3(b): "Customer will not cache Google Maps Content except as expressly
  permitted under the Maps Service Specific Terms."
- **Attribution** is required wherever Places content is shown to a user, visible and unmodified.

So: `retention_days = 30` on the source row and a sweeper that deletes on it, `default_ttl_days
= 30` for freshness, place IDs living on as the external id on each candidate, a search cache
that expires itself on the same clock, and Google credited wherever this data appears.

## ToS class

**green** — an official API used as documented, with a key we pay for. Nothing here evades a
CAPTCHA, a login or a rate limit; a 403 or a 429 stops the connector by way of the shared HTTP
client, which is the only place that decides what a blocked response means.

## Fields provided

`name`, `category`, `address`, `city`, `state`, `country`, `postal_code`, `geo`, `phone`,
`website`, `google_maps_url`, `rating`, `review_count`, `business_status`, `opening_hours`.

Not collected: reviews and photos (docs/08 forbids storing third-party review text and images),
and `openNow`, which is true only at the instant of the call and is not a fact about a business.

## Tiling

A query returns at most 60 results however large the area, and says nothing about what it left
out. `tiling.py` covers a bounding box with tiles of at most 15 km, and a tile that comes back
full is split into quarters and searched again (twice at most, to bound the spend). A city with
900 matching businesses is therefore found; a single city-wide query would have quietly returned
60 of them.

## Turning it on

`GOOGLE_PLACES_ENABLED` defaults to **false** and the connector is not registered without it.
Set it deliberately: this source writes content that must be deleted on a clock, and that should
be someone's decision rather than a side effect of setting a key.

What keeps the clock:

- `app.sweep_expired_field_values` (migration 0017) deletes values past
  `observed_at + sources.retention_days`, hourly via pg_cron. `google_places` is seeded with
  `retention_days = 30`.
- The search cache needs no sweeper: its Redis keys expire on the same 30-day clock.
- Raw Places bodies must never be written to `raw_documents`/S3, which nothing sweeps.

## Fixtures

`tests/fixtures/google_places/` holds the API's **real shape with invented values** — business
names, phones, addresses and place IDs are all made up.

That is deliberate, and not only about personal data: committing real Places content to git would
itself be storage beyond the 30 days the terms allow, in a place no sweeper can reach. The shapes
were captured from live calls on 2026-09-23 (a Text Search for dental clinics in Pune, a Details
call for one of the results, and a 400 from a deliberately invalid field mask) and every value
was replaced before anything was written to the repository.

To re-record after an API change: make the call by hand, compare the shape against `models.py`,
and update the fixtures by editing values — never by pasting a live response.
