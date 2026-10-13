"""The batched writers, against a real Postgres.

Skipped unless `DATABASE_URL_WORKERS` is set, like the rest of the live suite. That is also the
hole these tests fill: storing candidates has always been exercised through a fake graph, so
nothing checked what the statements actually do, and three separate defects reached production
through that gap in one afternoon -- a `source_url` the database refuses, an insert that returns
no rows, and two current values where there may only ever be one.

What is worth testing here is only what batching can break. The per-candidate version got these
right by running once per candidate; doing a hundred at a time has to arrive at the same place.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from uuid6 import uuid7

from app.config import Settings
from app.connectors.types import Candidate, FieldValue
from app.db import batch, best
from app.db.engine import create_engine
from tests.planner_support import FakeReference as PlannerReference

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_WORKERS"),
    reason="needs a database; set DATABASE_URL_WORKERS to run",
)

OBSERVED = datetime(2026, 9, 23, tzinfo=UTC)

#: What the fixture hands a test: a worker connection with the tenant already set, and the org,
#: workspace and job to write into.
Tenant = tuple[AsyncConnection, str, str, str]


def value(field: str, text_value: str, *, url: str = "https://example.test/p") -> FieldValue:
    return FieldValue(
        entity_type="company",
        field=field,
        value=text_value,
        source_key="overture",
        source_url=url,
        observed_at=OBSERVED,
        method="provider",
        derivation="found",
        confidence=0.85,
    )


def candidate(
    external_id: str, name: str, *, url: str | None = None, fields: tuple[FieldValue, ...] = ()
) -> Candidate:
    return Candidate(
        source_key="overture",
        external_id=external_id,
        name=name,
        url=url,
        source_url="https://example.test/p",
        observed_at=OBSERVED,
        values=fields or (value("name", name),),
    )


@pytest_asyncio.fixture
async def tenant() -> AsyncIterator[Tenant]:
    """A worker connection with a tenant set, inside a transaction that is always rolled back.

    The org, workspace and job are read with the migrations role, because the worker role cannot
    discover them: `app.org_id` has to be set before RLS will show a workspace, and in
    production the org arrives in the job envelope rather than being looked up. The writing
    itself runs as `app_worker`, so the grants and policies under test are the real ones.
    """
    admin_url = os.environ.get("DATABASE_URL_MIGRATIONS")
    if not admin_url:
        pytest.skip("needs DATABASE_URL_MIGRATIONS to find a tenant to write into")
    admin = create_async_engine(
        admin_url.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={"statement_cache_size": 0},
    )
    async with admin.connect() as c:
        row = (
            await c.execute(
                text(
                    "select w.org_id, w.id, j.id from app.workspaces w"
                    " join app.research_jobs j on j.workspace_id = w.id limit 1"
                )
            )
        ).first()
    await admin.dispose()
    if row is None:
        pytest.skip("no workspace with a research job to write into")
    org, workspace, job = str(row[0]), str(row[1]), str(row[2])

    engine: AsyncEngine = create_engine(Settings())
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(text("select set_config('app.org_id', :org, true)"), {"org": org})
        try:
            yield connection, org, workspace, job
        finally:
            # Always rolled back: these tests must leave the database as they found it, and a
            # transaction left open takes locks with it.
            await transaction.rollback()
    await engine.dispose()


async def _source_id(conn: AsyncConnection) -> str:
    row = (await conn.execute(text("select id from app.sources where key = 'overture'"))).first()
    assert row is not None, "run `pnpm db:seed`"
    return str(row[0])


async def _store(tenant: Tenant, candidates: list[Candidate]) -> list[batch.Prepared]:
    conn, org, workspace, job = tenant
    source_id = await _source_id(conn)

    pending = batch.prepare(candidates)
    known = await batch.known_companies(conn, [p.place_id for p in pending])
    for p in pending:
        p.company_id = known.get(p.place_id, "")
    await batch.insert_companies(conn, [p for p in pending if not p.company_id])
    await batch.upsert_locations(conn, pending)
    await batch.record_domains(conn, pending)
    await batch.write_values(conn, batch.value_rows(pending), source_id=source_id)
    await batch.deliver(conn, pending, org_id=org, workspace_id=workspace, research_job_id=job)
    await batch.link(conn, pending, org_id=org, research_job_id=job)
    return pending


async def test_a_batch_returns_a_row_for_every_candidate(tenant: Tenant) -> None:
    pending = await _store(
        tenant,
        [candidate(str(uuid7()), f"Gym {i}") for i in range(5)],
    )
    assert len(pending) == 5
    assert all(p.company_id for p in pending)
    assert all(p.lead_id for p in pending)


async def test_two_candidates_on_one_domain_become_one_company(tenant: Tenant) -> None:
    """Two branches of a chain with one website are one company with two locations.

    `ON CONFLICT DO UPDATE` refuses to touch a row twice in one statement, so the batch has to
    deduplicate by domain before it sends anything.
    """
    pending = await _store(
        tenant,
        [
            candidate(str(uuid7()), "Chain Gym Sector 1", url="https://chaingym.in"),
            candidate(str(uuid7()), "Chain Gym Sector 2", url="https://chaingym.in"),
        ],
    )
    assert pending[0].company_id == pending[1].company_id
    assert pending[0].location_id != pending[1].location_id


async def test_only_the_last_value_of_a_pair_stays_current(tenant: Tenant) -> None:
    """The bug this file was written for."""
    pending = await _store(
        tenant,
        [
            candidate(
                str(uuid7()),
                "Chain Gym Sector 1",
                url="https://chain2.in",
                fields=(value("phone", "+911111111111"),),
            ),
            candidate(
                str(uuid7()),
                "Chain Gym Sector 2",
                url="https://chain2.in",
                fields=(value("phone", "+912222222222"),),
            ),
        ],
    )
    company_id = pending[0].company_id
    rows = (
        await tenant[0].execute(
            text(
                "select value #>> '{}', is_current from app.field_values"
                " where entity_type = 'company' and entity_id = :c and field = 'phone'"
                " order by is_current desc"
            ),
            {"c": company_id},
        )
    ).all()
    current = [r[0] for r in rows if r[1]]
    assert len(current) == 1, f"expected one current phone, got {current}"
    # The last one written wins, as it did when each candidate ran its own retirement.
    assert current[0] == "+912222222222"


async def test_the_same_place_twice_is_one_company_and_one_location(
    tenant: Tenant,
) -> None:
    """What makes a task idempotent: a retry must not double anything."""
    place = str(uuid7())
    first = await _store(tenant, [candidate(place, "Iron Gym")])
    second = await _store(tenant, [candidate(place, "Iron Gym")])
    assert first[0].company_id == second[0].company_id
    assert first[0].location_id == second[0].location_id
    assert first[0].is_new is True
    # Delivered once; the second run found the workspace already had it.
    assert second[0].is_new is False


async def test_a_lead_already_held_is_not_billed_again(tenant: Tenant) -> None:
    """`is_new` is what the job is charged on, so it has to come from the insert itself."""
    place = str(uuid7())
    await _store(tenant, [candidate(place, "Paid Once")])
    again = await _store(tenant, [candidate(place, "Paid Once")])
    assert again[0].is_new is False
    assert again[0].lead_id is not None


async def test_every_candidate_gets_its_values(tenant: Tenant) -> None:
    pending = await _store(
        tenant,
        [
            candidate(
                str(uuid7()),
                f"Gym {i}",
                fields=(value("name", f"Gym {i}"), value("phone", f"+9111111111{i:02d}")),
            )
            for i in range(10)
        ],
    )
    assert all(p.values_written == 2 for p in pending)
    company_ids = [p.company_id for p in pending]
    rows = (
        await tenant[0].execute(
            text(
                "select count(*) from app.field_values"
                " where entity_type = 'company' and is_current and entity_id = any(:ids)"
            ).bindparams(),
            {"ids": company_ids},
        )
    ).scalar()
    assert rows == 20


async def test_the_same_place_twice_in_one_batch_is_one_company(tenant: Tenant) -> None:
    """The regression batching introduced, and the reason this file exists.

    The per-candidate version got this for free: the second candidate looked the place up,
    found the location the first had just written, and reused its company. A batch does every
    lookup before every write, so nothing stopped it creating a company per candidate and a
    location per *place* -- leaving companies with no location at all, each a duplicate of a
    real business with a lead pointing at it. 76 of them, 7.7% of the live graph.
    """
    place = str(uuid7())
    pending = await _store(
        tenant,
        [candidate(place, "Twice Gym"), candidate(place, "Twice Gym")],
    )

    assert pending[0].company_id == pending[1].company_id
    assert pending[0].location_id == pending[1].location_id
    assert pending[0].lead_id == pending[1].lead_id


async def test_every_company_a_batch_creates_gets_a_location(tenant: Tenant) -> None:
    """A company with no location is the shape the bug left behind, so look for that shape.

    Asserting the ids match says the dedupe worked; this says the graph is not carrying
    orphans, which is the thing anyone would actually notice.
    """
    conn = tenant[0]
    place = str(uuid7())
    pending = await _store(
        tenant,
        [candidate(place, "Shared Place") for _ in range(4)]
        + [candidate(str(uuid7()), f"Own Place {i}") for i in range(2)],
    )

    company_ids = sorted({p.company_id for p in pending})
    assert len(company_ids) == 3, "one company per place, not one per candidate"

    rows = (
        await conn.execute(
            text(
                "select c.id from app.companies c"
                " where c.id = any(:ids)"
                "   and not exists (select 1 from app.company_locations l"
                "                    where l.company_id = c.id)"
            ),
            {"ids": company_ids},
        )
    ).all()
    assert rows == [], "a company a batch created must have the location it was created for"


async def test_a_place_already_owned_hands_its_owner_back(tenant: Tenant) -> None:
    """The second half of the concurrency fix: the location upsert says who really owns a place.

    Set up the state the race leaves behind -- a place that already belongs to a company the
    lookup did not return -- and the writer has to adopt that company rather than keep the one
    it just created. Without this, the company it created is left with no location at all.
    """
    conn, _org, _workspace, _job = tenant
    place = str(uuid7())
    incumbent = str(uuid7())
    await conn.execute(
        text(
            "insert into app.companies (id, canonical_name, normalized_name)"
            " values (:id, 'Incumbent Gym', 'incumbent gym')"
        ),
        {"id": incumbent},
    )
    await conn.execute(
        text(
            "insert into app.company_locations (id, company_id, google_place_id)"
            " values (:id, :company, :place)"
        ),
        {"id": str(uuid7()), "company": incumbent, "place": place},
    )

    # Deliberately skipping `known_companies`, which is exactly what a racing task effectively
    # does: it ran its lookup before the location above existed.
    pending = batch.prepare([candidate(place, "Latecomer Gym")])
    await batch.insert_companies(conn, pending)
    newborn = pending[0].company_id
    await batch.upsert_locations(conn, pending)

    assert pending[0].company_id == incumbent, "the place says who owns it"
    assert newborn != incumbent, "the test is only meaningful if a second company was created"
    orphaned = (
        await conn.execute(
            text(
                "select count(*) from app.companies c where c.id = :id"
                " and not exists (select 1 from app.company_locations l"
                "                  where l.company_id = c.id)"
            ),
            {"id": incumbent},
        )
    ).scalar()
    assert orphaned == 0


async def test_two_tasks_cannot_claim_one_place_at_once() -> None:
    """The first half: a place is claimed before it is looked up, so the lookup is the last word.

    This is the only test in the suite that needs two live transactions, because the defect only
    exists between them. Neither writes anything: an advisory lock is transaction-scoped, so a
    rollback releases it and there is nothing to clean up.
    """
    place = str(uuid7())
    engine = create_engine(Settings())
    try:
        first = await engine.connect()
        second = await engine.connect()
        try:
            t1 = await first.begin()
            await batch.claim_places(first, [place])

            t2 = await second.begin()
            blocked = asyncio.create_task(batch.claim_places(second, [place]))
            done, _ = await asyncio.wait({blocked}, timeout=2.0)
            assert not done, "the second task claimed a place the first was holding"

            await t1.rollback()
            await asyncio.wait_for(blocked, timeout=10.0)
            await t2.rollback()
        finally:
            await first.close()
            await second.close()
    finally:
        await engine.dispose()


async def test_a_national_phone_number_reaches_the_column(tenant: Tenant) -> None:
    """The bug this rescued 473 numbers from, against the real column and its CHECK.

    Storage used to require a leading `+` and drop everything else, on the reasoning that a
    national number cannot be normalised without knowing the country. The country was on the
    candidate the whole time. 481 numbers were being dropped -- 43% of the companies that had a
    phone had no dialable one -- and every one of them was a business someone could have rung.
    """
    conn = tenant[0]
    place = str(uuid7())
    pending = batch.prepare(
        [
            Candidate(
                source_key="overture",
                external_id=place,
                name="National Format Clinic",
                phone="085277 87137",
                country="IN",
                source_url="https://example.test/p",
                observed_at=OBSERVED,
                values=(value("name", "National Format Clinic"),),
            )
        ]
    )
    await batch.insert_companies(conn, pending)
    await batch.upsert_locations(conn, pending)

    stored = (
        await conn.execute(
            text("select phone_e164 from app.company_locations where google_place_id = :p"),
            {"p": place},
        )
    ).scalar()
    assert stored == "+918527787137"


async def test_a_phone_without_a_country_is_still_refused(tenant: Tenant) -> None:
    """The old rule's reasoning was right about this half: without a country there is nothing
    to strip the trunk prefix by, and inventing one would put a real call through to the wrong
    country. Such a number keeps its `phone` field value and its provenance; it is simply not
    claimed to be E.164."""
    conn = tenant[0]
    place = str(uuid7())
    pending = batch.prepare(
        [
            Candidate(
                source_key="overture",
                external_id=place,
                name="No Country Clinic",
                phone="085277 87137",
                country=None,
                source_url="https://example.test/p",
                observed_at=OBSERVED,
                values=(value("name", "No Country Clinic"),),
            )
        ]
    )
    await batch.insert_companies(conn, pending)
    await batch.upsert_locations(conn, pending)

    stored = (
        await conn.execute(
            text("select phone_e164 from app.company_locations where google_place_id = :p"),
            {"p": place},
        )
    ).scalar()
    assert stored is None


async def test_the_best_value_per_field_is_the_most_confident_one(tenant: Tenant) -> None:
    """docs/04: per field, the current row with the highest confidence; ties go to the newest.

    `companies.best` has existed since migration 0012 and nothing had ever written to it -- all
    1,550 companies held `{}`. It is a derived cache and nothing here decides anything: the
    winner is picked by numbers somebody else set when they observed the value.
    """
    conn = tenant[0]
    place = str(uuid7())
    source_id = await _source_id(conn)

    def phone(number: str, confidence: float, day: int) -> FieldValue:
        return FieldValue(
            entity_type="company",
            field="phone",
            value=number,
            source_key="overture",
            source_url="https://example.test/p",
            observed_at=datetime(2026, 9, day, tzinfo=UTC),
            method="provider",
            derivation="found",
            confidence=confidence,
        )

    pending = batch.prepare(
        [
            candidate(
                place,
                "Best Value Clinic",
                fields=(value("name", "Best Value Clinic"), phone("+911141000001", 0.5, 1)),
            )
        ]
    )
    await batch.insert_companies(conn, pending)
    await batch.upsert_locations(conn, pending)
    await batch.write_values(conn, batch.value_rows(pending), source_id=source_id)
    # A second, more confident observation of the same field, as a crawl of the site would make.
    await batch.write_values(
        conn,
        [batch.ValueRow(str(uuid7()), pending[0].company_id, phone("+911141000002", 0.9, 2))],
        source_id=source_id,
    )

    assert await best.recompute(conn, [pending[0].company_id]) == 1

    stored = (
        await conn.execute(
            text("select best from app.companies where id = :c"), {"c": pending[0].company_id}
        )
    ).scalar()
    assert stored["phone"] == "+911141000002", "the more confident value wins"
    assert stored["name"] == "Best Value Clinic"


async def test_a_tie_on_confidence_goes_to_the_newest(tenant: Tenant) -> None:
    conn = tenant[0]
    place = str(uuid7())
    source_id = await _source_id(conn)

    def website(url: str, day: int) -> FieldValue:
        return FieldValue(
            entity_type="company",
            field="website",
            value=url,
            source_key="overture",
            source_url="https://example.test/p",
            observed_at=datetime(2026, 9, day, tzinfo=UTC),
            method="provider",
            derivation="found",
            confidence=0.85,
        )

    pending = batch.prepare([candidate(place, "Tie Clinic", fields=(website("https://a.in", 1),))])
    await batch.insert_companies(conn, pending)
    await batch.upsert_locations(conn, pending)
    await batch.write_values(conn, batch.value_rows(pending), source_id=source_id)
    await batch.write_values(
        conn,
        [batch.ValueRow(str(uuid7()), pending[0].company_id, website("https://b.in", 9))],
        source_id=source_id,
    )

    await best.recompute(conn, [pending[0].company_id])

    stored = (
        await conn.execute(
            text("select best from app.companies where id = :c"), {"c": pending[0].company_id}
        )
    ).scalar()
    assert stored["website"] == "https://b.in"


async def test_a_retired_value_is_not_the_best_one(tenant: Tenant) -> None:
    """`is_current` is the filter. A value that was true last month still says so in
    `field_values`, which is the point of the table, and must not be offered as the answer."""
    conn = tenant[0]
    place = str(uuid7())
    source_id = await _source_id(conn)
    pending = batch.prepare(
        [candidate(place, "Moved Clinic", fields=(value("phone", "+911141000003"),))]
    )
    await batch.insert_companies(conn, pending)
    await batch.upsert_locations(conn, pending)
    await batch.write_values(conn, batch.value_rows(pending), source_id=source_id)
    # The same field again, which retires the first.
    await batch.write_values(
        conn,
        [batch.ValueRow(str(uuid7()), pending[0].company_id, value("phone", "+911141000004"))],
        source_id=source_id,
    )

    await best.recompute(conn, [pending[0].company_id])

    stored = (
        await conn.execute(
            text("select best from app.companies where id = :c"), {"c": pending[0].company_id}
        )
    ).scalar()
    assert stored["phone"] == "+911141000004"


async def test_a_company_with_no_values_is_not_given_an_empty_best(tenant: Tenant) -> None:
    """Nothing to compute is not the same as computed and empty. `recompute` reports 0 so a
    caller's log says what happened, and the column keeps its default."""
    conn = tenant[0]
    assert await best.recompute(conn, []) == 0
    assert await best.recompute(conn, [str(uuid7())]) == 0


async def test_the_state_column_is_filled_from_the_winning_value(tenant: Tenant) -> None:
    """`_company_params` wrote a literal `None` into `companies.state` for every company ever
    stored: 1,546 of 1,550 had no state and no state-level filter could work.

    Through the geography, not straight from the value, because the sources do not agree on a
    spelling -- the live graph held `HR` 870 against `Haryana` 245 for one state.
    """
    conn = tenant[0]
    place = str(uuid7())
    source_id = await _source_id(conn)
    pending = batch.prepare(
        [candidate(place, "State Clinic", fields=(value("state", "MH"),))]
    )
    await batch.insert_companies(conn, pending)
    await batch.upsert_locations(conn, pending)
    await batch.write_values(conn, batch.value_rows(pending), source_id=source_id)

    await best.recompute(conn, [pending[0].company_id], geography=PlannerReference())

    row = (
        await conn.execute(
            text("select state, best from app.companies where id = :c"),
            {"c": pending[0].company_id},
        )
    ).first()
    assert row is not None
    assert row[0] == "Maharashtra", "the column holds the state's name"
    assert row[1]["state"] == "MH", "the evidence keeps the code the source reported"


async def test_a_state_value_that_is_not_a_state_is_not_stored(tenant: Tenant) -> None:
    """`Gurugram` (a city), `India` and `IN` (a country) and `CA` all arrived in this field on
    the live graph. Storing them would put a city in a state filter."""
    conn = tenant[0]
    source_id = await _source_id(conn)
    for junk in ("Gurugram", "India", "IN", "CA"):
        place = str(uuid7())
        pending = batch.prepare([candidate(place, f"Junk {junk}", fields=(value("state", junk),))])
        await batch.insert_companies(conn, pending)
        await batch.upsert_locations(conn, pending)
        await batch.write_values(conn, batch.value_rows(pending), source_id=source_id)

        await best.recompute(conn, [pending[0].company_id], geography=PlannerReference())

        state = (
            await conn.execute(
                text("select state from app.companies where id = :c"),
                {"c": pending[0].company_id},
            )
        ).scalar()
        assert state is None, f"{junk!r} is not a state and must not be stored as one"


async def test_without_a_geography_no_state_is_invented(tenant: Tenant) -> None:
    """`recompute` is callable without one -- a test about rows should not have to seed a
    geography -- and then it stores no state rather than the raw value."""
    conn = tenant[0]
    place = str(uuid7())
    source_id = await _source_id(conn)
    pending = batch.prepare([candidate(place, "No Geo Clinic", fields=(value("state", "MH"),))])
    await batch.insert_companies(conn, pending)
    await batch.upsert_locations(conn, pending)
    await batch.write_values(conn, batch.value_rows(pending), source_id=source_id)

    await best.recompute(conn, [pending[0].company_id])

    state = (
        await conn.execute(
            text("select state from app.companies where id = :c"), {"c": pending[0].company_id}
        )
    ).scalar()
    assert state is None
