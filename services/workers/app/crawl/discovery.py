"""Choosing which pages of a site to read (docs/06 section 4.4, task 3.7).

A lead's website has one page we want for certain -- the one with the contact details -- and a few
more worth reading. It also has a few hundred we must not spend a crawl budget on: blog archives,
tag listings, paginated product grids, a login form. docs/06 caps a Standard crawl at six pages and
a Deep one at fifteen, so the whole job here is spending those six well.

Two sources of candidates, in this order:

1. **Links on the homepage.** A small business puts "Contact Us" in its navigation, and the anchor
   text is often better evidence than the URL: `/cdn-cgi/l/email-protection` says nothing while
   "Email us" says everything. Both are read.
2. **The sitemap**, for sites whose navigation is JavaScript we have not run. Only consulted when
   the homepage links left a role unfilled, because a sitemap for a shop lists ten thousand
   products and reading it to find `/contact` is the long way round.

Everything here is pure: HTML and XML in, a short ordered list of URLs out. No fetching, so every
rule below has a test.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin, urlsplit, urlunsplit

from scrapling import Selector

from app.normalize.domains import site_domain

#: docs/06 section 4.4.
MAX_PAGES_QUICK = 1
MAX_PAGES_STANDARD = 6
MAX_PAGES_DEEP = 15

#: Read far enough into a sitemap to find a contact page, and no further. A shop's sitemap runs to
#: tens of thousands of product URLs, and none of them is what we came for.
MAX_SITEMAP_URLS = 2000


class PageRole(StrEnum):
    """What we expect a page to tell us. The order of `PRIORITY` is what a short budget buys."""

    HOMEPAGE = "homepage"
    CONTACT = "contact"
    ABOUT = "about"
    TEAM = "team"
    SERVICES = "services"
    CAREERS = "careers"


#: Contact first because it carries the emails and phones that make a lead usable; careers last
#: because it is a hiring signal, which is Phase 5's interest rather than this phase's.
PRIORITY: tuple[PageRole, ...] = (
    PageRole.CONTACT,
    PageRole.ABOUT,
    PageRole.TEAM,
    PageRole.SERVICES,
    PageRole.CAREERS,
)

#: Matched against the URL path and, separately, against the anchor text. Hindi-English sites write
#: "Contact Us", "About Us", "Our Team"; `sampark` and `hamare-baare-mein` show up on a minority of
#: Indian small-business sites and cost nothing to recognise.
_ROLE_PATTERNS: tuple[tuple[PageRole, re.Pattern[str]], ...] = (
    (
        PageRole.CONTACT,
        re.compile(
            r"(?i)\b(contact|contacts|contact-us|contactus|reach-us|enquiry|enquire|sampark)\b"
        ),
    ),
    (
        PageRole.ABOUT,
        re.compile(r"(?i)\b(about|about-us|aboutus|who-we-are|our-story|company|overview)\b"),
    ),
    (
        PageRole.TEAM,
        re.compile(r"(?i)\b(team|our-team|people|leadership|management|doctors|staff|faculty)\b"),
    ),
    (
        PageRole.SERVICES,
        re.compile(
            r"(?i)\b(services|service|treatments|what-we-do|solutions|offerings|products)\b"
        ),
    ),
    (PageRole.CAREERS, re.compile(r"(?i)\b(careers|career|jobs|vacancies|join-us|hiring)\b")),
)

#: Paths that are never worth a page of the budget. Archives and listings because they are indexes
#: of content rather than content; `wp-admin`, `login` and `cart` because they are the restriction
#: detector's job and there is no reason to make it do it.
_SKIP_PATH = re.compile(
    r"(?i)(^|/)(wp-admin|wp-login|wp-json|admin|login|signin|sign-in|register|signup|cart|checkout"
    r"|account|my-account|basket|feed|rss|atom|amp|print|search|tag|tags|category|categories"
    r"|author|archive|archives|page|comment|comments|wp-content|cdn-cgi|xmlrpc)(/|$|\.)"
)

#: Not pages. Fetching a 40 MB brochure to look for an email address is a bad trade.
_SKIP_SUFFIX = re.compile(
    r"(?i)\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|gz|tar|csv|jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?"
    r"|mp[34]|m4[av]|mov|avi|webm|wav|ogg|woff2?|ttf|eot|css|js|json|xml|rss)$"
)

#: Dropped when comparing URLs: they change the link without changing the page.
_TRACKING_PARAMS = re.compile(r"(?i)^(utm_[a-z_]+|gclid|fbclid|msclkid|mc_[a-z]+|ref|source)$")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A page worth fetching, and why we think so."""

    url: str
    role: PageRole
    #: What pointed at it: "homepage" for a link on the homepage, "sitemap" otherwise.
    via: str


def canonical(url: str) -> str:
    """The form two links to one page agree on: no fragment, no tracking, no trailing slash.

    Case is lowered on the host only. A path is case-sensitive on most servers, and folding it
    would merge `/Team` with `/team` on the ones where it is not.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = "&".join(
        pair
        for pair in parts.query.split("&")
        if pair and not _TRACKING_PARAMS.match(pair.split("=", 1)[0])
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def same_site(url: str, site: str) -> bool:
    """Whether a URL belongs to the site we are crawling, by registrable domain.

    Registrable rather than exact host, so `www.` and a `blog.` subdomain both count --
    `site_domain`, not `registrable_domain`, which keeps the whole host because that is what
    `companies.primary_domain` stores. A link to the company's Facebook page is a different site:
    it is collected as a social link (docs/08 row 50), not crawled.
    """
    left, right = site_domain(url), site_domain(site)
    return bool(left and right and left == right)


def classify(url: str, anchor_text: str = "") -> PageRole | None:
    """What a URL and its link text suggest the page is, or None when nothing suggests anything.

    The anchor text is checked first. `/cdn-cgi/l/email-protection` tells us nothing and "Email
    us" tells us everything, and the text is what a person reading the page would go by.
    """
    path = urlsplit(url).path
    for role, pattern in _ROLE_PATTERNS:
        if anchor_text and pattern.search(anchor_text):
            return role
    for role, pattern in _ROLE_PATTERNS:
        if pattern.search(path):
            return role
    return None


def is_crawlable(url: str, site: str) -> bool:
    """Whether a URL is worth a page of the budget at all."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return False
    if not same_site(url, site):
        return False
    path = parts.path
    return not (_SKIP_SUFFIX.search(path) or _SKIP_PATH.search(path))


def links_from(html: str | bytes, base_url: str) -> list[tuple[str, str]]:
    """Every (absolute url, anchor text) on a page, in document order.

    Relative hrefs are resolved against the document's own `<base href>` when it has one, because
    a page that sets one means it.
    """
    page = Selector(html if isinstance(html, str) else html.decode("utf-8", errors="ignore"))
    declared = page.css("base::attr(href)").get()
    base = urljoin(base_url, declared.strip()) if declared else base_url

    found: list[tuple[str, str]] = []
    for anchor in page.css("a"):
        href = anchor.attrib.get("href")
        if not href:
            continue
        href = href.strip()
        if not href or href.startswith(("#", "javascript:", "data:")):
            continue
        text = " ".join((anchor.get_all_text() or "").split())
        # The title and aria-label are the accessible name when the link is an icon, which is
        # exactly the case where the text is empty and the URL is opaque.
        if not text:
            text = " ".join(
                (anchor.attrib.get("aria-label") or anchor.attrib.get("title") or "").split()
            )
        try:
            found.append((urljoin(base, href), text))
        except ValueError:
            continue
    return found


def sitemap_urls(xml: str | bytes, *, limit: int = MAX_SITEMAP_URLS) -> list[str]:
    """The `<loc>` values of a sitemap or a sitemap index, capped.

    Both document types are read the same way on purpose: the caller does not need to know which
    it got, and a nested index's children are themselves sitemaps that it can fetch in turn.
    """
    text = xml if isinstance(xml, str) else xml.decode("utf-8", errors="ignore")
    # A regex rather than a parser: a sitemap is a flat list of one element type, and malformed
    # XML from a plugin should cost us the broken entries rather than the whole file.
    return [m.group(1).strip() for m in re.finditer(r"(?is)<loc>\s*(.*?)\s*</loc>", text)][:limit]


def _collect(
    candidates: Iterable[tuple[str, str]],
    *,
    site: str,
    via: str,
    seen: set[str],
    into: dict[PageRole, Candidate],
) -> None:
    """Fills unfilled roles from `candidates`, first match per role wins.

    First match rather than best: a link's position is evidence. A nav bar's "Contact Us" comes
    before a footer's "contact our Delhi branch", and the nav one is the page with the details.
    """
    for url, text in candidates:
        if len(into) == len(PRIORITY):
            return
        key = canonical(url)
        if key in seen or not is_crawlable(url, site):
            continue
        role = classify(url, text)
        if role is None or role in into:
            continue
        seen.add(key)
        into[role] = Candidate(key, role, via)


def choose_pages(
    homepage_url: str,
    links: Iterable[tuple[str, str]],
    *,
    sitemap: Sequence[str] = (),
    limit: int = MAX_PAGES_STANDARD,
) -> list[Candidate]:
    """The pages to fetch, homepage first, then one per role while the budget lasts.

    One page per role, not the best three contact pages: a second `/contact-2` says the same thing
    as the first and costs a page that `/about` would have used.
    """
    site = homepage_url
    seen = {canonical(homepage_url)}
    chosen: list[Candidate] = [Candidate(canonical(homepage_url), PageRole.HOMEPAGE, "homepage")]
    by_role: dict[PageRole, Candidate] = {}

    _collect(links, site=site, via="homepage", seen=seen, into=by_role)
    # Only now the sitemap, and only for what the homepage did not offer. A shop's sitemap lists
    # ten thousand products; reading it to find `/contact` is the long way round.
    if sitemap and len(by_role) < len(PRIORITY):
        _collect(((url, "") for url in sitemap), site=site, via="sitemap", seen=seen, into=by_role)

    for role in PRIORITY:
        if len(chosen) >= limit:
            break
        candidate = by_role.get(role)
        if candidate is not None:
            chosen.append(candidate)
    return chosen


def page_budget(depth: str) -> int:
    """How many pages one company gets at this depth (docs/06 section 4.4)."""
    return {
        "quick": MAX_PAGES_QUICK,
        "standard": MAX_PAGES_STANDARD,
        "deep": MAX_PAGES_DEEP,
    }.get(depth, MAX_PAGES_STANDARD)
