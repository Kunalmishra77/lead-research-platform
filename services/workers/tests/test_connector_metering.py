"""Metering of paid calls (docs/11): charged once, never silently skipped, always attributed."""

from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.jobs.errors import AccessRestrictedError, InvalidInputError
from app.metering import usage as usage_module
from app.metering.usage import SqlUsageRecorder, call_unit_key
from tests.connector_support import COST, FREE, JOB, ORG, URL, RecordingUsage, make_client, make_ctx


@respx.mock
async def test_a_paid_call_is_metered_once_per_unit_key() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    usage = RecordingUsage()
    ctx = make_ctx()
    async with make_client(usage=usage) as client:
        await client.get(URL, cost=COST, ctx=ctx, params={"q": "cafe"})
        await client.get(URL, cost=COST, ctx=ctx, params={"q": "cafe"})
        await client.get(URL, cost=COST, ctx=ctx, params={"q": "salon"})

    assert [call["org_id"] for call in usage.calls] == [ORG, ORG, ORG]
    assert [call["research_job_id"] for call in usage.calls] == [JOB, JOB, JOB]
    assert usage.calls[0]["cost_micros"] == 4000
    # The repeated call carries the same unit key, so the recorder charges it only once.
    assert usage.calls[0]["unit_key"] == usage.calls[1]["unit_key"]
    assert usage.calls[2]["unit_key"] != usage.calls[0]["unit_key"]


@respx.mock
async def test_a_paid_call_without_attribution_is_refused_before_it_is_made() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    usage = RecordingUsage()
    async with make_client(usage=usage) as client:
        with pytest.raises(InvalidInputError, match="meter against"):
            await client.get(URL, cost=COST)
        with pytest.raises(InvalidInputError, match="meter against"):
            await client.get(URL, cost=COST, ctx=make_ctx(research_job_id=None))

    # Money is never spent off the books: the request is not even sent.
    assert route.call_count == 0
    assert usage.calls == []


@respx.mock
async def test_a_free_call_needs_no_attribution() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    usage = RecordingUsage()
    async with make_client(usage=usage) as client:
        await client.get(URL, cost=FREE)
        await client.get(URL, cost=FREE, ctx=make_ctx())
    assert usage.calls == []


@respx.mock
async def test_a_restricted_response_is_not_metered() -> None:
    respx.get(URL).mock(return_value=httpx.Response(403))
    usage = RecordingUsage()
    async with make_client(usage=usage) as client:
        with pytest.raises(AccessRestrictedError, match="http_403"):
            await client.get(URL, cost=COST, ctx=make_ctx())
    assert usage.calls == []


@respx.mock
async def test_a_post_body_is_part_of_the_unit_key() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    usage = RecordingUsage()
    ctx = make_ctx()
    async with make_client(usage=usage) as client:
        await client.post(URL, cost=COST, ctx=ctx, json={"q": "cafe"})
        await client.post(URL, cost=COST, ctx=ctx, json={"q": "salon"})
    assert usage.calls[0]["unit_key"] != usage.calls[1]["unit_key"]


class FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeConnection:
    """Captures the two statements SqlUsageRecorder runs, with their bound parameters."""

    def __init__(self, claimed: bool = True) -> None:
        self.claimed = claimed
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> FakeResult:
        sql = " ".join(str(statement).split())
        self.executed.append((sql, params))
        if "usage_unit_keys" in sql:
            return FakeResult(1 if self.claimed else 0)
        return FakeResult(1)


def patch_tenant_transaction(
    monkeypatch: pytest.MonkeyPatch, conn: FakeConnection
) -> list[str | None]:
    """Replaces tenant_transaction, recording the org it was opened with."""
    seen: list[str | None] = []

    @asynccontextmanager
    async def fake(engine: AsyncEngine, org_id: str) -> Any:
        seen.append(org_id)
        yield conn

    monkeypatch.setattr(usage_module, "tenant_transaction", fake)
    return seen


async def test_a_usage_event_is_written_inside_the_tenant_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection()
    seen = patch_tenant_transaction(monkeypatch, conn)
    recorder = SqlUsageRecorder(engine=None)  # type: ignore[arg-type]

    recorded = await recorder.record(
        org_id=ORG,
        research_job_id=JOB,
        meter="api_example_source",
        unit_key=call_unit_key("example_source", URL),
        cost_micros=4000,
    )

    assert recorded is True
    assert seen == [ORG]  # RLS is set from the org, not inferred
    claim, event = conn.executed
    assert "insert into app.usage_unit_keys" in claim[0]
    assert "on conflict do nothing" in claim[0]
    assert "insert into app.usage_events" in event[0]
    assert claim[1]["org"] == ORG
    assert event[1]["org"] == ORG
    assert event[1]["cost"] == 4000
    # Internal cost only: credits for delivered leads are booked by the executor (docs/11).
    assert event[1]["credits"] == 0
    # Both rows share one id, so the dedupe row points at the event it claimed.
    assert claim[1]["event"] == event[1]["id"]


async def test_a_repeated_unit_key_writes_no_second_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection(claimed=False)
    patch_tenant_transaction(monkeypatch, conn)
    recorder = SqlUsageRecorder(engine=None)  # type: ignore[arg-type]

    recorded = await recorder.record(
        org_id=ORG,
        research_job_id=JOB,
        meter="api_example_source",
        unit_key="example_source:same",
        cost_micros=4000,
    )

    assert recorded is False
    assert len(conn.executed) == 1  # the claim lost; no usage_events row follows


async def test_usage_without_a_job_is_still_recorded_against_the_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection()
    seen = patch_tenant_transaction(monkeypatch, conn)
    recorder = SqlUsageRecorder(engine=None)  # type: ignore[arg-type]

    recorded = await recorder.record(
        org_id=ORG,
        research_job_id=None,
        meter="ai",
        unit_key="ai:parse:one",
        cost_micros=4000,
        org_level=True,
    )

    # A parse happens before any job exists (ADR-0005). The spend is the org's either way, so
    # it is booked; only the usage_unit_keys dedupe row, whose key needs a job, is skipped.
    assert recorded is True
    assert seen == [ORG]
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert "insert into app.usage_events" in sql
    assert params["org"] == ORG
    assert params["cost"] == 4000


async def test_usage_without_an_org_is_refused_rather_than_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection()
    patch_tenant_transaction(monkeypatch, conn)
    recorder = SqlUsageRecorder(engine=None)  # type: ignore[arg-type]

    # Without an org there is nobody to attribute the spend to, and RLS has no context to set.
    with pytest.raises(InvalidInputError, match="no org"):
        await recorder.record(
            org_id="",
            research_job_id=None,
            meter="ai",
            unit_key="ai:orphan",
            cost_micros=4000,
        )
    assert conn.executed == []


@respx.mock
async def test_a_paid_call_through_a_client_without_a_recorder_is_refused() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    async with make_client(usage=None) as client:
        with pytest.raises(InvalidInputError, match="no usage recorder"):
            await client.get(URL, cost=COST, ctx=make_ctx())


async def test_a_connector_without_a_job_is_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConnection()
    patch_tenant_transaction(monkeypatch, conn)
    recorder = SqlUsageRecorder(engine=None)  # type: ignore[arg-type]

    # A connector's unit key is deterministic so that a retry dedupes. Without a job there is
    # no dedupe row, so the same call could be charged twice: the org-level path is not for it.
    with pytest.raises(InvalidInputError, match="no research job"):
        await recorder.record(
            org_id=ORG,
            research_job_id=None,
            meter="api_example_source",
            unit_key=call_unit_key("example_source", URL),
            cost_micros=4000,
        )
    assert conn.executed == []
