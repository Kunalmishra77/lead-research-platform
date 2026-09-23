"""The research.parse handler and the reply protocol (ADR-0005).

A user is waiting on the other end of this, so the thing worth testing hardest is that they get
an answer — a good one or a classified failure — and never silence.
"""

import json
from typing import Any

import pytest
import structlog
from leadforge_contracts.research_parse_reply import ResearchParseReply
from leadforge_contracts.research_spec import ResearchSpec
from redis.asyncio import Redis

from app.ai.gateway import AiGateway
from app.handlers.research import register_research_handlers
from app.jobs.context import JobContext
from app.jobs.errors import InvalidInputError, TransientError
from app.jobs.progress import ProgressPublisher
from app.jobs.registry import HandlerRegistry
from app.jobs.rpc import REPLY_PREFIX, check_reply_key, reply_key, send_reply
from tests.ai_support import ORG, ScriptedProvider, answer, settings
from tests.conftest import EnvelopeFactory
from tests.connector_support import RecordingUsage

JOB = "22222222-2222-7222-8222-222222222222"

DRAFT: dict[str, Any] = {
    "intent": "prospecting",
    "industry_terms": ["restaurant"],
    "location": {"country": "IN", "states": [], "cities": ["Delhi"], "radius_km": None},
    "employee_count": {"gte": None, "lte": None},
    "has_website": True,
    "keywords": {"must": [], "should": [], "not": []},
    "fields": ["email"],
    "depth": None,
    "max_results": None,
    "exclude_existing": False,
    "seed_company": None,
    "unsupported": [],
    "ambiguities": [],
    "confidence": 0.97,
}


def make_gateway(redis: Redis, provider: ScriptedProvider) -> AiGateway:
    """The shipped spec_parse prompt and schema; only the model itself is scripted."""
    return AiGateway(provider=provider, redis=redis, settings=settings(), usage=RecordingUsage())


async def run_parse(
    redis: Redis,
    make_envelope: EnvelopeFactory,
    provider: ScriptedProvider,
    *,
    payload: dict[str, Any] | None = None,
    job_id: str = JOB,
) -> tuple[HandlerRegistry, dict[str, Any] | None, BaseException | None]:
    registry = HandlerRegistry()
    register_research_handlers(registry, make_gateway(redis, provider))
    envelope = make_envelope(
        "research.parse",
        job_id=job_id,
        org_id=ORG,
        research_job_id=None,
        payload=payload
        if payload is not None
        else {"raw_query": "restaurants in Delhi with a website", "reply_to": reply_key(job_id)},
    )
    ctx = JobContext(
        envelope=envelope,
        redis=redis,
        progress=ProgressPublisher(redis),
        log=structlog.get_logger("test"),
        message_id="1-0",
    )
    handler = registry.get("research.parse")
    assert handler is not None
    raised: BaseException | None = None
    try:
        await handler(ctx)
    except BaseException as exc:  # the test decides what to do with it
        raised = exc

    popped = await redis.lpop(reply_key(job_id))
    reply = json.loads(popped) if popped else None
    return registry, reply, raised


async def test_a_parse_answers_with_a_spec_the_contract_accepts(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    _, reply, raised = await run_parse(redis, make_envelope, ScriptedProvider(answer(DRAFT)))

    assert raised is None
    assert reply is not None
    assert reply["ok"] is True
    assert reply["job_id"] == JOB

    result = reply["result"]
    ResearchSpec.model_validate(result["spec"])
    assert result["spec"]["filters"]["location"]["cities"] == ["Delhi"]
    assert result["spec"]["filters"]["has_website"] is True
    # This draft asks for `email`, which discovery cannot collect, so the user is asked first
    # rather than charged for results missing the column they wanted.
    assert result["needs_confirmation"] is True
    assert result["confidence"] == pytest.approx(0.97)
    # Whoever stores a value from this parse can say which model and prompt produced it.
    assert result["provenance"]["prompt_version"].startswith("spec_parse@")
    assert result["provenance"]["model"]
    assert result["provenance"]["observed_at"]


async def test_a_request_we_can_fully_answer_runs_without_asking(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    doable = {**DRAFT, "fields": []}
    _, reply, _ = await run_parse(redis, make_envelope, ScriptedProvider(answer(doable)))

    assert reply is not None
    result = reply["result"]
    assert result["needs_confirmation"] is False
    assert all(row["mode"] != "unsupported" for row in result["feasibility"])


async def test_anything_we_cannot_do_forces_a_confirmation(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    seeded = {**DRAFT, "fields": [], "seed_company": {"name": "Acme", "website": None}}
    _, reply, _ = await run_parse(redis, make_envelope, ScriptedProvider(answer(seeded)))

    assert reply is not None
    result = reply["result"]
    # The model was confident and raised nothing, but we cannot search "like Acme" yet. A
    # filter we silently drop is one the user believes is running.
    assert any(row["mode"] == "unsupported" for row in result["feasibility"])
    assert result["needs_confirmation"] is True


async def test_the_reply_says_what_can_and_cannot_be_searched(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    _, reply, _ = await run_parse(redis, make_envelope, ScriptedProvider(answer(DRAFT)))

    assert reply is not None
    modes = {row["filter"]: row["mode"] for row in reply["result"]["feasibility"]}
    assert modes["filters.industry"] == "directly_searchable"
    assert modes["filters.location"] == "directly_searchable"
    # The user asked for a website filter: it works, but by discarding results they paid for.
    assert modes["filters.has_website"] == "post_filter"
    # They also asked for email, which discovery does not collect. Saying so is the point.
    assert modes["fields"] == "unsupported"


async def test_an_unclear_request_is_flagged_for_confirmation(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    vague = {
        **DRAFT,
        "industry_terms": [],
        "fields": [],
        "location": {"country": None, "states": [], "cities": [], "radius_km": None},
        "ambiguities": ["Which city should I search?"],
        "confidence": 0.3,
    }
    _, reply, _ = await run_parse(redis, make_envelope, ScriptedProvider(answer(vague)))

    assert reply is not None
    result = reply["result"]
    assert result["needs_confirmation"] is True
    assert result["ambiguities"] == ["Which city should I search?"]


async def test_a_constraint_we_cannot_apply_is_reported_rather_than_dropped(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    draft = {**DRAFT, "unsupported": ["rating above 4 stars"], "fields": []}
    _, reply, _ = await run_parse(redis, make_envelope, ScriptedProvider(answer(draft)))

    assert reply is not None
    result = reply["result"]
    assert result["unsupported"] == ["rating above 4 stars"]
    assert result["needs_confirmation"] is True
    assert any(row["mode"] == "unsupported" for row in result["feasibility"])


async def test_a_failure_is_replied_rather_than_left_to_time_out(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    provider = ScriptedProvider(TransientError("openai unavailable"), TransientError("also down"))
    _, reply, raised = await run_parse(redis, make_envelope, provider)

    # The caller learns in a second instead of waiting out its 20-second timeout...
    assert reply is not None
    assert reply["ok"] is False
    assert reply["error"]["error_class"] == "transient"
    # ...and the job ends there. Retrying would re-run a paid call for a caller that already
    # has its answer and has stopped listening.
    assert raised is None


async def test_an_error_reaching_the_user_is_redacted(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    def leaky() -> TransientError:
        return TransientError(
            "openai unavailable: GET https://api.openai.com/v1/responses?key=sk-secret-value "
            "for owner@example.com"
        )

    # Both tiers fail the same way, so the message that escapes is the leaky one.
    _, reply, _ = await run_parse(redis, make_envelope, ScriptedProvider(leaky(), leaky()))

    assert reply is not None
    message = reply["error"]["message"]
    # This string is handed to an end user by the API (docs/10 hygiene).
    assert "sk-secret-value" not in message
    assert "owner@example.com" not in message
    assert "[redacted]" in message


async def test_the_reply_matches_the_shared_contract(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    _, reply, _ = await run_parse(redis, make_envelope, ScriptedProvider(answer(DRAFT)))

    assert reply is not None
    # Drift here would be a 502 for every user, found in production rather than in CI.
    ResearchParseReply.model_validate(reply)


async def test_a_parse_is_capped_by_the_envelope_budget(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    registry = HandlerRegistry()
    register_research_handlers(registry, make_gateway(redis, ScriptedProvider(answer(DRAFT))))
    envelope = make_envelope(
        "research.parse",
        job_id=JOB,
        org_id=ORG,
        research_job_id=None,
        budget={"credits_remaining": 0, "cost_cap_micros": 1},
        payload={"raw_query": "cafes in Pune", "reply_to": reply_key(JOB)},
    )
    ctx = JobContext(
        envelope=envelope,
        redis=redis,
        progress=ProgressPublisher(redis),
        log=structlog.get_logger("test"),
        message_id="1-0",
    )
    handler = registry.get("research.parse")
    assert handler is not None
    await handler(ctx)

    popped = await redis.lpop(reply_key(JOB))
    assert popped is not None
    reply = json.loads(popped)
    # Without a job there is still a budget: the envelope's own id carries it, so a parse
    # cannot spend without limit just because no research job exists yet.
    assert reply["ok"] is False
    assert reply["error"]["error_class"] == "budget_exhausted"


async def test_a_reply_key_that_is_not_this_jobs_is_refused(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    other = "rpc:reply:99999999-9999-7999-8999-999999999999"
    _, _, raised = await run_parse(
        redis,
        make_envelope,
        ScriptedProvider(answer(DRAFT)),
        payload={"raw_query": "cafes in Pune", "reply_to": other},
    )

    # A reply key from a request payload would be a write primitive for anyone who can enqueue.
    assert isinstance(raised, InvalidInputError)
    assert await redis.exists(other) == 0


async def test_a_malformed_payload_fails_without_replying_anywhere(
    redis: Redis, make_envelope: EnvelopeFactory
) -> None:
    _, reply, raised = await run_parse(
        redis, make_envelope, ScriptedProvider(), payload={"raw_query": ""}
    )

    assert isinstance(raised, InvalidInputError)
    assert reply is None


def test_a_reply_key_belongs_to_exactly_one_job() -> None:
    assert reply_key(JOB).startswith(REPLY_PREFIX)
    assert check_reply_key(reply_key(JOB), JOB) == reply_key(JOB)
    with pytest.raises(InvalidInputError):
        check_reply_key(reply_key("other"), JOB)


async def test_a_reply_is_never_written_outside_the_reply_prefix(redis: Redis) -> None:
    with pytest.raises(InvalidInputError, match="refusing to write outside"):
        await send_reply(redis, "some:other:key", {"ok": True})
    assert await redis.exists("some:other:key") == 0


async def test_a_reply_expires_so_an_abandoned_one_does_not_linger(redis: Redis) -> None:
    key = reply_key(JOB)
    await send_reply(redis, key, {"reply_version": 1, "job_id": JOB, "ok": True})
    assert await redis.ttl(key) > 0
