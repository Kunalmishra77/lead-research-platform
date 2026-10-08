"""Choosing which pages to read (docs/06 section 4.4, task 3.7).

Pure rules: HTML and XML in, a short list out. The budget is six pages on a Standard crawl, so
every test here is really about what those six are spent on.
"""

import pytest

from app.crawl.discovery import (
    MAX_PAGES_DEEP,
    MAX_PAGES_QUICK,
    MAX_PAGES_STANDARD,
    Candidate,
    PageRole,
    canonical,
    choose_pages,
    classify,
    is_crawlable,
    links_from,
    page_budget,
    same_site,
    sitemap_urls,
)

SITE = "https://clinic.example/"

HOMEPAGE = b"""
<html><head><base href="https://clinic.example/"></head><body>
  <nav>
    <a href="/">Home</a>
    <a href="/about-us">About Us</a>
    <a href="/our-team">Our Team</a>
    <a href="/treatments">Treatments</a>
    <a href="/cdn-cgi/l/email-protection">Email us</a>
    <a href="/careers/">Careers</a>
    <a href="/blog/2026/09/a-post">A post</a>
    <a href="/wp-login.php">Login</a>
    <a href="brochure.pdf">Download brochure</a>
    <a href="https://www.facebook.com/clinic">Facebook</a>
    <a href="#top">Back to top</a>
    <a href="javascript:void(0)">Menu</a>
    <a>no href</a>
  </nav>
</body></html>
"""


def test_canonical_drops_what_does_not_change_the_page() -> None:
    assert canonical("https://Clinic.Example/About/?utm_source=x#team") == (
        "https://clinic.example/About"
    )
    assert canonical("https://clinic.example/") == "https://clinic.example/"
    assert canonical("https://clinic.example/a?b=1&utm_medium=x&c=2") == (
        "https://clinic.example/a?b=1&c=2"
    )


def test_canonical_keeps_path_case_because_servers_do() -> None:
    # Folding the path would merge /Team with /team on the servers where they differ.
    assert canonical("https://clinic.example/Team") != canonical("https://clinic.example/team")


def test_same_site_is_by_registrable_domain_not_exact_host() -> None:
    assert same_site("https://www.clinic.example/a", SITE)
    assert same_site("https://blog.clinic.example/a", SITE)
    assert not same_site("https://www.facebook.com/clinic", SITE)
    assert not same_site("https://clinic.example.evil.test/a", SITE)


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("/contact", PageRole.CONTACT),
        ("/contact-us/", PageRole.CONTACT),
        ("/enquiry", PageRole.CONTACT),
        ("/about-us", PageRole.ABOUT),
        ("/who-we-are", PageRole.ABOUT),
        ("/our-team", PageRole.TEAM),
        ("/doctors", PageRole.TEAM),
        ("/treatments", PageRole.SERVICES),
        ("/what-we-do", PageRole.SERVICES),
        ("/careers", PageRole.CAREERS),
        ("/jobs/", PageRole.CAREERS),
    ],
)
def test_a_path_suggests_a_role(path: str, role: PageRole) -> None:
    assert classify(f"https://clinic.example{path}") is role


def test_anchor_text_beats_an_opaque_url() -> None:
    # The case this rule exists for: Cloudflare's email obfuscation, where the URL says nothing
    # and the link text says everything.
    assert classify("https://clinic.example/cdn-cgi/l/email-protection", "Email us") is None
    assert classify("https://clinic.example/x7f2", "Contact Us") is PageRole.CONTACT
    assert classify("https://clinic.example/x7f2", "Our Team") is PageRole.TEAM


def test_an_unremarkable_url_suggests_nothing() -> None:
    assert classify("https://clinic.example/blog/2026/09/a-post") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://clinic.example/wp-admin/",
        "https://clinic.example/wp-login.php",
        "https://clinic.example/cart",
        "https://clinic.example/tag/braces",
        "https://clinic.example/category/news",
        "https://clinic.example/2026/page/4",
        "https://clinic.example/feed",
        "https://clinic.example/brochure.pdf",
        "https://clinic.example/logo.png",
        "https://clinic.example/app.js",
        "ftp://clinic.example/x",
        "https://other.example/contact",
    ],
)
def test_pages_that_are_not_worth_the_budget_are_skipped(url: str) -> None:
    assert not is_crawlable(url, SITE)


def test_a_normal_page_is_crawlable() -> None:
    assert is_crawlable("https://clinic.example/contact", SITE)
    assert is_crawlable("https://www.clinic.example/about-us/", SITE)


def test_links_are_read_absolute_and_in_order() -> None:
    links = links_from(HOMEPAGE, SITE)
    urls = [url for url, _ in links]
    assert "https://clinic.example/about-us" in urls
    assert "https://clinic.example/brochure.pdf" in urls  # resolved against <base>
    # Fragments, javascript:, and anchors with no href are not links to anywhere.
    assert not any(u.endswith("#top") for u in urls)
    assert not any(u.startswith("javascript:") for u in urls)
    assert len(links) == 10


def test_an_icon_link_falls_back_to_its_accessible_name() -> None:
    html = '<a href="/x" aria-label="Contact Us"><svg/></a>'
    assert links_from(html, SITE) == [("https://clinic.example/x", "Contact Us")]


def test_a_declared_base_href_is_honoured() -> None:
    html = (
        '<html><head><base href="https://clinic.example/en/"></head>'
        '<body><a href="contact">C</a></body></html>'
    )
    assert links_from(html, "https://clinic.example/")[0][0] == "https://clinic.example/en/contact"


def test_sitemap_reads_both_a_urlset_and_an_index() -> None:
    urlset = b"""<?xml version="1.0"?><urlset>
      <url><loc>https://clinic.example/contact</loc></url>
      <url><loc> https://clinic.example/about </loc></url>
    </urlset>"""
    assert sitemap_urls(urlset) == [
        "https://clinic.example/contact",
        "https://clinic.example/about",
    ]
    index = b"<sitemapindex><sitemap><loc>https://clinic.example/sitemap-1.xml</loc></sitemap></sitemapindex>"
    assert sitemap_urls(index) == ["https://clinic.example/sitemap-1.xml"]


def test_a_sitemap_is_read_only_so_far() -> None:
    # A shop's sitemap runs to tens of thousands of products and none of them is what we came for.
    huge = "<urlset>" + "<url><loc>https://clinic.example/p</loc></url>" * 5000 + "</urlset>"
    assert len(sitemap_urls(huge, limit=10)) == 10


def test_a_broken_sitemap_costs_its_broken_entries_not_the_file() -> None:
    messy = (
        "<urlset><url><loc>https://clinic.example/a</loc></url><url><loc>unclosed</url></urlset>"
    )
    assert "https://clinic.example/a" in sitemap_urls(messy)


def test_the_homepage_is_always_first_and_roles_follow_in_priority_order() -> None:
    chosen = choose_pages(SITE, links_from(HOMEPAGE, SITE))
    assert chosen[0] == Candidate("https://clinic.example/", PageRole.HOMEPAGE, "homepage")
    roles = [c.role for c in chosen]
    assert roles == [
        PageRole.HOMEPAGE,
        PageRole.ABOUT,
        PageRole.TEAM,
        PageRole.SERVICES,
        PageRole.CAREERS,
    ]
    # No contact page on this homepage: the only candidate was Cloudflare's obfuscated link, whose
    # path is skipped as `cdn-cgi`. That is the right answer, and the sitemap is where it is found.
    assert PageRole.CONTACT not in roles


def test_the_sitemap_fills_only_what_the_homepage_missed() -> None:
    chosen = choose_pages(
        SITE,
        links_from(HOMEPAGE, SITE),
        sitemap=[
            "https://clinic.example/contact-us",
            "https://clinic.example/about-us-old",
            "https://clinic.example/p/1",
        ],
    )
    by_role = {c.role: c for c in chosen}
    assert by_role[PageRole.CONTACT].url == "https://clinic.example/contact-us"
    assert by_role[PageRole.CONTACT].via == "sitemap"
    # About was already found on the homepage, so the sitemap's second About is not taken.
    assert by_role[PageRole.ABOUT].via == "homepage"


def test_only_one_page_per_role() -> None:
    links = [
        ("https://clinic.example/contact", "Contact"),
        ("https://clinic.example/contact-2", "Contact again"),
        ("https://clinic.example/reach-us", "Reach us"),
    ]
    chosen = choose_pages(SITE, links)
    assert [c.role for c in chosen] == [PageRole.HOMEPAGE, PageRole.CONTACT]
    assert chosen[1].url == "https://clinic.example/contact"


def test_duplicate_links_to_one_page_count_once() -> None:
    links = [
        ("https://clinic.example/contact?utm_source=nav", "Contact"),
        ("https://clinic.example/contact/", "Contact"),
    ]
    chosen = choose_pages(SITE, links)
    assert len(chosen) == 2


def test_the_budget_is_respected_and_contact_survives_a_small_one() -> None:
    chosen = choose_pages(
        SITE,
        [
            ("https://clinic.example/careers", "Careers"),
            ("https://clinic.example/contact", "Contact"),
            ("https://clinic.example/about", "About"),
        ],
        limit=2,
    )
    # Two pages: the homepage, and the one that carries the emails.
    assert [c.role for c in chosen] == [PageRole.HOMEPAGE, PageRole.CONTACT]


def test_a_quick_crawl_reads_the_homepage_and_nothing_else() -> None:
    chosen = choose_pages(SITE, links_from(HOMEPAGE, SITE), limit=MAX_PAGES_QUICK)
    assert [c.role for c in chosen] == [PageRole.HOMEPAGE]


def test_the_budgets_are_the_documented_ones() -> None:
    assert (page_budget("quick"), page_budget("standard"), page_budget("deep")) == (
        MAX_PAGES_QUICK,
        MAX_PAGES_STANDARD,
        MAX_PAGES_DEEP,
    )
    # An unknown depth gets the middle one rather than an exception: a spec that somehow carries a
    # new depth should crawl conservatively, not fail the job.
    assert page_budget("exhaustive") == MAX_PAGES_STANDARD


def test_an_offsite_link_is_never_chosen() -> None:
    chosen = choose_pages(SITE, [("https://www.facebook.com/clinic/about", "About us on Facebook")])
    assert [c.role for c in chosen] == [PageRole.HOMEPAGE]
