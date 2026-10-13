"""Writing discovered businesses into the global company graph (docs/06 §3, ADR-0007).

Three tables, and the difference between them matters:

* `companies` is one business. Its only dedupe handle is `primary_domain`, deliberately a plain
  UNIQUE so that NULLs never collide — two businesses with no website are two businesses, not one.
* `company_locations` is one address of it, and for anything found on a map the real identity is
  `google_place_id`, also plain UNIQUE. This is what makes a job idempotent: the same place found
  twice, by two different searches or by the same search rerun, is one row.
* `field_values` is append-only. Nothing is ever overwritten — a new observation is inserted and
  the previous one has `is_current` flipped off, which is the only column `app_worker` may update
  (migration 0013). That is what lets a value keep the evidence it was true on a given day even
  after it changes.

A fourth table, and it is the one that makes any of this visible: `leads` says which workspace
was handed which company, by which job (ADR-0012). The graph is shared and holds only what public
sources reported, so nothing in it belongs to anyone; without a `leads` row a job's results cannot
be read back at all, and a second workspace searching the same market would find every business
already present and be given none of them.

It is also what billing counts. `app.credit_rates` prices a "delivered new lead" (docs/11), and a
lead is delivered to a *workspace* — so a company already in the graph is still a new lead for a
customer who has not been given it before, and one they already hold is not.

Everything runs inside `tenant_transaction`, which the graph policies do not need (`USING (true)`)
but `leads` does, and which keeps a task's rows committing or failing together.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from uuid6 import uuid7

from app.connectors.types import Candidate, FieldValue
from app.db import batch, best
from app.db.batch import normalized_name
from app.db.best import StateLookup
from app.db.tenant import tenant_transaction
from app.normalize.phones import to_e164


@dataclass(frozen=True, slots=True)
class Stored:
    """What one candidate turned into."""

    company_id: str
    location_id: str | None
    lead_id: str | None
    #: True when this workspace had not been given this company before — which is what a
    #: "delivered new lead" means and what the job is charged for. Deliberately not "new to the
    #: graph": a company another customer discovered first is still new to this one, and they
    #: received it just the same.
    is_new: bool
    values_written: int


class GraphRepo(Protocol):
    async def store(
        self,
        org_id: str,
        candidates: list[Candidate],
        *,
        source_id: str,
        workspace_id: str,
        research_job_id: str,
    ) -> list[Stored]: ...

    async def add_company_values(
        self,
        org_id: str,
        company_id: str,
        values: Sequence[FieldValue],
        *,
        source_id: str,
    ) -> int: ...


class SqlGraphRepo:
    def __init__(self, engine: AsyncEngine, *, geography: StateLookup | None = None) -> None:
        self._engine = engine
        #: Used only to place a state name (`app/db/best.py`). Optional because a test that
        #: cares about rows rather than about geography should not have to seed one, and
        #: `recompute` stores no state at all without it rather than guessing.
        self._geography = geography

    #: Candidates per transaction. Large, because the statement count no longer grows with it:
    #: a chunk costs about a dozen round trips whether it holds ten candidates or a hundred.
    #: What bounds it is how much one `insert ... values` should carry and how long a
    #: transaction should hold its locks, not how many questions the database is asked.
    CHUNK: Final[int] = 100


    async def store(
        self,
        org_id: str,
        candidates: list[Candidate],
        *,
        source_id: str,
        workspace_id: str,
        research_job_id: str,
    ) -> list[Stored]:
        """Writes candidates a chunk at a time, in a handful of statements for the whole chunk.

        One statement per kind of row, not per candidate (`app/db/batch.py`). Storing one
        business used to cost about twenty-six round trips; at the 452 ms this deployment sees
        to its database that is 11.7 seconds each, and forty-nine minutes for the two hundred
        and fifty a single search now returns. The count no longer grows with the batch.

        One transaction per chunk rather than one for everything. The all-or-nothing a single
        transaction bought is not worth what it costs here: every write is an upsert keyed on
        the source's own id, a retry re-runs them harmlessly, and the job link is keyed on
        (job, lead) -- while a transaction left open by a worker that died takes its locks with
        it, and one such orphan blocked every discovery task on this database for eleven
        minutes.
        """
        stored: list[Stored] = []
        for start in range(0, len(candidates), self.CHUNK):
            chunk = candidates[start : start + self.CHUNK]
            async with tenant_transaction(self._engine, org_id) as conn:
                stored.extend(
                    await self._store_chunk(
                        conn,
                        chunk,
                        source_id=source_id,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        research_job_id=research_job_id,
                    )
                )
        return stored

    async def _store_chunk(
        self,
        conn: AsyncConnection,
        candidates: Sequence[Candidate],
        *,
        source_id: str,
        org_id: str,
        workspace_id: str,
        research_job_id: str,
    ) -> list[Stored]:
        pending = batch.prepare(candidates)

        # Claimed before it is looked up, so that a task searching the same area cannot be
        # between the two: the lookup has to be the last word on who owns a place.
        await batch.claim_places(conn, [p.place_id for p in pending])

        # A place we have seen before belongs to the company it belonged to then, whatever its
        # website says now. One query answers that for the whole chunk.
        known = await batch.known_companies(conn, [p.place_id for p in pending])
        for p in pending:
            p.company_id = known.get(p.place_id, "")
        await batch.insert_companies(conn, [p for p in pending if not p.company_id])

        await batch.upsert_locations(conn, pending)
        await batch.record_domains(conn, pending)
        await batch.write_values(conn, batch.value_rows(pending), source_id=source_id)

        # The derived cache, after the observations it derives from and inside the same
        # transaction: a `best` that disagrees with the values it was computed from is worse
        # than no `best` at all, and a reader cannot tell which it is looking at.
        await best.recompute(
            conn, [p.company_id for p in pending], geography=self._geography
        )
        await batch.deliver(
            conn,
            pending,
            org_id=org_id,
            workspace_id=workspace_id,
            research_job_id=research_job_id,
        )
        await batch.link(conn, pending, org_id=org_id, research_job_id=research_job_id)

        return [
            Stored(
                company_id=p.company_id,
                location_id=p.location_id,
                lead_id=p.lead_id,
                is_new=p.is_new,
                values_written=p.values_written,
            )
            for p in pending
        ]

    async def add_company_values(
        self,
        org_id: str,
        company_id: str,
        values: Sequence[FieldValue],
        *,
        source_id: str,
    ) -> int:
        """Adds observations to a company already in the graph. Returns how many were written.

        What the crawler needs and `store()` does not provide: there is no candidate here, no new
        company, no location and no lead to deliver. The business was found by a search and paid
        for then; this is its own website telling us more about it, which costs nothing and
        delivers nothing new to the workspace.

        One transaction for the batch, like `store()`: a crawl that dies halfway should leave a
        company with the values of a page it finished, not half of one.
        """
        if not values:
            return 0
        rows = [
            batch.ValueRow(id=str(uuid7()), entity_id=company_id, value=value) for value in values
        ]
        async with tenant_transaction(self._engine, org_id) as conn:
            return await batch.write_values(conn, rows, source_id=source_id)


# `normalized_name` and `_country` live in `app/db/batch.py`, beside the statements that use
# them; `to_e164` now lives in `app/normalize/phones.py`, because the crawl needs the same rule
# and no crawler is going to import a storage module to get it. Re-exported because both have
# always been reachable from here as far as every caller and test is concerned, and a move is
# not a reason to make them look like new API.
__all__ = ["GraphRepo", "SqlGraphRepo", "Stored", "normalized_name", "to_e164"]
