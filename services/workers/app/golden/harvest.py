"""Adding sites to the golden corpus from the live graph (task 3.14, ADR-0020).

Fetches a site the way the crawler would, scrubs every contact value out of it, and writes the
fixture with a **draft** label file. The draft is a starting point and never ground truth: the
report refuses to score a draft, because a label produced by guessing is a test that measures the
guess.

What the draft guesses, and why each guess is only a guess:

* the business's own email is the one on its own domain, preferring a `mailto:` over text --
  which is wrong for a clinic whose doctor uses Gmail, and that is most of them;
* the business's own phone is the one in a `tel:` link -- which is wrong when the footer's "site
  by" number is the only link on the page;
* anything beside "site by", "developed by" or "powered by" is somebody else's -- which catches
  the common case and misses the rest.

Reviewing means opening the fixture, fixing `expect` and `absent`, writing a line in
`absent_because` for each, and setting `draft: false`.
"""

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.crawl.discovery import PageRole, choose_pages, links_from
from app.golden.corpus import CORPUS, LABELS, Labels, Page
from app.golden.scrub import Substitution, scrub
from app.normalize.domains import registrable_domain

#: Honest, and the same shape the crawler uses. A corpus is built by reading pages we already
#: read in the course of normal work, so the only new thing here is that we keep a copy.
USER_AGENT = "LeadForgeBot/0.1 (+golden corpus; contact via the site owner's own form)"

#: Who else's number sits in a footer. Matched against the text around a substitution.
_SOMEBODY_ELSE = re.compile(
    r"(?i)(site|website|designed|developed|powered|maintained|hosted)\s+(by|with)"
)

#: How many pages of one site to keep. The crawler reads up to six at standard depth; three is
#: enough to exercise the page-choice rules and keeps the corpus reviewable by a human.
PAGES_PER_SITE = 3


def _host_placeholder(url: str, slug: str) -> str:
    """The URL with the real host replaced and the path kept.

    The path is what the page-choice rules read (`/contact`, `/about`), so a corpus of `/page1`
    would stop exercising them. The host is replaced because the corpus names no real business.
    """
    parts = urlsplit(url)
    return f"https://{slug}.example.in{parts.path or '/'}"


def _draft_labels(
    slug: str, pages: list[tuple[Page, list[Substitution]]]
) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """A first guess at what the pipeline should find, to be reviewed by a human."""
    expect: dict[str, str] = {}
    absent: list[str] = []
    because: dict[str, str] = {}

    best_email: tuple[int, str] | None = None
    best_phone: tuple[int, str] | None = None
    rank = {"jsonld": 3, "mailto": 3, "tel": 3, "whatsapp": 2, "text": 1}

    for _page, substitutions in pages:
        for sub in substitutions:
            score = rank.get(sub.context, 0)
            if sub.kind == "email" and (best_email is None or score > best_email[0]):
                best_email = (score, sub.replacement)
            elif sub.kind == "phone" and (best_phone is None or score > best_phone[0]):
                best_phone = (score, sub.replacement)

    if best_email is not None:
        expect["email"] = best_email[1]
    if best_phone is not None:
        expect["phone"] = best_phone[1]

    # Everything the draft did not choose is listed as absent, because that is the safer
    # default: a reviewer moving a value from `absent` to `expect` is a deliberate decision,
    # while a value in neither list is simply unmeasured and nobody notices.
    chosen = {pick[1] for pick in (best_email, best_phone) if pick is not None}
    for _page, substitutions in pages:
        for sub in substitutions:
            if sub.replacement in chosen or sub.replacement in absent:
                continue
            absent.append(sub.replacement)
            because[sub.replacement] = (
                f"a second {sub.kind} found in {sub.context}; "
                "review whether it is the business's own"
            )
    return expect, absent, because


async def harvest(domain: str, slug: str, *, root: Path = CORPUS) -> Labels | None:
    """One site, scrubbed into the corpus with a draft label file. None when it cannot be read."""
    site = root / slug
    pages: list[tuple[Page, list[Substitution]]] = []

    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        try:
            home = await client.get(f"https://{domain}/")
        except Exception:
            return None
        if home.status_code >= 400 or not home.text:
            return None

        wanted = [("homepage", str(home.url), home.text)]
        for candidate in choose_pages(
            str(home.url), links_from(home.text, str(home.url)), sitemap=[], limit=PAGES_PER_SITE
        ):
            if candidate.role is PageRole.HOMEPAGE or len(wanted) >= PAGES_PER_SITE:
                continue
            try:
                page = await client.get(candidate.url)
            except Exception as exc:
                # Printed rather than logged, and swallowed rather than raised: this is a
                # developer running a harvester, one page of one site failing is the normal
                # case, and a corpus of two pages instead of three is still a corpus.
                print(f"  {slug}: could not read {candidate.url} ({type(exc).__name__})")
                continue
            if page.status_code < 400 and page.text:
                wanted.append((candidate.role.value, str(page.url), page.text))

    site.mkdir(parents=True, exist_ok=True)
    for role, url, html in wanted:
        scrubbed = scrub(html, slug=slug)
        name = f"{role}.html"
        (site / name).write_text(scrubbed.html, encoding="utf-8")
        pages.append(
            (Page(role=role, file=name, url=_host_placeholder(url, slug)), scrubbed.substitutions)
        )

    expect, absent, because = _draft_labels(slug, pages)
    labels = Labels(
        slug=slug,
        harvested_at=datetime.now(UTC).isoformat(timespec="seconds"),
        pages=[page for page, _ in pages],
        expect=expect,
        absent=absent,
        absent_because=because,
        draft=True,
    )
    (site / LABELS).write_text(labels.to_json(), encoding="utf-8")
    return labels


def slug_for(name: str, domain: str) -> str:
    """A directory name that is readable and names no real business.

    From the domain rather than the company name, and truncated: the corpus is reviewed by
    reading directory names, and `dr-sharma-dental-clinic-gurugram-best-dentist` is not readable.
    """
    stem = registrable_domain(f"https://{domain}") or domain
    label = stem.split(".")[0]
    cleaned = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return (cleaned or "site")[:28]


async def harvest_many(
    sites: list[tuple[str, str]],
    *,
    want: int,
    root: Path = CORPUS,
    delay_s: float = 2.0,
    refresh: bool = False,
) -> list[str]:
    """`want` sites, one at a time with a pause, stopping once that many are added.

    `want` rather than "all of them": the first version took a limit and used it only to size
    the candidate query, so `--limit 12` harvested 26 sites. A flag that does not do what it
    says is worse than no flag.

    `refresh` inverts which sites are eligible: only the ones already in the corpus, rebuilt
    from the live page. It exists because the scrubber is what writes the labels, so a fix to
    the scrubber makes every fixture it wrote out of date -- and the fix that prompted it had
    left three numbers in the corpus that `phonenumbers` refuses and every labelled phone a
    mobile, so `phone_type=fixed_line` was being measured on nothing. A refreshed site is a
    draft again, and that is not a cost of the mechanism but the point of it: the human agreed
    to the old values, and these are different values (ADR-0020).

    Serial and slow on purpose: this reads other people's servers for our convenience, and
    doing it politely costs nothing we need.
    """
    added: list[str] = []
    for name, domain in sites:
        if len(added) >= want:
            break
        slug = slug_for(name, domain)
        known = (root / slug / LABELS).exists()
        if known and not refresh:
            continue
        if refresh and not known:
            continue
        labels = await harvest(domain, slug, root=root)
        if labels is not None:
            added.append(slug)
        await asyncio.sleep(delay_s)
    return added
