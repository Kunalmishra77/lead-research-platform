"""Email addresses (docs/06 section 6): one rule, in one place.

Like phones, this rule lived inside `app/crawl/contacts.py` as a private `_tidy_email`, which
meant it applied to addresses read off a web page and to nothing else. The place index's mapping
did `add("email", place.email)` with the raw string, and the index's own check is only that the
value is *plausible* -- enough to catch the Delhi row where every column had shifted one place
and `IN` arrived in the email field, and not enough to make two sources agree on spelling.

Measured on the live graph on 2026-10-13, before this module existed: 424 current email values,
349 of them from Overture and 75 from the crawl. None were mixed-case or padded, so nothing is
being fixed retroactively here -- Overture's happen to be clean. What is being fixed is that
nothing *guaranteed* it. `field_values` is unique per (entity, field) for the current row, so
`Info@Clinic.IN` from a release and `info@clinic.in` from that clinic's own site are two
different values for one address: the second retires the first, the provenance of the first is
lost, and a dedupe that compares strings sees two businesses.

The split with the crawl is deliberate. De-obfuscating `name (at) clinic (dot) in` is about how
a page hides an address from scrapers, which only the crawler can know about; what counts as a
usable business address is not. So the crawler decodes and then calls `normalize`.
"""

import re
from typing import Final

from email_validator import EmailNotValidError, validate_email

from app.normalize.domains import has_real_tld

#: Role accounts. Kept, but flagged: `info@` is a lead, `priya@` is a person, and docs/10 treats
#: the second as personal data with different handling. 52 of the 424 addresses on the live graph
#: are these, and nothing recorded which -- the crawl computed the flag and dropped it.
ROLE_LOCAL_PARTS: Final[frozenset[str]] = frozenset(
    {
        "info", "contact", "enquiry", "enquiries", "inquiry", "inquiries", "hello", "hi",
        "sales", "support", "help", "admin", "office", "mail", "email", "care",
        "reception", "frontdesk", "appointments", "booking", "bookings", "service",
        "team", "hr", "careers", "jobs", "accounts", "billing", "finance", "noreply",
        "no-reply", "donotreply",
    }
)  # fmt: skip

#: Addresses that belong to a page's plumbing, or to a template nobody filled in, rather than to
#: the business. Applied to every source: a bulk release carries placeholder addresses too.
_IGNORED_HOSTS: Final[re.Pattern[str]] = re.compile(
    r"(?i)(^|\.)(example\.(com|org|net)|sentry\.io|wixpress\.com|sentry-next\.wixpress\.com"
    r"|domain\.com|yourdomain\.com|email\.com|company\.com|godaddy\.com|wordpress\.com)$"
)

#: Plainly not a person's or a company's address, whatever the syntax says.
_IGNORED_LOCAL_PARTS: Final[frozenset[str]] = frozenset(
    {"you", "your", "name", "email", "someone", "user", "username"}
)

#: `field_values.value` is jsonb and the column takes anything; 254 is the limit RFC 5321 puts on
#: a path, and a longer "address" is something else that happens to contain an `@`.
_MAX_LENGTH: Final[int] = 254


def normalize(address: str | None) -> str | None:
    """The address in the one spelling every source should store, or None.

    Lowercased, because an address is case-insensitive in practice and a stored `Info@` is a
    second value for the same mailbox. Validated for syntax only: the page or the release is the
    source of truth for what it said, and whether anyone answers is `app/verify`'s question and
    a paid one.
    """
    raw = (address or "").strip().strip("<>").strip()
    local, at, domain = raw.rpartition("@")
    local, domain = local.strip(), domain.strip().strip(".")
    if (
        not at
        or not local
        or not domain
        or len(raw) > _MAX_LENGTH
        or local.lower() in _IGNORED_LOCAL_PARTS
        or _IGNORED_HOSTS.search(domain)
        # A real top-level domain. `validate_email` checks shape, not existence, so without this
        # a page reading "treatment done@Dr.Bhatia's clinic" yields `done@dr.bhatia` -- which
        # went out in a customer's results before the crawl's version of this line existed.
        or not has_real_tld(domain)
    ):
        return None

    value = f"{local}@{domain}".lower()
    try:
        validate_email(value, check_deliverability=False)
    except EmailNotValidError:
        return None
    return value


def is_role(address: str) -> bool:
    """Whether the local part is a shared mailbox rather than a named person.

    Read off the address as given, so a caller that has not normalised still gets the right
    answer for `Info@clinic.in`.
    """
    return address.split("@", 1)[0].strip().lower() in ROLE_LOCAL_PARTS
