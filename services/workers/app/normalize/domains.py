"""What a web address says about who owns it.

Shared because two parts of the system need the same judgement and must not drift apart: the SERP
matcher deciding whether a result is a business's own site, and the graph deciding whether a
domain may identify a company.

The distinction that matters here is between a domain a business *owns* and one it merely has a
page on. `companies.primary_domain` is UNIQUE, so it is an identity claim: the second business to
arrive with `sites.google.com` would not get its own company, it would be merged into the first
one's. Two unrelated clinics become one, and no later stage can tell they were ever separate.
The schema anticipated this — `company_domains.is_platform` exists, and the unique index on
`company_domains.domain` is partial on `not is_platform` — so a platform host is recorded as a
domain the company has a page on, and never as the domain that identifies it.
"""

from urllib.parse import urlparse

#: Public suffixes two labels deep, so the registrable name is the third from the right. Not the
#: full Public Suffix List: the geographies we sell into, plus the common global ones. An unlisted
#: suffix costs a conservative answer, never a wrong one.
MULTI_LABEL_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.in", "net.in", "org.in", "gen.in", "firm.in", "ind.in", "ac.in",
        "co.uk", "org.uk", "me.uk", "com.au", "net.au", "com.sg", "com.my",
        "co.nz", "co.za", "com.br", "co.jp", "com.mx", "com.tr", "ae.org",
    }
)  # fmt: skip

#: Hosts that thousands of unrelated businesses share: site builders, social networks and link
#: pages. A business may well be reachable only through one of these — that is normal for a small
#: clinic — but the address identifies the platform, not the business.
PLATFORM_HOSTS: frozenset[str] = frozenset(
    {
        # Site builders and free hosting
        "sites",  # sites.google.com
        "business",  # business.site
        "blogspot",
        "wordpress",
        "wixsite",
        "wix",
        "weebly",
        "squarespace",
        "webnode",
        "godaddysites",
        "mystrikingly",
        "strikingly",
        "webflow",
        # Social and link pages
        "facebook",
        "instagram",
        "linkedin",
        "twitter",
        "x",
        "youtube",
        "whatsapp",
        "wa",  # wa.me
        "linktr",  # linktr.ee
        "bio",  # bio.link
        "t",  # t.me
        "telegram",
        "pinterest",
        "tumblr",
        "medium",
    }
)


def host_of(url: str) -> str:
    """The host of a URL, lowercased, without credentials, port or a leading `www.`."""
    if not url:
        return ""
    parsed = urlparse(url if "//" in url else f"//{url}", scheme="https")
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    return host.removeprefix("www.").strip(".")


def registrable_domain(url: str | None) -> str | None:
    """The bare host a site lives on, or None when there is nothing usable.

    None rather than a guess. `companies.primary_domain` CHECKs that no `www.` remains and that
    the value is a bare host, and it is the dedupe key: a wrong domain merges two unrelated
    businesses into one company.
    """
    if not url:
        return None
    host = host_of(url)
    if not host or "." not in host or " " in host:
        return None
    return host


def registrable_stem(url: str | None) -> str:
    """The name whoever owns this address registered, without subdomain or public suffix.

    `dentalgalaxy.justdial.com` is Justdial's, not Dental Galaxy's, and `32smiles.co.in` is
    `32smiles`. Reading the leftmost label instead gets both of those exactly backwards.
    """
    host = host_of(url or "")
    labels = host.split(".")
    if len(labels) < 2:
        return host.replace("-", "")
    suffix_labels = 2 if ".".join(labels[-2:]) in MULTI_LABEL_SUFFIXES else 1
    if len(labels) <= suffix_labels:
        return ""
    return labels[-(suffix_labels + 1)].replace("-", "")


def is_platform_host(url: str | None) -> bool:
    """True when this address belongs to a platform rather than to one business.

    Checked against every label, not just the registrable one: `example.wixsite.com` and
    `wixsite.com` are the same platform, and a business's own name sitting in front of it does
    not make the domain theirs.
    """
    host = host_of(url or "")
    if not host:
        return False
    return any(label in PLATFORM_HOSTS for label in host.split("."))
