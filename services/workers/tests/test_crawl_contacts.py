"""Contacts off a page (docs/06 sections 5.2 and 6, task 3.8).

Phase 3's acceptance asks for email, phone and social-link precision of 95% or better, so these
tests are mostly about what is *not* extracted: a GSTIN is not a phone, a testimonial's address is
still an address but a placeholder is not, and a Facebook share button is not the company's page.
"""

import pytest

from app.crawl.contacts import (
    ROLE_LOCAL_PARTS,
    SocialPlatform,
    contact_bundle,
    decode_entities,
    emails_from,
    phones_from,
    preferred,
    socials_from,
)

CONTACT_PAGE = """
<html><body>
  <p>Call us on <a href="tel:+91 98765 43210">+91 98765 43210</a> or 011 2345 6789.</p>
  <p>Email <a href="mailto:info@clinic.co.in">info@clinic.co.in</a></p>
  <p>Dr Sharma: priya.sharma [at] clinic [dot] co [dot] in</p>
  <p>GSTIN 07AABCU9603R1ZX</p>
  <footer>
    <a href="https://www.facebook.com/clinicdelhi">Facebook</a>
    <a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
    <a href="https://instagram.com/@clinicdelhi/">Instagram</a>
    <a href="https://www.linkedin.com/company/clinic-delhi/">LinkedIn</a>
    <a href="https://wa.me/919876543210">WhatsApp</a>
  </footer>
</body></html>
"""


def test_entities_and_escapes_are_decoded_before_matching() -> None:
    assert decode_entities("info&#64;clinic&#46;com") == "info@clinic.com"
    assert decode_entities("info%40clinic.com") == "info@clinic.com"


def test_a_mailto_address_is_found_and_marked_as_linked() -> None:
    [first, *_] = emails_from(CONTACT_PAGE)
    assert first.value == "info@clinic.co.in"
    assert first.from_link
    assert first.role_account


def test_an_obfuscated_address_is_decoded_and_marked() -> None:
    addresses = {e.value: e for e in emails_from(CONTACT_PAGE)}
    person = addresses["priya.sharma@clinic.co.in"]
    assert person.deobfuscated
    assert not person.from_link
    # Not a role account: this is a named person, which docs/10 treats as personal data.
    assert not person.role_account


@pytest.mark.parametrize(
    "written",
    [
        "info (at) clinic (dot) co (dot) in",
        "info [at] clinic [dot] co [dot] in",
        "info{at}clinic{dot}co{dot}in",
        "info&#64;clinic.co.in",
        "info%40clinic.co.in",
        "info at clinic dot co dot in",
    ],
)
def test_the_obfuscations_sites_actually_use_are_undone(written: str) -> None:
    found = emails_from(f"<html><body><p>{written}</p></body></html>")
    assert [e.value for e in found] == ["info@clinic.co.in"]


@pytest.mark.parametrize(
    "text",
    [
        "you@example.com",
        "your-name@yourdomain.com",
        "someone@domain.com",
        "name@email.com",
        "hello@example.org",
    ],
)
def test_placeholder_addresses_are_not_contacts(text: str) -> None:
    # A theme's sample text is on thousands of sites. Storing it would put the same fake address
    # on thousands of leads.
    assert emails_from(f"<p>{text}</p>") == []


def test_an_address_in_a_sentence_does_not_swallow_the_preceding_word() -> None:
    found = emails_from("<p>Please write to info@clinic.co.in today.</p>")
    assert [e.value for e in found] == ["info@clinic.co.in"]


def test_role_accounts_are_recognised() -> None:
    assert "info" in ROLE_LOCAL_PARTS
    assert emails_from("<p>careers@clinic.co.in</p>")[0].role_account
    assert not emails_from("<p>rohit@clinic.co.in</p>")[0].role_account


def test_a_tel_link_becomes_e164_with_its_line_type() -> None:
    phones = {p.value: p for p in phones_from(CONTACT_PAGE, region="IN")}
    mobile = phones["+919876543210"]
    assert mobile.from_link
    assert mobile.line_type == "mobile"


def test_a_local_number_in_text_needs_a_region_to_be_read() -> None:
    page = "<p>011 2345 6789</p>"
    assert phones_from(page, region="IN") != []
    # Without a region a local number is not a number anywhere. Guessing one would put a Delhi
    # landline on a lead in another country.
    assert phones_from(page) == []


def test_a_gstin_is_not_a_phone_number() -> None:
    values = [p.value for p in phones_from(CONTACT_PAGE, region="IN")]
    assert all("07AABCU" not in v for v in values)
    assert "+919876543210" in values


@pytest.mark.parametrize(
    "text",
    [
        "<p>PIN code 110048</p>",
        "<p>Invoice 2024000123456</p>",
        "<p>Reg. No 1234567890123</p>",
        "<p>Established 1998 2001 2015</p>",
    ],
)
def test_numbers_that_are_not_phones_are_left_alone(text: str) -> None:
    assert phones_from(text, region="IN") == []


def test_a_whatsapp_link_is_both_a_phone_and_a_social() -> None:
    phones = [
        p.value for p in phones_from('<a href="https://wa.me/919876543210">chat</a>', region="IN")
    ]
    assert phones == ["+919876543210"]
    socials = socials_from('<a href="https://wa.me/919876543210">chat</a>')
    assert socials[0].platform is SocialPlatform.WHATSAPP
    assert socials[0].handle == "919876543210"


def test_social_handles_are_canonical() -> None:
    by_platform = {s.platform: s for s in socials_from(CONTACT_PAGE)}
    assert by_platform[SocialPlatform.FACEBOOK].handle == "clinicdelhi"
    assert by_platform[SocialPlatform.INSTAGRAM].handle == "clinicdelhi"  # @ and slash removed
    assert by_platform[SocialPlatform.LINKEDIN].handle == "company/clinic-delhi"


def test_a_share_button_is_not_the_company_page() -> None:
    # Every site with a share widget links to facebook.com/sharer. Storing that as the company's
    # Facebook page would be wrong on most leads that have one.
    handles = [s.handle for s in socials_from(CONTACT_PAGE)]
    assert "sharer" not in handles
    assert socials_from('<a href="https://twitter.com/intent/tweet?text=hi">Tweet</a>') == []


def test_a_personal_linkedin_profile_keeps_its_shape() -> None:
    [social] = socials_from('<a href="https://www.linkedin.com/in/priya-sharma/">Priya</a>')
    assert social.handle == "in/priya-sharma"


def test_a_bare_linkedin_link_is_not_a_profile() -> None:
    assert socials_from('<a href="https://www.linkedin.com/">LinkedIn</a>') == []


def test_one_mailto_can_carry_several_addresses() -> None:
    found = emails_from('<a href="mailto:a@clinic.co.in,b@clinic.co.in?subject=Hi">Mail</a>')
    assert [e.value for e in found] == ["a@clinic.co.in", "b@clinic.co.in"]
    assert all(e.from_link for e in found)


def test_an_address_in_both_a_link_and_the_text_is_kept_once_as_linked() -> None:
    found = emails_from('<a href="mailto:info@clinic.co.in">info@clinic.co.in</a>')
    assert len(found) == 1
    assert found[0].from_link


def test_the_bundle_parses_once_and_returns_all_three() -> None:
    emails, phones, socials = contact_bundle(CONTACT_PAGE, region="IN")
    assert {e.value for e in emails} == {"info@clinic.co.in", "priya.sharma@clinic.co.in"}
    assert "+919876543210" in {p.value for p in phones}
    assert {s.platform for s in socials} >= {
        SocialPlatform.FACEBOOK,
        SocialPlatform.INSTAGRAM,
        SocialPlatform.LINKEDIN,
        SocialPlatform.WHATSAPP,
    }


def test_preferred_puts_a_linked_value_first() -> None:
    emails, phones, _ = contact_bundle(CONTACT_PAGE, region="IN")
    assert preferred(emails).value == "info@clinic.co.in"  # type: ignore[union-attr]
    assert preferred(phones).from_link  # type: ignore[union-attr]
    assert preferred([]) is None


def test_an_empty_page_yields_nothing_rather_than_raising() -> None:
    assert contact_bundle("", region="IN") == ([], [], [])
    assert contact_bundle(b"\xff\xfe not html", region="IN") == ([], [], [])


def test_a_number_written_with_an_en_dash_is_still_a_number() -> None:
    # Word processors and CMS editors turn "011-2345-6789" into en dashes. Folding them in one
    # place keeps every pattern in this module ASCII.
    assert phones_from("<p>011\u20132345\u20136789</p>", region="IN") != []


@pytest.mark.parametrize("tld", ["test", "invalid", "localhost"])
def test_an_address_at_a_reserved_tld_is_not_a_contact(tld: str) -> None:
    # RFC 2606 / RFC 6761 names exist so they can never resolve, so nobody's inbox is at one.
    # Found while writing the crawl handler's test against a `.test` site: the validator was
    # right and the test was wrong.
    assert emails_from(f"<p>info@clinic.{tld}</p>") == []
