"""A domain's mail records (task 3.9, docs/06 §5.3).

The patterns are the whole content of this module, so they are tested against the hostnames the
live graph actually uses rather than against invented ones. No test here resolves anything: a
DNS query leaves the machine, is slow, and its answer changes under you (CLAUDE.md).
"""

import pytest

from app.crawl.dns_records import (
    CONFIDENCE_DNS,
    PROVIDERS,
    SELF_HOSTED,
    MailDns,
    provider_for,
)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        # Every one of these is the preferred MX of a real domain in the graph, with the count
        # of domains using it as of 2026-10-13.
        ("aspmx.l.google.com", "Google Workspace"),  # 55
        ("smtp.google.com", "Google Workspace"),  # 13
        ("mx1.hostinger.com", "Hostinger"),  # 35
        ("mx1.hostinger.in", "Hostinger"),  # 15
        ("anytimefitness-com.mail.protection.outlook.com", "Microsoft 365"),  # 22 in total
        ("smtp.secureserver.net", "GoDaddy"),  # 12
        ("mx.zoho.in", "Zoho Mail"),  # 6
        ("mx.zoho.com", "Zoho Mail"),  # 2
        ("us2.mx1.mailhostbox.com", "Directi"),  # 4
        ("mx0a-0020ab01.pphosted.com", "Proofpoint"),  # 2
    ],
)
def test_the_hosts_the_graph_uses_are_recognised(host: str, expected: str) -> None:
    assert provider_for(host, "example.in") == expected


def test_a_business_running_its_own_mail_is_said_to_be_doing_so() -> None:
    """Several of the graph's domains point at `mail.<their own domain>`.

    "They run their own" is a different fact from "we do not know", and leaving the field empty
    would have conflated them.
    """
    assert provider_for("mail.19fitnessgym.com", "19fitnessgym.com") == SELF_HOSTED
    assert provider_for("19fitnessgym.com", "19fitnessgym.com") == SELF_HOSTED


def test_a_host_that_matches_nothing_is_not_guessed_at() -> None:
    """Three of the graph's domains use `jellyfish.systems`, which is not in the list.

    None, not "other": a label invented here would be stored as a fact with a confidence of 0.9
    beside it.
    """
    assert provider_for("mx1-hosting.jellyfish.systems", "example.in") is None
    assert provider_for("weird.example.net", "example.in") is None


def test_a_provider_wins_over_the_domains_own_name() -> None:
    """A provider hands out vanity hosts inside the customer's domain --
    `theirdomain-com.mail.protection.outlook.com` is Microsoft's, not theirs. Checked in that
    order, or every Microsoft 365 customer would read as self-hosted."""
    assert (
        provider_for("theirs-com.mail.protection.outlook.com", "theirs.com") == "Microsoft 365"
    )


def test_the_longest_matching_suffix_wins() -> None:
    """`mx.zoho.in` must be Zoho and not a stray match on a shorter entry, and
    `mail.protection.outlook.com` must be matched before `protection.outlook.com`."""
    assert provider_for("mx.zoho.in", "x.in") == "Zoho Mail"
    assert provider_for("a.mail.protection.outlook.com", "x.in") == "Microsoft 365"


def test_a_trailing_dot_and_upper_case_do_not_hide_a_provider() -> None:
    """DNS answers are returned with a trailing dot and are case-insensitive."""
    assert provider_for("ASPMX.L.GOOGLE.COM.", "x.in") == "Google Workspace"


def test_a_suffix_matches_a_label_boundary_rather_than_a_substring() -> None:
    """`notgoogle.com` is not Google. Without the dot in the suffix check it would be."""
    assert provider_for("mx.notl.google.com.evil.net", "x.in") is None
    assert provider_for("mx.fakehostinger.com", "x.in") is None


def test_every_pattern_is_a_lowercase_hostname() -> None:
    """The host is lower-cased before matching, so an entry with a capital would never match."""
    for suffix, name in PROVIDERS:
        assert suffix == suffix.lower(), suffix
        assert "." in suffix, suffix
        assert not suffix.startswith("."), suffix
        assert name.strip() == name and name, suffix


def test_no_two_patterns_disagree_about_one_host() -> None:
    """Two entries where one is a suffix of the other are fine -- longest wins -- but two
    identical entries naming different providers would make the answer depend on sort order."""
    by_suffix: dict[str, str] = {}
    for suffix, name in PROVIDERS:
        assert by_suffix.get(suffix, name) == name, f"{suffix} claimed by two providers"
        by_suffix[suffix] = name


def test_the_preferred_exchanger_is_the_first() -> None:
    records = MailDns(exchangers=("alt1.example.net", "primary.example.net"), provider=None)
    assert records.preferred == "alt1.example.net"


def test_dns_is_believed_more_than_a_page_and_less_than_certainty() -> None:
    """It came from the domain's own nameservers rather than from text that might be a
    testimonial. Short of 1.0 because records change and this one was true when it was read."""
    assert 0.8 < CONFIDENCE_DNS < 1.0
