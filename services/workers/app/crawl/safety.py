"""The SSRF guard (docs/06 section 4.8, docs/10: "block private ranges, metadata IPs, non-http").

The crawler fetches URLs it was handed by a third party -- a website field from a Places listing,
a link found on a page. Any of those can point back inside our own network, and a request made
from the worker arrives with the worker's network position: inside the VPC, able to reach the
instance metadata service and the database.

The check is split in two on purpose. `url_rejection` is pure: given a URL and the addresses a
resolver returned, it says yes or no with no I/O at all, so every range and every oddity below has
a test. `SafeResolver` is the thin part that actually asks DNS, and it is the only piece that
needs a running network to exercise.

Resolving before deciding is the whole point. A hostname is not evidence: `localtest.me` and any
attacker-controlled domain can resolve to 127.0.0.1, so a blocklist of names is theatre. What
matters is the address the connection would go to.
"""

import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.jobs.errors import InvalidInputError

#: Either address family; the checks below ask the same questions of both.
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Only these reach the open web. `file:`, `gopher:`, `ftp:` and friends are not pages, and
#: `data:` and `javascript:` are not fetches.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: docs/06 section 4.8. More than this is a redirect chain being used to hide the destination.
MAX_REDIRECTS = 5

#: Cloud instance metadata. These are ordinary-looking public addresses that are link-local or
#: unique-local in practice, so they are named rather than left to the range checks: AWS/GCP/Azure
#: use 169.254.169.254, Alibaba 100.100.100.200, Oracle 192.0.0.192, and fd00:ec2::254 on IPv6.
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a)
    for a in ("169.254.169.254", "100.100.100.200", "192.0.0.192", "fd00:ec2::254")
)


#: Checked in order, because ranges overlap and the first match names the rejection in the log:
#: "loopback" says more than "not routable", and 240.0.0.0/4 is both reserved and private by
#: Python's reckoning, where reserved is the truer word.
#:
#: The last entry is the one that matters most. Enumerating ranges misses things -- Python says
#: 100.64.0.0/10 is neither private nor reserved, so CGNAT walks straight through every named
#: check, and that range is where Tailscale hosts and this project's own Coolify box live
#: (100.87.108.105). `is_global` is the question actually being asked: would a packet to this
#: address leave for the public internet? Anything else is somewhere we have no business reaching,
#: whatever it happens to be called.
_ADDRESS_CHECKS: tuple[tuple[str, Callable[[IPAddress], bool]], ...] = (
    ("loopback_address", lambda a: a.is_loopback),
    ("link_local_address", lambda a: a.is_link_local),
    ("multicast_address", lambda a: a.is_multicast),
    ("unspecified_address", lambda a: a.is_unspecified),
    ("reserved_address", lambda a: a.is_reserved),
    ("private_address", lambda a: a.is_private),
    ("not_globally_routable", lambda a: not a.is_global),
)


@dataclass(frozen=True, slots=True)
class Rejection:
    """Why a URL will not be fetched. `reason` is for logs; `detail` names the offending part."""

    reason: str
    detail: str

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}"


def address_rejection(address: IPAddress) -> Rejection | None:
    """Why this address is out of bounds, or None when it is an ordinary public address."""
    if address in METADATA_ADDRESSES:
        return Rejection("metadata_address", str(address))

    # An IPv4-mapped or 6to4/Teredo address carries a v4 address inside it, and the v4 one is what
    # the packet reaches. Unwrap before judging, or ::ffff:127.0.0.1 walks straight through.
    if isinstance(address, ipaddress.IPv6Address):
        embedded = (
            address.ipv4_mapped
            or address.sixtofour
            or (address.teredo[1] if address.teredo else None)
        )
        if embedded is not None:
            inner = address_rejection(embedded)
            return Rejection(inner.reason, f"{address} -> {inner.detail}") if inner else None

    for reason, holds in _ADDRESS_CHECKS:
        if holds(address):
            return Rejection(reason, str(address))
    return None


def url_rejection(  # noqa: PLR0911 - one guard clause per way a URL can be unsafe
    url: str, addresses: Iterable[object] = ()
) -> Rejection | None:
    """Why this URL will not be fetched, or None when it may be.

    `addresses` are what a resolver returned for the host; strings and `ip_address` objects are
    both accepted so a caller does not have to convert. Passing none checks only the URL itself,
    which is the right first pass before paying for DNS.
    """
    try:
        # urlsplit itself raises on an unterminated IPv6 literal (`http://[::1`), before there is
        # anything to inspect, so the parse is inside the guard rather than after it.
        parts = urlsplit(url)
    except ValueError as exc:
        return Rejection("malformed_url", str(exc))
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return Rejection("scheme_not_allowed", scheme or "(none)")

    # Credentials in a URL are a redirect-laundering trick as often as a convenience, and nothing
    # we crawl needs them.
    if parts.username or parts.password:
        return Rejection("credentials_in_url", "userinfo present")

    host = parts.hostname
    if not host:
        return Rejection("missing_host", url[:120])

    # A host given as a literal address needs no resolver: judge it now.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return address_rejection(literal)

    for raw in addresses:
        try:
            address = (
                raw
                if isinstance(raw, (ipaddress.IPv4Address, ipaddress.IPv6Address))
                else (ipaddress.ip_address(str(raw)))
            )
        except ValueError:
            return Rejection("unresolvable_address", str(raw)[:120])
        rejected = address_rejection(address)
        if rejected is not None:
            return rejected
    return None


class SafeResolver:
    """Resolves a host and refuses the answer if any address is out of bounds.

    Every address is checked, not the first. A host that returns one public and one private
    address would otherwise be reachable on a retry, and which one a connection picks is not ours
    to decide.
    """

    def __init__(self, *, family: int = socket.AF_UNSPEC) -> None:
        self._family = family

    def resolve(self, host: str, port: int) -> Sequence[str]:
        try:
            infos = socket.getaddrinfo(host, port, self._family, socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise InvalidInputError(f"cannot resolve {host}: {exc}") from exc
        # sockaddr is (host, port) for IPv4 and (host, port, flow, scope) for IPv6, so the first
        # element is typed as str | int. It is always the address; str() keeps that explicit.
        return [str(info[4][0]) for info in infos]

    def check(self, url: str) -> None:
        """Raises `InvalidInputError` when the URL must not be fetched.

        `invalid_input` rather than `access_restricted`: nothing out there is refusing us. We were
        handed a URL that points somewhere it has no business pointing, and no retry or backoff
        changes that.
        """
        first = url_rejection(url)
        if first is not None:
            raise InvalidInputError(f"unsafe url: {first}")

        parts = urlsplit(url)
        host = parts.hostname
        if host is None:  # already covered by url_rejection; keeps the type checker honest
            raise InvalidInputError("unsafe url: missing_host")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return  # a literal address was fully judged above

        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        rejected = url_rejection(url, self.resolve(host, port))
        if rejected is not None:
            raise InvalidInputError(f"unsafe url: {rejected}")
