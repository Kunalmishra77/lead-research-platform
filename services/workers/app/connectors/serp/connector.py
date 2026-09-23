"""SERP connector (serper.dev) — finding the URLs Places did not have (docs/08, ADR-0006).

Places gives us a business; often it does not give us a website, and without one there is nothing
for Phase 3 to crawl. This asks a search engine, through a vendor, and answers with a candidate
rather than a claim: `resolve_website` returns a URL only when the domain looks like the business
it belongs to, and returns nothing the rest of the time.

Nothing here queries a search engine directly. The vendor holds that relationship (ADR-0006), and
every response goes through the shared client, which owns retries, blocks and metering.
"""

import json
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import quote_plus

from app.connectors.base import BaseConnector
from app.connectors.http_client import CallCost, ConnectorHttpClient
from app.connectors.serp.matching import best_website, host_of, is_aggregator
from app.connectors.serp.models import SearchResponse
from app.connectors.types import (
    AuthKind,
    Candidate,
    DiscoveryQuery,
    FieldValue,
    RateLimit,
    RawResult,
    SourceRef,
    TosClass,
)
from app.jobs.errors import BudgetExhaustedError, InvalidInputError, ParseFailedError
from app.metering.context import CallContext

SEARCH_URL = "https://google.serper.dev/search"

#: One credit per search, confirmed from the API's own `credits` field. The money value of a
#: credit is NOT published on any page reachable without signing in (ADR-0006), so this is an
#: estimate and is flagged as such in the README until someone reads the dashboard.
SEARCH_COST_MICROS = 1_000

#: What the vendor should report having charged. A mismatch is logged rather than assumed away.
EXPECTED_CREDITS_PER_SEARCH = 1

#: Ten, and only ten. "1 credit per search" was observed at `num=10`; vendors in this category
#: commonly bill per block of ten, and serper publishes no pricing we can read (ADR-0006). Asking
#: for twenty might quietly cost two credits and meter one, so the ceiling stays where the
#: evidence is until a twenty-result response has been recorded and its `credits` value read.
MAX_RESULTS = 10
DEFAULT_RESULTS = MAX_RESULTS

#: How much to trust a website we picked by name similarity. Below Places' 0.85 on purpose: this
#: is inference from a search page, and a crawled value from the company's own site must win.
WEBSITE_CONFIDENCE = 0.6


class SerpConnector(BaseConnector):
    key: ClassVar[str] = "serp"
    tos_class: ClassVar[TosClass] = "green"
    auth: ClassVar[AuthKind] = "api_key"
    #: URLs only. Names, addresses and phone numbers on a search page belong to whoever published
    #: them; we take the link and let the crawler read the company's own site (docs/08).
    fields_provided: ClassVar[frozenset[str]] = frozenset({"website"})
    #: Not a discovery source. It answers "what is this business's site", not "which businesses
    #: are there", and the planner must not reach for it to find companies (docs/08).
    discovers: ClassVar[bool] = False
    default_ttl_days: ClassVar[int] = 14
    #: `x-ratelimit-limit: 25` was observed, with no stated window. Read as per minute, which
    #: is the safe reading: if it is really per second we are merely slow, and if it is per
    #: minute a per-second limiter would earn a 403 that stops the job (README).
    rate_limit: ClassVar[RateLimit] = RateLimit(requests=15, per_seconds=60.0, concurrency=2)
    cost_per_call_micros: ClassVar[int] = SEARCH_COST_MICROS

    def __init__(self, client: ConnectorHttpClient, api_key: str) -> None:
        if not api_key:
            raise InvalidInputError("serp needs an API key")
        self._client = client
        self._api_key = api_key

    async def search(self, query: DiscoveryQuery, ctx: CallContext) -> list[Candidate]:
        """Sites that came up for a query, one per host.

        This is not discovery in the sense Places means it, which is why `discovers` is false: a
        search page says a page exists, not that a business does. A candidate's `name` is the
        host, never the page title — "Example Dental Studio | Facebook" is a page about a
        business, and storing it as a company name would invent a company (docs/06).

        Which means **no caller may resolve these into companies.** A hostname is not a name
        anybody stated, and `Candidate` has nowhere to record that it was derived rather than
        found, so a company built from one would carry a name no source ever gave it under
        provenance pointing at a page that does not contain it. They are leads to a URL, and the
        planner is kept away from them by `discovers = False`.
        """
        response = await self._query(
            query.text, ctx, country=query.country, language=query.language
        )
        observed = datetime.now(UTC)
        seen: dict[str, Candidate] = {}
        for result in response.organic:
            host = host_of(result.link)
            if not host or is_aggregator(result.link):
                # A listing site is not a business; `matching.py` refuses them for the same
                # reason and this path must not be the softer one.
                continue
            seen.setdefault(
                host,
                Candidate(
                    source_key=self.key,
                    external_id=host,
                    name=host,
                    url=result.link,
                    source_url=result.link,
                    observed_at=observed,
                    # Evidence, kept short (docs/08). The page title lives here rather than
                    # standing in for a name nobody stated.
                    raw={
                        "position": result.position,
                        "title": result.title[:200],
                        "snippet": result.snippet[:500],
                    },
                ),
            )
        return list(seen.values())

    async def resolve_website(
        self,
        name: str,
        ctx: CallContext,
        *,
        city: str | None = None,
        country: str | None = None,
        language: str = "en",
    ) -> FieldValue | None:
        """The business's own website, or nothing.

        Nothing is a real answer: about half the businesses in the ADR-0006 test have no site.
        Returning a plausible-looking directory page instead would send the crawler to somebody
        else's business and attribute their phone number to this one.
        """
        if not name.strip():
            # Checked here rather than on the assembled query: quoting a blank name produces
            # `""`, which is not empty and would have been paid for.
            raise InvalidInputError("serp was asked to find a website for nothing")
        terms = f'"{name}" {city}'.strip() if city else f'"{name}"'
        response = await self._query(terms, ctx, country=country, language=language)

        links = [r.link for r in response.organic if r.link]
        if response.knowledge_graph and response.knowledge_graph.website:
            # Google's own panel for the entity: worth ranking alongside the rest, not above it.
            links.insert(0, response.knowledge_graph.website)

        url, score = best_website(name, links)
        if url is None:
            ctx.log.info(
                "serp found no site that looks like the business",
                business=name,
                results=len(links),
                best_score=round(score, 2),
            )
            return None
        ctx.log.info(
            "serp picked a website",
            business=name,
            website=url,
            match=round(score, 2),
            results=len(links),
        )
        return FieldValue(
            entity_type="company",
            field="website",
            value=url,
            source_key=self.key,
            # Where a human can check this: the search we ran, not the answer we chose. Pointing
            # the evidence at the value would prove nothing, and on a wrong pick it would send a
            # reviewer to the wrong company's homepage under a badge saying we saw it there.
            source_url=search_permalink(terms),
            observed_at=datetime.now(UTC),
            method="api",
            # Nobody stated this. A rule derived it from the name and the domain, and the UI
            # draws that differently from a value found in a source (docs/09, CLAUDE.md).
            derivation="derived_pattern",
            # Scaled by how well the domain matched, so a marginal pick reads as marginal.
            confidence=round(WEBSITE_CONFIDENCE * score, 3),
        )

    async def fetch(self, ref: SourceRef, ctx: CallContext) -> RawResult:
        """There is no record here to fetch, and pretending otherwise would cost a credit.

        `RawResult` is what docs/06 stores as a raw document. A whole search page is exactly the
        thing ADR-0006 says we do not keep, so this refuses rather than paying for a payload it
        must then throw away. `resolve_website` is the way in.
        """
        raise InvalidInputError("serp has no fetchable record; use resolve_website (ADR-0006)")

    def map(self, raw: RawResult) -> list[FieldValue]:
        """A raw search page carries no values on its own.

        The useful thing here needs the business's name to compare against, which a `RawResult`
        does not carry, so `resolve_website` is the real entry point and this stays empty rather
        than inventing an answer from the first link.
        """
        try:
            SearchResponse.model_validate_json(raw.body)
        except ValueError as exc:
            raise ParseFailedError(f"serp returned an unreadable response: {exc}") from exc
        return []

    async def _query(
        self,
        terms: str,
        ctx: CallContext,
        *,
        country: str | None = None,
        language: str = "en",
    ) -> SearchResponse:
        if not terms.strip():
            raise InvalidInputError("serp was asked to search for nothing")
        if 0 < ctx.cost_cap_micros < SEARCH_COST_MICROS:
            # Only the degenerate case, and deliberately not more: `cost_cap_micros` is the whole
            # request's total and callers must not decrement it (metering/context.py), so nothing
            # here can know what is left. `resolve_website` is called once per business, so the
            # running total across a fan-out is the one that matters and it has to be kept
            # somewhere both calls can see -- the shared client, over the same Redis ledger
            # `app/ai/spend.py` already uses for models. That is task 2.11, for every connector
            # at once; until then a job's serp spend is bounded by its candidate count, not by
            # its budget.
            raise BudgetExhaustedError(
                f"serp needs {SEARCH_COST_MICROS} micros, cap is {ctx.cost_cap_micros}"
            )
        body: dict[str, Any] = {"q": terms, "num": MAX_RESULTS, "hl": language}
        if country:
            body["gl"] = country.lower()
        response = await self._client.post(
            SEARCH_URL,
            cost=CallCost(meter=self.meter, cost_micros=SEARCH_COST_MICROS),
            ctx=ctx,
            headers=self._headers(),
            json=body,
        )
        _raise_for_api_error(response.status_code, response.content)
        try:
            parsed = SearchResponse.model_validate_json(response.content)
        except ValueError as exc:
            raise ParseFailedError(f"serp returned an unreadable response: {exc}") from exc

        if parsed.credits is not None and parsed.credits != EXPECTED_CREDITS_PER_SEARCH:
            # The vendor tells us what it charged on every response. Until someone reads the
            # dashboard this is the only way a price change reaches us before the invoice does.
            ctx.log.warning(
                "serp charged more than we meter",
                charged=parsed.credits,
                metered=EXPECTED_CREDITS_PER_SEARCH,
                cost_micros=SEARCH_COST_MICROS,
            )
        return parsed

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self._api_key, "Content-Type": "application/json"}


def search_permalink(terms: str) -> str:
    """Where a person can see what we saw. Read by humans, never fetched by us (ADR-0006)."""
    return f"https://www.google.com/search?q={quote_plus(terms)}"


def _raise_for_api_error(status: int, body: bytes) -> None:
    """A 4xx the shared client passed on: only this module knows what the vendor meant by it.

    403 and 429 never reach here — the client treats them as a block and a rate limit, which is
    the only correct reading and not something a connector may reinterpret (docs/08).
    """
    if status < 400:
        return
    try:
        parsed = json.loads(body)
        message = parsed.get("message") or parsed.get("error") if isinstance(parsed, dict) else None
    except ValueError:
        message = None
    detail = str(message or "no detail")
    if status == 402 or (status == 400 and _mentions_credit(detail)):
        # An exhausted account is not a bad request: the same call succeeds after a top-up, and
        # the job should pause and ask rather than repeat the mistake on every candidate. The
        # word test is confined to the two statuses that can mean it; read on any 4xx it would
        # turn a 404 whose message happens to say "quota" into a job-wide pause.
        raise BudgetExhaustedError(f"serp account is out of credit ({detail})")
    raise InvalidInputError(f"serp rejected the request (http {status}: {detail})")


def _mentions_credit(detail: str) -> bool:
    lower = detail.lower()
    return "credit" in lower or "quota" in lower


__all__ = ["SEARCH_COST_MICROS", "SEARCH_URL", "SerpConnector", "search_permalink"]
