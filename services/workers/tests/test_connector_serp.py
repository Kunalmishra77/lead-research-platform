"""The SERP connector (docs/08, ADR-0006).

Replayed from `tests/fixtures/serp/`; the vendor is never called. The cases that matter most are
the ones the ADR's live test exposed: a page of listing sites must yield no website at all, and a
domain that looks like the business must be found even when its name is mostly generic words.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import pytest
import respx

from app.connectors.factory import build_connectors
from app.connectors.http_client import ConnectorHttpClient
from app.connectors.serp import (
    SerpConnector,
    best_website,
    core_words,
    domain_match,
    homepage_of,
    is_aggregator,
    name_tokens,
    registrable_stem,
)
from app.connectors.serp.connector import (
    SEARCH_COST_MICROS,
    SEARCH_URL,
    search_permalink,
)
from app.connectors.serp.matching import MIN_CONFIDENCE
from app.connectors.types import DiscoveryQuery, RateLimit, RawResult, SourceRef
from app.jobs.errors import (
    BudgetExhaustedError,
    InvalidInputError,
    ParseFailedError,
    RateLimitedError,
)
from tests.ai_support import settings
from tests.connector_support import USER_AGENT, RecordingUsage, make_ctx
from tests.fixtures import load_json

API_KEY = "serper-test-not-a-real-key"


def make_connector() -> tuple[SerpConnector, RecordingUsage]:
    usage = RecordingUsage()
    client = ConnectorHttpClient(
        source_key="serp",
        rate_limit=RateLimit(requests=100, per_seconds=1.0, concurrency=4),
        user_agent=USER_AGENT,
        usage=usage,
        client=httpx.AsyncClient(timeout=5.0, headers={"user-agent": USER_AGENT}),
    )
    return SerpConnector(client, API_KEY), usage


def route(case: str, status: int = 200) -> Any:
    return respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(status, json=load_json("serp", case))
    )


@respx.mock
async def test_it_finds_the_business_own_site_among_the_listings() -> None:
    sent = route("search_success")
    connector, usage = make_connector()
    try:
        value = await connector.resolve_website("Example Dental Studio", make_ctx(), city="Pune")
    finally:
        await connector._client.aclose()

    assert value is not None
    assert value.value == "https://example-dental.test/"
    assert value.field == "website"
    assert value.method == "api"
    # Nobody stated this: a rule derived it, and the UI draws that differently (docs/09).
    assert value.derivation == "derived_pattern"
    # Evidence points at the search we ran, not at the answer we chose. Pointing it at the
    # value would prove nothing and, on a wrong pick, would vouch for the wrong company.
    assert value.source_url == search_permalink('"Example Dental Studio" Pune')
    assert value.source_url.startswith("https://")
    assert value.value not in value.source_url
    # Below a crawled value's confidence: this is inference from a search page (ADR-0006).
    assert 0 < value.confidence <= 0.6

    body = json.loads(sent.calls.last.request.content)
    assert body["q"] == '"Example Dental Studio" Pune'
    assert body["num"] == 10
    assert sent.calls.last.request.headers["x-api-key"] == API_KEY
    assert usage.calls[0]["cost_micros"] == SEARCH_COST_MICROS


@respx.mock
async def test_a_page_of_directories_yields_no_website_at_all() -> None:
    route("search_only_directories")
    connector, _ = make_connector()
    try:
        value = await connector.resolve_website("Second Example Clinic", make_ctx(), city="Pune")
    finally:
        await connector._client.aclose()

    # This is the case that decided the design: magicpin and kivihealth were picked by a
    # "first non-directory result" rule in the live test, and both are listing sites. A wrong
    # website sends the crawler to another company and attributes its phone number to this one.
    assert value is None


@respx.mock
async def test_googles_own_panel_is_ranked_with_the_rest_not_above_it() -> None:
    route("search_with_knowledge_graph")
    connector, _ = make_connector()
    try:
        value = await connector.resolve_website(
            "Third Example Orthodontics", make_ctx(), city="Pune"
        )
    finally:
        await connector._client.aclose()

    # The panel offers a Justdial page and the organic result is the real domain. The panel is
    # ranked with the rest, not above it, so the domain wins.
    assert value is not None
    assert value.value == "https://third-example.test/"


@respx.mock
async def test_no_results_is_a_normal_answer() -> None:
    route("search_empty")
    connector, _ = make_connector()
    try:
        value = await connector.resolve_website("Nonexistent Business", make_ctx())
    finally:
        await connector._client.aclose()

    # About half the businesses in the ADR-0006 test genuinely have no site of their own.
    assert value is None


@respx.mock
async def test_organic_results_become_one_candidate_per_site() -> None:
    route("search_success")
    connector, _ = make_connector()
    try:
        candidates = await connector.search(
            DiscoveryQuery(text="dental clinics in Pune", country="IN", max_results=10),
            make_ctx(),
        )
    finally:
        await connector._client.aclose()

    hosts = [c.external_id for c in candidates]
    # Listing sites are dropped here as firmly as in the matching rule, and a page title is
    # never a company name: "Example Dental Studio | Facebook" would invent a company.
    assert hosts == ["example-dental.test"]
    assert candidates[0].name == "example-dental.test"
    assert candidates[0].raw["title"].startswith("Example Dental Studio")
    assert all(c.observed_at is not None for c in candidates)
    assert candidates[0].raw["position"] == 1


@respx.mock
async def test_the_planner_is_not_offered_serp_as_a_way_to_find_companies() -> None:
    redis = fakeredis.FakeAsyncRedis()
    registry, _ = build_connectors(settings=settings(SERPER_API_KEY=API_KEY), redis=redis)

    # It answers "what is this business's site", not "which businesses are there". Being the
    # cheapest, a plain cheapest-first sort would otherwise put it ahead of the sources that
    # can actually discover something (docs/08).
    assert [c.key for c in registry.providing("website")] == ["serp"]
    assert registry.providing("website", for_discovery=True) == []
    await redis.aclose()


@respx.mock
async def test_a_snippet_about_paywalls_is_not_a_compliance_verdict() -> None:
    route("search_with_paywall_snippet")
    connector, _ = make_connector()
    try:
        value = await connector.resolve_website("Example Dental Studio", make_ctx(), city="Pune")
    finally:
        await connector._client.aclose()

    # The shared detector scans HTML bodies for "captcha" and "subscribe to continue". A JSON
    # API body is other people's prose, and treating it as a block would mark a good result
    # access_restricted, which is never retried.
    assert value is not None
    assert value.value == "https://example-dental.test/"


@respx.mock
async def test_an_empty_query_is_refused_before_it_is_paid_for() -> None:
    sent = route("search_success")
    connector, usage = make_connector()
    try:
        with pytest.raises(InvalidInputError, match="for nothing"):
            await connector.resolve_website("   ", make_ctx())
    finally:
        await connector._client.aclose()

    assert sent.call_count == 0
    assert usage.calls == []


@respx.mock
async def test_a_quota_failure_is_a_rate_limit() -> None:
    route("rate_limited", status=429)
    connector, _ = make_connector()
    try:
        with pytest.raises(RateLimitedError):
            await connector.resolve_website("Example Dental Studio", make_ctx())
    finally:
        await connector._client.aclose()


@respx.mock
async def test_a_bad_key_is_a_block_the_connector_does_not_reinterpret() -> None:
    route("unauthorized", status=403)
    connector, _ = make_connector()
    try:
        # 403 is the shared client's decision, and no connector may work around it (docs/08).
        with pytest.raises(Exception, match="http_403"):
            await connector.resolve_website("Example Dental Studio", make_ctx())
    finally:
        await connector._client.aclose()


@respx.mock
@pytest.mark.parametrize(("case", "status"), [("out_of_credits", 400), ("payment_required", 402)])
async def test_running_out_of_credits_pauses_rather_than_looking_like_a_bad_query(
    case: str, status: int
) -> None:
    route(case, status=status)
    connector, usage = make_connector()
    try:
        # The same request succeeds after a top-up, so this is not invalid input: it is the
        # job's cue to stop and ask (docs/01 error taxonomy).
        with pytest.raises(BudgetExhaustedError, match="out of credit"):
            await connector.resolve_website("Example Dental Studio", make_ctx())
    finally:
        await connector._client.aclose()

    # A rejected request is not a billed one.
    assert usage.calls == []


@respx.mock
async def test_a_genuinely_bad_request_fails_the_item_instead_of_pausing_the_job() -> None:
    route("bad_request", status=400)
    connector, _ = make_connector()
    try:
        # Until this fixture existed the suite could not tell the two 400s apart: the file called
        # bad_request.json contained a credit error, so a regression that paused every job on one
        # malformed query would have passed green.
        with pytest.raises(InvalidInputError, match="Missing required parameter"):
            await connector.resolve_website("Example Dental Studio", make_ctx())
    finally:
        await connector._client.aclose()


@respx.mock
async def test_a_404_that_happens_to_mention_quota_is_not_a_budget_problem() -> None:
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(404, json={"message": "quota endpoint not found"})
    )
    connector, _ = make_connector()
    try:
        # Read on every 4xx, the word test would pause the whole job over one dead endpoint.
        with pytest.raises(InvalidInputError, match="http 404"):
            await connector.resolve_website("Example Dental Studio", make_ctx())
    finally:
        await connector._client.aclose()


@respx.mock
async def test_an_error_the_vendor_does_not_explain_still_says_what_happened() -> None:
    route("error_without_detail", status=400)
    connector, _ = make_connector()
    try:
        with pytest.raises(InvalidInputError, match="no detail"):
            await connector.resolve_website("Example Dental Studio", make_ctx())
    finally:
        await connector._client.aclose()


@respx.mock
async def test_a_rejected_request_is_not_metered() -> None:
    route("unauthorized", status=403)
    connector, usage = make_connector()
    try:
        with pytest.raises(Exception, match="http_403"):
            await connector.resolve_website("Example Dental Studio", make_ctx())
    finally:
        await connector._client.aclose()
    assert usage.calls == []


@respx.mock
async def test_a_call_that_cannot_fit_the_budget_is_refused_before_it_is_made() -> None:
    sent = route("search_success")
    connector, usage = make_connector()
    try:
        with pytest.raises(BudgetExhaustedError):
            await connector.resolve_website(
                "Example Dental Studio", make_ctx(cost_cap_micros=SEARCH_COST_MICROS - 1)
            )
    finally:
        await connector._client.aclose()

    assert sent.call_count == 0
    assert usage.calls == []


@respx.mock
async def test_the_vendor_charging_more_than_we_meter_is_noticed() -> None:
    body = load_json("serp", "search_success")
    body["credits"] = 2
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json=body))
    warnings: list[tuple[str, dict[str, Any]]] = []

    class Recorder:
        def warning(self, event: str, **kw: Any) -> None:
            warnings.append((event, kw))

        def info(self, event: str, **kw: Any) -> None: ...

    ctx = make_ctx()
    connector, _ = make_connector()
    try:
        await connector.resolve_website(
            "Example Dental Studio", replace(ctx, log=Recorder()), city="Pune"
        )
    finally:
        await connector._client.aclose()

    # The vendor reports what it charged on every response. Until someone reads the dashboard
    # this is the only way a price change reaches us before the invoice does.
    assert any("charged more" in event for event, _ in warnings)


@respx.mock
async def test_the_request_never_asks_for_more_than_the_priced_page() -> None:
    sent = route("search_success")
    connector, _ = make_connector()
    try:
        await connector.search(
            DiscoveryQuery(text="dental clinics", country="IN", max_results=100, language="hi"),
            make_ctx(),
        )
    finally:
        await connector._client.aclose()

    body = json.loads(sent.calls.last.request.content)
    # "1 credit" was only ever observed at ten results, and the caller's appetite must not
    # quietly double the bill.
    assert body["num"] == 10
    assert body["gl"] == "in"
    assert body["hl"] == "hi"


async def test_there_is_no_record_to_fetch_and_it_does_not_pay_to_find_out() -> None:
    connector, usage = make_connector()
    try:
        # A whole search page is exactly what ADR-0006 says is not kept, so this refuses rather
        # than spending a credit on a payload it would have to throw away.
        with pytest.raises(InvalidInputError, match="no fetchable record"):
            await connector.fetch(SourceRef(source_key="serp", external_id="q"), make_ctx())
    finally:
        await connector._client.aclose()
    assert usage.calls == []


def test_a_valid_search_page_still_maps_to_no_values() -> None:
    connector, _ = make_connector()
    raw = RawResult(
        ref=SourceRef(source_key="serp", external_id="q"),
        status=200,
        body=json.dumps(load_json("serp", "search_success")).encode(),
        content_type="application/json",
        fetched_at=datetime.now(UTC),
        url=SEARCH_URL,
    )

    # The useful answer needs the business's name to compare against, which a RawResult does
    # not carry. Returning the first link instead would be inventing one.
    assert connector.map(raw) == []


def test_unreadable_json_is_a_parse_failure() -> None:
    connector, _ = make_connector()
    raw = RawResult(
        ref=SourceRef(source_key="serp", external_id="q"),
        status=200,
        body=b"{not json",
        content_type="application/json",
        fetched_at=datetime.now(UTC),
        url=SEARCH_URL,
    )

    with pytest.raises(ParseFailedError):
        connector.map(raw)


def test_a_connector_without_a_key_refuses_to_exist() -> None:
    client = ConnectorHttpClient(
        source_key="serp",
        rate_limit=RateLimit(requests=1),
        user_agent=USER_AGENT,
        client=httpx.AsyncClient(),
    )
    with pytest.raises(InvalidInputError, match="needs an API key"):
        SerpConnector(client, "")


# ---------------------------------------------------------------- the matching rule
#
# Every case below is a real shape, and the positives and negatives are one set on purpose: two
# earlier versions of this rule each passed a suite made only of the cases it was designed from.
# The first scored "Om Sai Clinic" -> mosaic.in a perfect 1.0. The second fixed that and began
# declining "Dental Galaxy Pvt Ltd" -> dentalgalaxy.in, the very case the ADR was built on, while
# still accepting sunpharmacy.in for "Sun Pharma". ADR-0006 has both tables.


def test_a_name_made_only_of_generic_words_still_has_something_to_match_on() -> None:
    # "AO Dentistry" lost its whole name to stop-words in the ADR-0006 test and was missed.
    assert name_tokens("AO Dentistry") == {"ao", "dentistry"}
    assert name_tokens("Dental Galaxy") == {"galaxy"}
    assert name_tokens("32Smiles") == {"32smiles"}


def test_a_legal_suffix_is_not_part_of_the_name() -> None:
    # Places returns "... Pvt Ltd" routinely and no one puts it in a domain. Counting it made the
    # ADR's flagship case decline its own website at 0.50.
    assert core_words("Dental Galaxy Pvt Ltd") == ["dental", "galaxy"]
    assert core_words("Dr. Harshal's Dental Clinic") == ["harshal", "s", "dental", "clinic"]


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("Dental Galaxy", "https://dentalgalaxy.in/"),
        ("32Smiles", "https://32smiles.co.in/"),
        ("AO Dentistry", "https://www.aodentistry.com/"),
        ("Blue Tokai Coffee", "https://bluetokaicoffee.com/about"),
        # The shapes the second rule declined. These are the common ones in the target market:
        # a legal suffix, an honorific and a possessive, and a name whose tail is generic.
        ("Dental Galaxy Pvt Ltd", "https://dentalgalaxy.in/"),
        ("Dr. Harshal's Dental Clinic", "https://harshaldental.com/"),
        ("Dr. Sanap's Clinic", "https://sanapclinic.com/"),
        ("32 Smiles Dental Care", "https://32smiles.co.in/"),
        ("A-1 Dental", "https://a-1dental.com/"),
        ("Sun Pharma", "https://sunpharma.com/"),
        ("Ram Krishna Sweets", "https://ramkrishnasweets.in/"),
    ],
)
def test_a_domain_made_of_the_business_name_is_a_match(name: str, url: str) -> None:
    assert domain_match(name, url) >= MIN_CONFIDENCE


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("Dr. Sanap's Clinic", "https://magicpin.in/Pune/Shivaji-Nagar/Clinic/Sanap/"),
        ("Dr. Harshal's dental Clinic", "https://kivihealth.com/harshal"),
        ("Example Dental Studio", "https://www.facebook.com/exampledentalstudio/"),
        ("Example Dental Studio", "https://www.justdial.com/Pune/Example-Dental-Studio"),
        ("Dental Galaxy", "https://www.practo.com/pune/clinic/dental-galaxy"),
        ("Dental Galaxy", "https://www.google.co.in/search?q=dental+galaxy"),
    ],
)
def test_a_page_about_the_business_is_not_the_business(name: str, url: str) -> None:
    # Listing sites put the business name in the path, never in the domain. That difference is
    # the whole rule: a hand-written list of directories would never be complete.
    assert domain_match(name, url) < MIN_CONFIDENCE


@pytest.mark.parametrize(
    ("name", "url"),
    [
        # Each of these scored above the threshold under one of the two earlier rules.
        ("Om Sai Clinic", "https://mosaic.in/"),
        ("Apple Dental Care", "https://www.apple.com/"),
        ("AO Dentistry", "https://chaos.com/"),
        ("Sun Pharma", "https://sunglasses-hut.test/"),
        # A single character of difference. "sunpharmacy" is a chemist, not the pharmaceutical
        # company, and the second rule gave it 0.818 because a substring test never asks what
        # the leftover letters of the domain are.
        ("Sun Pharma", "https://sunpharmacy.in/"),
        ("Ortho Care", "https://orthocareers.com/"),
        ("Smile Smiles", "https://smiles.com/"),
    ],
)
def test_a_domain_the_name_cannot_spell_out_is_not_a_match(name: str, url: str) -> None:
    # The domain has to read as the business's words with nothing left over. These leave
    # something over, or use one word of several, and a wrong website sends the crawler to
    # another company and attributes their phone number to this one.
    assert domain_match(name, url) == 0.0


@pytest.mark.parametrize(
    "url",
    [
        # The host LinkedIn serves to Indian users, our primary market. The earlier version read
        # the leftmost label, so this stemmed to "in" and walked straight past the guard.
        "https://in.linkedin.com/company/example-dental-studio",
        "https://m.facebook.com/exampledentalstudio/",
        "https://maps.google.com/?q=example+dental+studio",
        "https://www.justdial.com/Pune/Example-Dental-Studio",
    ],
)
def test_a_subdomain_does_not_smuggle_a_directory_past_the_guard(url: str) -> None:
    assert is_aggregator(url)
    assert domain_match("Example Dental Studio", url) == 0.0


def test_a_business_named_subdomain_of_a_directory_belongs_to_the_directory() -> None:
    # dentalgalaxy.justdial.com scored a perfect 1.0 as the company's own website, because both
    # the name match and the aggregator guard were reading the same wrong label.
    assert registrable_stem("https://dentalgalaxy.justdial.com/") == "justdial"
    assert registrable_stem("https://32smiles.co.in/") == "32smiles"
    assert registrable_stem("https://example-dental.test/") == "exampledental"
    assert domain_match("Dental Galaxy", "https://dentalgalaxy.justdial.com/") == 0.0
    assert domain_match("Example Dental", "https://exampledental.blogspot.com/") == 0.0


def test_an_accented_name_keeps_its_words() -> None:
    # Splitting on [^a-z0-9] alone turned "Café Müller" into "caf" and "ller", so a business
    # with an accent in its name could never match its own domain.
    assert domain_match("Café Müller", "https://cafemuller.test/") == 1.0


def test_a_tie_goes_to_whatever_the_search_ranked_higher() -> None:
    first, score = best_website(
        "Dental Galaxy", ["https://dentalgalaxy.in/", "https://dentalgalaxy.com/"]
    )
    # Both score 1.0; the order the search engine chose is more meaningful than alphabetical.
    assert first == "https://dentalgalaxy.in/"
    assert score == 1.0


def test_nothing_here_raises_on_input_it_was_not_expecting() -> None:
    # These reach the rule from a third-party payload, so a crash is a job failure, not a bug
    # report. Every one must simply decline.
    for url in ["", "not a url", "https://", "https://a/", "//example.test/", "https://.../"]:
        assert domain_match("Dental Galaxy", url) == 0.0
    for name in ["", "   ", "!!! ???", "北京 公司"]:
        assert domain_match(name, "https://dentalgalaxy.in/") == 0.0


def test_the_website_is_the_site_not_the_page_we_landed_on() -> None:
    # A search ranks inner pages: dentalgalaxy.in/our-team/ outranked the homepage in the live
    # check. `website` is a fact about the business, so it is the origin.
    assert homepage_of("https://dentalgalaxy.in/our-team/") == "https://dentalgalaxy.in/"
    assert homepage_of("https://example.test/a/b?c=d#e") == "https://example.test/"
    assert homepage_of("https://example.test/") == "https://example.test/"
    # Nothing to normalise, nothing invented.
    assert homepage_of("not a url") == "not a url"


def test_the_best_candidate_is_chosen_and_a_weak_field_is_declined() -> None:
    links = [
        "https://www.justdial.com/Pune/Dental-Galaxy",
        "https://dentalgalaxy.in/",
        "https://www.facebook.com/dentalgalaxy",
    ]
    url, score = best_website("Dental Galaxy", links)
    assert url == "https://dentalgalaxy.in/"
    assert score == 1.0

    nothing, weak = best_website("Dr. Sanap's Clinic", ["https://magicpin.in/x", "https://y.test"])
    assert nothing is None
    assert weak < MIN_CONFIDENCE


def test_the_registry_offers_serp_only_when_a_key_is_configured() -> None:
    redis = fakeredis.FakeAsyncRedis()
    with_key, clients = build_connectors(settings=settings(SERPER_API_KEY=API_KEY), redis=redis)
    without, no_clients = build_connectors(settings=settings(), redis=redis)

    assert "serp" in with_key.keys
    assert [c.key for c in with_key.providing("website")] == ["serp"]
    assert len(clients) == 1
    assert without.keys == []
    assert no_clients == []
