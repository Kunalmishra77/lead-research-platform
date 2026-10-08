"""The SSRF guard (docs/06 section 4.8, docs/10). Phase 3 acceptance asks for these by name:
private IPs, the metadata IP and file:// all blocked.
"""

import ipaddress

import pytest

from app.crawl.safety import (
    MAX_REDIRECTS,
    Rejection,
    SafeResolver,
    address_rejection,
    url_rejection,
)
from app.jobs.errors import InvalidInputError


def test_an_ordinary_public_url_passes() -> None:
    assert url_rejection("https://example.com/contact", ["93.184.216.34"]) is None


@pytest.mark.parametrize(
    "scheme",
    ["file", "ftp", "gopher", "data", "javascript", "jar", ""],
)
def test_only_http_and_https_are_fetched(scheme: str) -> None:
    url = f"{scheme}://example.com/x" if scheme else "example.com/x"
    rejected = url_rejection(url)
    assert rejected is not None
    assert rejected.reason == "scheme_not_allowed"


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("127.0.0.1", "loopback_address"),
        ("::1", "loopback_address"),
        ("10.0.0.5", "private_address"),
        ("172.16.4.9", "private_address"),
        ("192.168.1.1", "private_address"),
        ("0.0.0.0", "unspecified_address"),  # noqa: S104 - an address under test, not a bind
        ("169.254.1.1", "link_local_address"),
        ("fe80::1", "link_local_address"),
        ("fc00::1", "private_address"),
        ("224.0.0.1", "multicast_address"),
        ("240.0.0.1", "reserved_address"),
        ("192.0.2.1", "private_address"),  # TEST-NET-1, which Python calls private
    ],
)
def test_addresses_that_point_inward_are_refused(address: str, reason: str) -> None:
    rejected = address_rejection(ipaddress.ip_address(address))
    assert rejected is not None
    assert rejected.reason == reason


@pytest.mark.parametrize(
    "address",
    ["169.254.169.254", "100.100.100.200", "192.0.0.192", "fd00:ec2::254"],
)
def test_cloud_metadata_addresses_are_named_not_inferred(address: str) -> None:
    # These are the ones that hand out credentials, so they are checked by name rather than left
    # to a range rule that a future refactor could narrow.
    rejected = address_rejection(ipaddress.ip_address(address))
    assert rejected is not None
    assert rejected.reason == "metadata_address"


def test_carrier_grade_nat_is_refused_even_though_python_calls_it_neither() -> None:
    # 100.64.0.0/10 is neither `is_private` nor `is_reserved` in Python, so every named range
    # check misses it. It is also where Tailscale hosts and this project's own Coolify box live,
    # so a URL resolving into it would have been fetched from inside our network.
    rejected = address_rejection(ipaddress.ip_address("100.87.108.105"))
    assert rejected is not None
    assert rejected.reason == "not_globally_routable"


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/"],
)
def test_a_literal_address_needs_no_resolver(url: str) -> None:
    assert url_rejection(url) is not None


def test_an_ipv4_mapped_ipv6_address_cannot_smuggle_loopback_through() -> None:
    # ::ffff:127.0.0.1 is not loopback by IPv6 rules, but the packet reaches 127.0.0.1.
    rejected = address_rejection(ipaddress.ip_address("::ffff:127.0.0.1"))
    assert rejected is not None
    assert rejected.reason == "loopback_address"
    assert "127.0.0.1" in rejected.detail


def test_a_sixtofour_address_is_judged_by_what_it_wraps() -> None:
    # 2002:c0a8:0101:: wraps 192.168.1.1.
    rejected = address_rejection(ipaddress.ip_address("2002:c0a8:0101::"))
    assert rejected is not None
    assert rejected.reason == "private_address"


def test_a_public_host_resolving_to_a_private_address_is_refused() -> None:
    # The reason the check happens after resolution: the name says nothing.
    rejected = url_rejection("https://internal.example.com/", ["10.1.2.3"])
    assert rejected is not None
    assert rejected.reason == "private_address"


def test_every_resolved_address_is_checked_not_only_the_first() -> None:
    # A host answering with one public and one private address is reachable on a retry, and which
    # address a connection picks is not ours to decide.
    rejected = url_rejection("https://mixed.example.com/", ["93.184.216.34", "127.0.0.1"])
    assert rejected is not None
    assert rejected.reason == "loopback_address"


def test_credentials_in_a_url_are_refused() -> None:
    rejected = url_rejection("https://user:pw@example.com/", ["93.184.216.34"])
    assert rejected is not None
    assert rejected.reason == "credentials_in_url"


def test_a_malformed_host_does_not_raise() -> None:
    assert url_rejection("http://[::1") is not None  # urlsplit raises before there is a host
    assert url_rejection("http:///path") is not None


def test_an_address_that_is_not_an_address_is_refused_rather_than_trusted() -> None:
    rejected = url_rejection("https://example.com/", ["not-an-ip"])
    assert rejected is not None
    assert rejected.reason == "unresolvable_address"


def test_the_redirect_cap_is_the_documented_one() -> None:
    assert MAX_REDIRECTS == 5


def test_rejection_reads_as_one_line() -> None:
    assert str(Rejection("loopback_address", "127.0.0.1")) == "loopback_address: 127.0.0.1"


class _Resolver(SafeResolver):
    """SafeResolver with DNS replaced, so the decision is tested without a network."""

    def __init__(self, answers: list[str]) -> None:
        super().__init__()
        self._answers = answers

    def resolve(self, host: str, port: int) -> list[str]:
        return self._answers


def test_the_resolver_raises_invalid_input_not_access_restricted() -> None:
    # Nothing out there is refusing us; we were handed a URL that points somewhere it should not,
    # and no retry changes that. The error class decides whether the consumer retries.
    with pytest.raises(InvalidInputError, match="private_address"):
        _Resolver(["10.0.0.1"]).check("https://internal.example.com/")


def test_the_resolver_passes_an_ordinary_host() -> None:
    _Resolver(["93.184.216.34"]).check("https://example.com/contact")


def test_the_resolver_refuses_a_bad_scheme_before_asking_dns() -> None:
    class _Never(SafeResolver):
        def resolve(self, host: str, port: int) -> list[str]:
            raise AssertionError("DNS must not be asked for a scheme we never fetch")

    with pytest.raises(InvalidInputError, match="scheme_not_allowed"):
        _Never().check("file:///etc/passwd")
