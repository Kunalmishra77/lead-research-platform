"""Structured data (docs/06 section 5.1, task 3.8).

The cheapest extraction there is, when a site has filled it in. Most of these tests are about
picking the right node: a page carries several, and reading the first one names the company after
its breadcrumb trail.
"""

import json

from app.crawl.structured import (
    BUSINESS_TYPES,
    Address,
    business_from,
    extract,
    json_ld_blocks,
    rank_of,
)


def page(*blocks: object, body: str = "") -> str:
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks
    )
    return f"<html><head>{scripts}</head><body>{body}</body></html>"


DENTIST = {
    "@context": "https://schema.org",
    "@type": "Dentist",
    "name": "Clinic Delhi",
    "description": "Dental care in South Delhi",
    "url": "https://clinic.example/",
    "telephone": "+91 98765 43210",
    "email": "mailto:info@clinic.example",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "12 Main Road",
        "addressLocality": "New Delhi",
        "addressRegion": "Delhi",
        "postalCode": "110048",
        "addressCountry": "IN",
    },
    "sameAs": ["https://www.facebook.com/clinicdelhi", "https://instagram.com/clinicdelhi"],
    "openingHours": ["Mo-Sa 09:00-19:00"],
    "logo": "https://clinic.example/logo.png",
    "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.8", "reviewCount": "214"},
}

BREADCRUMBS = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "name": "Home > Services > Implants",
    "itemListElement": [],
}


def test_a_localbusiness_block_gives_everything_without_a_single_regex() -> None:
    found = business_from(page(DENTIST))
    assert found is not None
    assert found.schema_type == "Dentist"
    assert found.name == "Clinic Delhi"
    assert found.telephones == ("+91 98765 43210",)
    assert found.emails == ("info@clinic.example",)  # the mailto: prefix is dropped
    assert found.address == Address("12 Main Road", "New Delhi", "Delhi", "110048", "IN")
    assert found.same_as == (
        "https://www.facebook.com/clinicdelhi",
        "https://instagram.com/clinicdelhi",
    )
    assert found.opening_hours == ("Mo-Sa 09:00-19:00",)
    assert found.rating == 4.8
    assert found.review_count == 214


def test_the_breadcrumb_trail_is_not_the_company_name() -> None:
    # The reason nodes are ranked instead of taken in order.
    found = business_from(page(BREADCRUMBS, DENTIST))
    assert found is not None
    assert found.name == "Clinic Delhi"


def test_a_more_specific_type_wins_over_organization() -> None:
    publisher = {"@type": "Organization", "name": "Some Web Agency"}
    found = business_from(page(publisher, DENTIST))
    assert found is not None
    assert found.schema_type == "Dentist"


def test_a_yoast_graph_is_read_through() -> None:
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": "clinic.example"},
            {"@type": "WebPage", "name": "Contact"},
            DENTIST,
        ],
    }
    found = business_from(page(graph))
    assert found is not None
    assert found.name == "Clinic Delhi"


def test_a_type_given_as_a_url_is_still_recognised() -> None:
    found = business_from(page({**DENTIST, "@type": "http://schema.org/LocalBusiness"}))
    assert found is not None
    assert found.schema_type == "LocalBusiness"


def test_a_list_of_types_takes_the_most_specific() -> None:
    found = business_from(page({**DENTIST, "@type": ["Organization", "Dentist"]}))
    assert found is not None
    assert rank_of({"@type": ["Organization", "Dentist"]}) == BUSINESS_TYPES.index("Dentist")
    assert found.name == "Clinic Delhi"


def test_a_page_with_only_a_website_block_yields_no_business() -> None:
    assert business_from(page({"@type": "WebSite", "name": "clinic.example"})) is None


def test_a_product_block_is_not_the_business() -> None:
    # A shop's product pages each carry one, and none of them is the company.
    assert business_from(page({"@type": "Product", "name": "Teeth whitening"})) is None


def test_opengraph_is_a_fallback_and_never_beats_a_business_block() -> None:
    og = (
        '<meta property="og:site_name" content="Clinic Delhi"/>'
        '<meta property="og:description" content="Dental care"/>'
        '<meta property="og:url" content="https://clinic.example/"/>'
    )
    html = f"<html><head>{og}</head><body></body></html>"
    found = business_from(html)
    assert found is not None
    assert found.schema_type == "OpenGraph"
    assert found.name == "Clinic Delhi"

    block = f'<script type="application/ld+json">{json.dumps(DENTIST)}</script>'
    better = business_from(f"<html><head>{og}{block}</head><body></body></html>")
    assert better is not None
    assert better.schema_type == "Dentist"


def test_microdata_is_read_too() -> None:
    # Older small-business sites mark up with itemprop, and they are much of this market.
    html = """
    <div itemscope itemtype="http://schema.org/LocalBusiness">
      <span itemprop="name">Clinic Delhi</span>
      <span itemprop="telephone">+919876543210</span>
      <div itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
        <span itemprop="streetAddress">12 Main Road</span>
        <span itemprop="addressLocality">New Delhi</span>
      </div>
    </div>
    """
    found = business_from(html)
    assert found is not None
    assert found.name == "Clinic Delhi"
    assert found.telephones == ("+919876543210",)
    assert found.address.locality == "New Delhi"


def test_opening_hours_given_as_a_specification_are_flattened() -> None:
    node = {
        **DENTIST,
        "openingHours": None,
        "openingHoursSpecification": [
            {
                "@type": "OpeningHoursSpecification",
                "dayOfWeek": ["https://schema.org/Monday", "https://schema.org/Tuesday"],
                "opens": "09:00",
                "closes": "19:00",
            }
        ],
    }
    found = business_from(page(node))
    assert found is not None
    assert found.opening_hours == ("Mo,Tu 09:00-19:00",)


def test_an_address_published_as_one_string_is_kept_whole() -> None:
    # Splitting it on commas invents components. A real parser is task 3.12's job.
    found = business_from(page({**DENTIST, "address": "12 Main Road, New Delhi 110048"}))
    assert found is not None
    assert found.address.street == "12 Main Road, New Delhi 110048"
    assert found.address.locality is None


def test_a_rating_on_another_scale_is_refused() -> None:
    out_of_ten = {**DENTIST, "aggregateRating": {"ratingValue": "9.2", "reviewCount": "10"}}
    found = business_from(page(out_of_ten))
    assert found is not None
    assert found.rating is None
    # The count is still real even when the scale is not ours.
    assert found.review_count == 10


def test_broken_json_ld_costs_that_block_and_not_the_page() -> None:
    good = f'<script type="application/ld+json">{json.dumps(DENTIST)}</script>'
    html = (
        '<html><head><script type="application/ld+json">{ not json </script>'
        f"{good}</head><body></body></html>"
    )
    found = business_from(html)
    assert found is not None
    assert found.name == "Clinic Delhi"
    assert json_ld_blocks(html) == [DENTIST]


def test_an_empty_or_unparseable_page_yields_nothing_rather_than_raising() -> None:
    assert extract("") == []
    assert business_from("") is None
    assert business_from(b"\xff\xfe not html at all") is None


def test_an_empty_address_knows_it_is_empty() -> None:
    assert Address().is_empty
    assert not Address(locality="Delhi").is_empty
