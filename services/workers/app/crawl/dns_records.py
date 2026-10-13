"""What a domain's DNS says about it (task 3.9, docs/06 §5.3).

Two facts, from one MX lookup:

* `mail_exchanger` -- the host a sender would hand mail to. Found, not inferred.
* `mail_provider` -- who runs it, matched from the hostname. `derived_pattern`, because the
  hostname is evidence and the label is our reading of it.

The patterns come from the graph rather than from a vendor list. Measured over 495 domains on
2026-10-13:

    no MX at all              251  (51%)
    Google Workspace           69
    Hostinger                  50
    Microsoft 365              22
    GoDaddy                    12
    Zoho                        8
    Directi / mailhostbox       4
    Jellyfish                   3

which is why there is no fingerprint dataset here: seven suffixes cover two thirds of the
domains that have mail at all, and the rest are a business running its own `mail.<domain>`. The
HTML half of task 3.9 is a different matter -- the widely-used fingerprint set stopped being
openly licensed in 2023 -- and is deliberately not done here (ADR-0018 is the same question,
answered the same way: no copyleft or proprietary data in a product that intends to sell).

**What this does not do.** It does not say whether an address is deliverable. That is task 4.5,
and the difference matters: 51% of these domains have no MX, which is a fact worth collecting and
a verdict worth withholding until something owns it. A domain with no MX gets no mail fields at
all, and the absence is what a later verification step reads.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import dns.exception
import dns.resolver

#: MX hostname suffix -> who runs it. Longest suffix wins, so `mx.zoho.in` is Zoho and not a
#: stray `.in` match. Ordered by what the graph actually uses.
PROVIDERS: Final[tuple[tuple[str, str], ...]] = (
    ("l.google.com", "Google Workspace"),
    ("googlemail.com", "Google Workspace"),
    ("smtp.google.com", "Google Workspace"),
    ("protection.outlook.com", "Microsoft 365"),
    ("mail.protection.outlook.com", "Microsoft 365"),
    ("hostinger.com", "Hostinger"),
    ("hostinger.in", "Hostinger"),
    ("secureserver.net", "GoDaddy"),
    ("zoho.com", "Zoho Mail"),
    ("zoho.in", "Zoho Mail"),
    ("zoho.eu", "Zoho Mail"),
    ("mailhostbox.com", "Directi"),
    ("pphosted.com", "Proofpoint"),
    ("trendmicro.com", "Trend Micro"),
    ("yandex.net", "Yandex 360"),
    ("mail.ru", "Mail.ru"),
    ("protonmail.ch", "Proton Mail"),
    ("emailsrvr.com", "Rackspace"),
    ("titan.email", "Titan"),
    ("improvmx.com", "ImprovMX"),
    ("mimecast.com", "Mimecast"),
    ("messagelabs.com", "Symantec"),
)

#: A business running its own mail on its own domain. Not a provider name, and worth saying so
#: rather than leaving `mail_provider` empty: "they run their own" is a different fact from "we
#: do not know".
SELF_HOSTED: Final[str] = "self-hosted"

#: A DNS answer is as close to certain as this pipeline gets: it came from the domain's own
#: nameservers, not from a page that might be a testimonial or a template. Higher than
#: `CONFIDENCE_STRUCTURED` (0.8) for that reason, and short of 1.0 because records change and
#: this one was true when it was read.
CONFIDENCE_DNS: Final[float] = 0.9

#: Seconds. Generous because this runs beside a crawl that already waits seconds per page, and a
#: slow resolver should cost one lookup rather than fail a task.
TIMEOUT_S: Final[float] = 5.0
LIFETIME_S: Final[float] = 8.0


#: How a caller reads a domain's mail records. A type rather than a hard call, so a test can
#: pass its own and never touch a resolver, and so a worker on a network that blocks DNS can be
#: given one that answers nothing.
MailDnsLookup = Callable[[str], Awaitable["MailDns | None"]]


@dataclass(frozen=True, slots=True)
class MailDns:
    """What one domain's MX records say."""

    #: In preference order, as a sender would try them. Lower-case, no trailing dot.
    exchangers: tuple[str, ...]
    #: Who runs the preferred one, or None when the hostname matches nothing known.
    provider: str | None

    @property
    def preferred(self) -> str:
        return self.exchangers[0]


def provider_for(host: str, domain: str) -> str | None:
    """Who runs this mail host, or None when the hostname says nothing recognisable.

    Longest suffix first, so `mail.protection.outlook.com` is matched before
    `protection.outlook.com` and `mx.zoho.in` is Zoho rather than a stray match on `.in`.
    """
    host = host.lower().rstrip(".")
    for suffix, name in sorted(PROVIDERS, key=lambda p: len(p[0]), reverse=True):
        if host == suffix or host.endswith(f".{suffix}"):
            return name
    # `mail.theirdomain.in` for theirdomain.in. Checked after the list, because a provider can
    # also give a customer a vanity host inside their own domain.
    if host == domain.lower() or host.endswith(f".{domain.lower()}"):
        return SELF_HOSTED
    return None


def _resolver() -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver()
    resolver.timeout = TIMEOUT_S
    resolver.lifetime = LIFETIME_S
    return resolver


def _lookup(domain: str) -> MailDns | None:
    """The blocking half, run in a thread by `mail_dns`."""
    try:
        answers = _resolver().resolve(domain, "MX")
    except (
        dns.resolver.NoAnswer,
        dns.resolver.NXDOMAIN,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
        dns.exception.SyntaxError,
    ):
        # No mail, no such domain, or nobody answered. All three are "no mail fields", and none
        # of them is this task's business to label -- see the module docstring on task 4.5.
        return None

    ranked: list[tuple[int, str]] = []
    for record in answers:
        host = str(getattr(record, "exchange", "")).rstrip(".").lower()
        # A single `.` is the RFC 7505 "null MX": the domain is saying it accepts no mail at all.
        # Not an exchanger, and not an error either.
        if not host:
            continue
        ranked.append((int(getattr(record, "preference", 0)), host))

    if not ranked:
        return None
    exchangers = tuple(host for _, host in sorted(ranked))
    return MailDns(exchangers=exchangers, provider=provider_for(exchangers[0], domain))


async def mail_dns(domain: str) -> MailDns | None:
    """The domain's mail hosts, or None when it has none we could find.

    In a thread: `dns.resolver` blocks, and this runs on the same event loop as a crawl that is
    waiting on a politeness delay. A lookup that takes the full eight seconds must not be eight
    seconds nobody else can use.
    """
    if not domain or "." not in domain:
        return None
    return await asyncio.to_thread(_lookup, domain)
