"""A page turned into field values (task 3.8, CLAUDE.md provenance rule).

Every assertion here is really about provenance: what evidence a value rests on, and whether the
number next to it says so honestly.
"""

from datetime import UTC, datetime

from app.crawl.mapping import (
    CONFIDENCE_LINKED,
    CONFIDENCE_STRUCTURED,
    CONFIDENCE_TEXT,
    SOURCE_KEY,
    field_names,
    merge_pages,
    values_from_page,
)

WHEN = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
PAGE_URL = "https://clinicdelhi.in/contact"

LINKED = """
<html><body>
  <p>Email <a href="mailto:info@clinicdelhi.in">info@clinicdelhi.in</a></p>
  <p>Call <a href="tel:+919876543210">+91 98765 43210</a></p>
  <footer><a href="https://instagram.com/clinicdelhi">Instagram</a></footer>
</body></html>
"""

TEXT_ONLY = """
<html><body>
  <p>Write to info [at] clinicdelhi [dot] in or ring 011 2345 6789.</p>
</body></html>
"""

STRUCTURED = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Dentist","name":"Clinic Delhi",
 "description":"Dental care in South Delhi","telephone":"+91 11 2345 6789",
 "email":"hello@clinicdelhi.in",
 "address":{"@type":"PostalAddress","streetAddress":"12 Main Road",
   "addressLocality":"New Delhi","addressRegion":"Delhi","postalCode":"110048",
   "addressCountry":"IN"},
 "sameAs":["https://www.facebook.com/clinicdelhi"],
 "openingHours":["Mo-Sa 09:00-19:00"],
 "aggregateRating":{"ratingValue":"4.9","reviewCount":"300"}}
</script></head><body>
  <p>Email <a href="mailto:footer@clinicdelhi.in">footer@clinicdelhi.in</a></p>
</body></html>
"""


def by_field(html: str) -> dict[str, object]:
    facts = values_from_page(html, page_url=PAGE_URL, observed_at=WHEN, region="IN")
    return {v.field: v for v in facts.values}


def test_every_value_carries_the_provenance_the_rule_requires() -> None:
    [value, *_] = values_from_page(LINKED, page_url=PAGE_URL, observed_at=WHEN, region="IN").values
    assert value.source_key == SOURCE_KEY
    assert value.source_url == PAGE_URL
    assert value.observed_at == WHEN
    assert value.method == "crawl"
    # Literally present on the page: nothing here infers, so nothing is `ai` or derived.
    assert value.derivation == "found"
    assert 0.0 < value.confidence <= 1.0


def test_a_linked_address_is_believed_more_than_one_in_a_sentence() -> None:
    linked = by_field(LINKED)["email"]
    text = by_field(TEXT_ONLY)["email"]
    assert linked.value == "info@clinicdelhi.in"  # type: ignore[attr-defined]
    assert text.value == "info@clinicdelhi.in"  # type: ignore[attr-defined]
    # Same address, different evidence: a mailto is the author pointing at their own inbox, a
    # string in a paragraph might be a customer's address in a testimonial.
    assert linked.confidence == CONFIDENCE_LINKED  # type: ignore[attr-defined]
    assert text.confidence == CONFIDENCE_TEXT  # type: ignore[attr-defined]


def test_a_decoded_address_is_still_found_not_inferred() -> None:
    # Every character came from the page; only the spelling of @ and . was restored. Marking it
    # inferred would misreport the one thing CLAUDE.md's rule turns on.
    value = by_field(TEXT_ONLY)["email"]
    assert value.derivation == "found"  # type: ignore[attr-defined]
    assert value.method == "crawl"  # type: ignore[attr-defined]


def test_a_phone_is_stored_in_e164_with_its_type() -> None:
    fields = by_field(LINKED)
    assert fields["phone"].value == "+919876543210"  # type: ignore[attr-defined]
    assert fields["phone_type"].value == "mobile"  # type: ignore[attr-defined]


def test_a_social_handle_becomes_its_own_field() -> None:
    assert by_field(LINKED)["instagram"].value == "clinicdelhi"  # type: ignore[attr-defined]


def test_structured_data_outranks_a_footer_on_the_same_page() -> None:
    fields = by_field(STRUCTURED)
    # The schema.org block published hello@; the footer links footer@. The block is the stronger
    # claim, and `add` keeps the first value offered for a field.
    assert fields["email"].value == "hello@clinicdelhi.in"  # type: ignore[attr-defined]
    assert fields["email"].confidence == CONFIDENCE_STRUCTURED  # type: ignore[attr-defined]


def test_structured_data_fills_the_address_fields() -> None:
    fields = by_field(STRUCTURED)
    assert fields["city"].value == "New Delhi"  # type: ignore[attr-defined]
    assert fields["state"].value == "Delhi"  # type: ignore[attr-defined]
    assert fields["postal_code"].value == "110048"  # type: ignore[attr-defined]
    assert fields["country"].value == "IN"  # type: ignore[attr-defined]
    assert "12 Main Road" in str(fields["address"].value)  # type: ignore[attr-defined]


def test_a_structured_telephone_is_normalised_like_a_tel_link() -> None:
    assert by_field(STRUCTURED)["phone"].value == "+911123456789"  # type: ignore[attr-defined]


def test_sameas_is_trusted_over_a_footer_icon() -> None:
    # A footer can link the web agency's Instagram; `sameAs` is the business naming its own.
    fields = by_field(STRUCTURED)
    assert fields["facebook"].value == "clinicdelhi"  # type: ignore[attr-defined]
    assert fields["facebook"].confidence == CONFIDENCE_STRUCTURED  # type: ignore[attr-defined]


def test_a_sites_own_rating_is_not_stored() -> None:
    # A page claiming 4.9 stars is marketing. In the grid it would be indistinguishable from
    # Google's rating, which is a measurement.
    fields = by_field(STRUCTURED)
    assert "rating" not in fields
    assert "review_count" not in fields


def test_opening_hours_survive_as_a_list() -> None:
    assert by_field(STRUCTURED)["opening_hours"].value == [  # type: ignore[attr-defined]
        "Mo-Sa 09:00-19:00"
    ]


def test_a_page_with_nothing_on_it_yields_nothing() -> None:
    facts = values_from_page("<html><body><p>Hello.</p></body></html>", page_url=PAGE_URL)
    assert facts.values == ()
    assert facts.emails_found == 0


def test_the_counts_report_what_was_seen_not_what_was_kept() -> None:
    # Three addresses on the page, one stored, and the page says so: the gap is what Phase 4's
    # multi-value contact model will close.
    html = (
        '<a href="mailto:a@clinicdelhi.in">a</a>'
        '<a href="mailto:b@clinicdelhi.in">b</a>'
        '<a href="mailto:c@clinicdelhi.in">c</a>'
    )
    facts = values_from_page(html, page_url=PAGE_URL, region="IN")
    assert facts.emails_found == 3
    assert len([v for v in facts.values if v.field == "email"]) == 1


def test_merging_pages_keeps_the_best_evidence_for_each_field() -> None:
    homepage = values_from_page(TEXT_ONLY, page_url="https://clinicdelhi.in/", region="IN")
    contact = values_from_page(LINKED, page_url=PAGE_URL, region="IN")
    merged = {v.field: v for v in merge_pages([homepage, contact])}
    # The contact page's mailto beats the homepage's obfuscated text for the same address.
    assert merged["email"].source_url == PAGE_URL
    assert merged["email"].confidence == CONFIDENCE_LINKED
    # And a field only one page had is still there.
    assert merged["instagram"].value == "clinicdelhi"


def test_merging_is_stable_when_two_pages_agree_exactly() -> None:
    first = values_from_page(LINKED, page_url="https://clinicdelhi.in/a", region="IN")
    second = values_from_page(LINKED, page_url="https://clinicdelhi.in/b", region="IN")
    merged = {v.field: v for v in merge_pages([first, second])}
    # A tie goes to the page read first, which is the higher-priority one.
    assert merged["email"].source_url == "https://clinicdelhi.in/a"


def test_field_names_lists_what_a_crawl_covered() -> None:
    facts = values_from_page(LINKED, page_url=PAGE_URL, region="IN")
    assert field_names(facts.values) == ["email", "instagram", "phone", "phone_type"]
