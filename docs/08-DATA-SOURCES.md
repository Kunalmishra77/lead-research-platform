# 08 — Data Sources and Connector Contract

## Source policy (enforced in code)
- `tos_class`: green (official API or clearly permitted), amber (public pages, permitted by robots/ToS, needs care), red (ToS prohibits automated access or requires login).
- Red sources cannot be enabled in production unless `sources.legal_approved = true` (set by admin after legal review). Default: never build red crawlers.
- Never bypass CAPTCHA, login, paywall, bot challenge, robots.txt disallow, or IP blocks. Detect -> stop -> `access_restricted`.
- Prefer official APIs whenever they exist. Respect each API's caching/storage terms (e.g. Google Places content caching rules — store place IDs and refresh details per terms).
- Store facts and short evidence snippets; do not store full articles, full review texts, or images from third-party sites.
- Honest User-Agent with contact URL; per-domain rate limits.
- Verify each provider's current quotas, pricing and terms in official docs at implementation time and record them in the connector README.

## Connector contract (Python)
```python
class BaseConnector(ABC):
    key: ClassVar[str]                  # "google_places"
    tos_class: ClassVar[Literal["green", "amber", "red"]]
    auth: ClassVar[Literal["none", "api_key", "oauth"]]
    fields_provided: ClassVar[set[str]]
    default_ttl_days: ClassVar[int]
    rate_limit: ClassVar[RateLimit]     # requests per window, concurrency
    cost_per_call_micros: ClassVar[int]

    async def search(self, query: DiscoveryQuery, ctx: JobContext) -> list[Candidate]: ...
    async def fetch(self, ref: SourceRef, ctx: JobContext) -> RawResult: ...
    def map(self, raw: RawResult) -> list[FieldValue]: ...   # pure, deterministic, tested
    async def health(self) -> ConnectorHealth: ...
```
- All network calls via `connectors/http_client.py` (metering, rate limiting, retries for transient, restriction detection, tracing).
- Tests use recorded fixtures (`tests/fixtures/<key>/*.json|html`): success, empty, rate-limited, restricted/error.

## Source matrix
| Source | Data | Method | Auth | Class | Phase |
| --- | --- | --- | --- | --- | --- |
| Google Places API (New) | name, category, address, geo, phone, website, hours, rating, review count, business status | Text Search / Nearby / Place Details with field masks | API key + billing | green | 2 |
| SERP API (one vendor) | URLs for websites, social profiles, directory pages, news | Vendor API | API key | green | 2 |
| Company websites | description, services, contacts, socials, team, careers, locations, tech | Own crawler | none | amber (robots-respecting) | 3 |
| Tech detection | technologies | Passive analysis of fetched pages + DNS | none | green | 3 |
| DNS (MX/TXT) | email provider, verification | DNS queries | none | green | 3 |
| UK Companies House | legal entity, status, incorporation, officers | Official API | API key | green | 5 |
| OpenCorporates | legal entities across jurisdictions | Official API | API key (licence terms) | green | 5 |
| SEC EDGAR | US public company filings | Official API | UA header | green | 5 |
| India MCA data | CIN, status, directors | Licensed data vendor (no scraping of MCA portal; no CAPTCHA automation) | vendor key | green via vendor | 11 |
| ATS job boards (Greenhouse, Lever, Ashby, Workable) | open roles, departments, locations | Public board APIs | none/key | green | 5 |
| Careers pages | open roles | Crawler + JobPosting schema.org | none | amber | 5 |
| Email verifier | deliverability | Vendor API | API key | green | 4 |
| YouTube Data API | channel stats | Official API | API key (quota units) | green | 11 |
| Meta Graph API (Business Discovery) | IG business account followers/media counts | Official API, app review | OAuth app | green (if approved) | 11 |
| Instagram / Facebook / Threads / LinkedIn / X pages | URLs and handles only | Found on company sites or in SERP results; no crawling of the platforms | none | green for URL storage only | 3 |
| News (RSS, GDELT, news API vendor) | funding, launches, leadership | APIs/RSS; store headline + link + AI summary | key | green | 8 |
| Review platforms (Trustpilot API etc.) | rating aggregates | Official APIs only | key | green | 11 |
| Directories (Justdial, IndiaMART, Clutch, Yelp...) | listings | Only via official API/partnership or where ToS + robots permit; otherwise skip | varies | usually red | later, per legal review |
| Crunchbase / Tracxn / similar | funding | Licensed API (BYOK) | key | green (licensed) | 11 |
| People data providers (BYOK) | work emails, titles | Licensed API waterfall | key | green (licensed) | 11 |
| CSV upload / CRM pull | user data | Import | OAuth | tenant-private | 4 / 9 |

## Capability map (seed; updated from yield stats)
```yaml
website:        [google_places, serp, registry_matcher]
phone:          [google_places, website]
email:          [website, provider_waterfall]
address:        [google_places, website, companies_house]
socials:        [website, serp]
technologies:   [tech_detector]
employee_band:  [companies_house, provider_waterfall, firmographic_estimator]
founded_year:   [companies_house, opencorporates, website]
hiring:         [ats_boards, careers_page]
rating:         [google_places]
people:         [website_team_page, companies_house_officers, provider_waterfall]
```
