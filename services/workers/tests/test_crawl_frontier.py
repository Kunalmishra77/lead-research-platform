"""The crawl frontier (docs/06 sections 4.1 and 4.3, task 3.1).

Runs on fakeredis by default and on a real Redis with REDIS_TEST_URL set, because the claim is a
Lua script and a script that only works against a fake is not much use.
"""

import asyncio

import pytest
from redis.asyncio import Redis

from app.crawl.frontier import (
    HOSTS_KEY,
    Frontier,
    host_of,
    seen_key,
    urls_key,
)

JOB = "01a11663-0000-7000-8000-000000000001"
CONTACT = "https://clinic.example/contact"
ABOUT = "https://clinic.example/about"
OTHER = "https://other.example/contact"


def test_keys_are_the_documented_shape() -> None:
    assert HOSTS_KEY == "crawl:hosts"
    assert urls_key("clinic.example") == "crawl:urls:clinic.example"
    assert seen_key(JOB) == f"crawl:seen:{JOB}"
    assert host_of("https://Clinic.Example:8443/a") == "clinic.example:8443"


@pytest.mark.asyncio
async def test_a_queued_url_comes_back_once(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    assert await frontier.add(JOB, [(CONTACT, 0.0)]) == 1

    claimed = await frontier.claim()
    assert claimed is not None
    assert claimed.url == CONTACT
    assert claimed.host == "clinic.example"
    # Taken, not copied: nothing else can claim it.
    assert await frontier.claim() is None


@pytest.mark.asyncio
async def test_the_best_url_of_a_host_goes_first(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    await frontier.add(JOB, [(ABOUT, 2.0), (CONTACT, 0.0)])
    first = await frontier.claim()
    assert first is not None
    # Contact before about, because a contact page is what makes a lead usable.
    assert first.url == CONTACT


@pytest.mark.asyncio
async def test_a_url_already_queued_for_this_job_is_not_queued_again(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    assert await frontier.add(JOB, [(CONTACT, 0.0)]) == 1
    assert await frontier.add(JOB, [(CONTACT, 0.0)]) == 0
    # And not after it has been fetched either: the seen set is what stops a link found on three
    # pages costing three fetches.
    await frontier.claim()
    assert await frontier.add(JOB, [(CONTACT, 0.0)]) == 0


@pytest.mark.asyncio
async def test_another_job_is_not_bound_by_this_one_s_memory(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    await frontier.add(JOB, [(CONTACT, 0.0)])
    other_job = "01a11663-0000-7000-8000-000000000002"
    assert await frontier.add(other_job, [(CONTACT, 0.0)]) == 1


@pytest.mark.asyncio
async def test_a_host_is_not_claimed_twice_while_it_cools_down(redis: Redis) -> None:
    # This is the per-host concurrency rule, and the reason the claim is one script: a second
    # worker arriving in the same millisecond must see the host as not due.
    frontier = Frontier(redis, cooldown_s=60.0)
    await frontier.add(JOB, [(CONTACT, 0.0), (ABOUT, 1.0)])

    first = await frontier.claim()
    assert first is not None
    assert await frontier.claim() is None
    # The second URL is still there, waiting for the clock.
    assert await frontier.pending("clinic.example") == 1


@pytest.mark.asyncio
async def test_a_cooling_host_does_not_block_another_host(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=60.0)
    await frontier.add(JOB, [(CONTACT, 0.0), (OTHER, 0.0)])

    hosts = {c.host for c in [await frontier.claim(), await frontier.claim()] if c}
    assert hosts == {"clinic.example", "other.example"}


@pytest.mark.asyncio
async def test_concurrent_claims_never_hand_out_the_same_url(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    urls = [(f"https://host{i}.example/contact", 0.0) for i in range(12)]
    await frontier.add(JOB, urls)

    claimed = await asyncio.gather(*[frontier.claim() for _ in range(12)])
    got = [c.url for c in claimed if c is not None]
    assert len(got) == len(set(got)) == 12


@pytest.mark.asyncio
async def test_a_link_to_a_cooling_host_gets_no_free_fetch(redis: Redis) -> None:
    # The reason `add` uses nx: overwriting a host's score with `now` would reset its cooldown
    # every time a link to it turned up, which is how a polite crawler stops being one.
    frontier = Frontier(redis, cooldown_s=60.0)
    await frontier.add(JOB, [(CONTACT, 0.0)])
    await frontier.claim()
    await frontier.add(JOB, [(ABOUT, 0.0)])
    assert await frontier.claim() is None


@pytest.mark.asyncio
async def test_an_emptied_host_leaves_the_clock(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    await frontier.add(JOB, [(CONTACT, 0.0)])
    await frontier.claim()
    assert await frontier.hosts() == 1  # still there, holding its cooldown
    # The next claim finds it empty and removes it, which is where the tidying happens.
    assert await frontier.claim() is None
    assert await frontier.hosts() == 0


@pytest.mark.asyncio
async def test_defer_pushes_a_host_out_for_a_long_crawl_delay(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    await frontier.add(JOB, [(CONTACT, 0.0), (ABOUT, 1.0)])
    await frontier.claim()
    await frontier.defer("clinic.example", 60.0)
    assert await frontier.claim() is None


@pytest.mark.asyncio
async def test_defer_never_shortens_a_wait_another_worker_set(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    await frontier.add(JOB, [(CONTACT, 0.0), (ABOUT, 1.0)])
    await frontier.claim()
    await frontier.defer("clinic.example", 60.0)
    await frontier.defer("clinic.example", 0.0)  # a shorter delay must not win
    assert await frontier.claim() is None


@pytest.mark.asyncio
async def test_pending_counts_across_hosts_and_per_host(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    await frontier.add(JOB, [(CONTACT, 0.0), (ABOUT, 1.0), (OTHER, 0.0)])
    assert await frontier.pending() == 3
    assert await frontier.pending("clinic.example") == 2
    assert await frontier.pending("nobody.example") == 0


@pytest.mark.asyncio
async def test_forget_lets_a_site_be_crawled_again(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    await frontier.add(JOB, [(CONTACT, 0.0)])
    await frontier.claim()
    await frontier.forget(JOB)
    assert await frontier.add(JOB, [(CONTACT, 0.0)]) == 1


@pytest.mark.asyncio
async def test_an_empty_batch_and_a_url_with_no_host_cost_nothing(redis: Redis) -> None:
    frontier = Frontier(redis, cooldown_s=0.0)
    assert await frontier.add(JOB, []) == 0
    # A relative URL has no host to pace against. It is counted as handled rather than queued,
    # because the caller has been told it was seen and must not keep offering it.
    assert await frontier.add(JOB, [("/contact", 0.0)]) == 1
    assert await frontier.hosts() == 0
    assert await frontier.claim() is None


@pytest.mark.asyncio
async def test_an_empty_frontier_claims_nothing(redis: Redis) -> None:
    assert await Frontier(redis).claim() is None
