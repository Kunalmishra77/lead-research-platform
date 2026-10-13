"""The computed best value per field (docs/04 "Best-value computation", task 3.13).

`companies.best` has existed since migration 0012 and nothing has ever written to it: 1,550
companies, all of them `{}`. The spec is one sentence -- "per field, the current `field_values`
row with the highest confidence (ties -> newest)" -- and the reason it is worth having is that
`field_values` holds every observation, one row per field per source, while the grid, an export
and the API's company endpoint each want one answer per field and one read per company.

It is a derived cache, never a source. Nothing here decides anything: the winner is picked by
`confidence` and then `observed_at`, both of which were set by whoever observed the value.
Deleting the whole column and recomputing it would lose nothing.

## Why the columns are not all filled from this

Some fields have a real column as well -- `companies.city`, `company_locations.phone_e164` --
and those are written from the candidate, where they are normalised on the way in. Projecting
them from `best` as well would mean two writers for one column and the second undoing the first:
the city *value* says "New Delhi" because that is what the source said, and the city *column*
says "Delhi" because that is the spelling the geography uses and filters read
(`app/normalize/cities.py`). Both are correct and they must not be reconciled.

`companies.state` is the exception, and the reason this module exists today rather than in Phase
4: `_company_params` wrote a literal `None` into it for every company ever stored. The state
arrives as a field value and nowhere else, so 1,546 of 1,550 companies had no state at all and
no state-level filter could work. It is filled from the winning value, through the seeded
geography so that `HR` and `Haryana` are one state rather than two.
"""

import json
from collections.abc import Sequence
from typing import Any, Final, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

#: Fields whose winning value is projected into a column of its own. Deliberately short: a field
#: already written from the candidate is not here, because two writers for one column means the
#: later one silently wins and nobody knows which that is.
PROJECTED: Final[tuple[str, ...]] = ("state",)


class StateLookup(Protocol):
    """What this needs of the seeded geography: a state by the words a source used."""

    async def find_state(self, name: str, *, country: str | None = None) -> Any: ...


async def recompute(
    conn: AsyncConnection,
    company_ids: Sequence[str],
    *,
    geography: StateLookup | None = None,
) -> int:
    """Rewrites `best` for these companies, and projects what has a column of its own.

    One statement for the whole chunk rather than one per company: a discovery task hands this a
    hundred ids, and at the 452 ms this deployment sees to its database the difference is a
    hundred round trips against two.

    Returns how many companies were rewritten, which is what a caller logs.
    """
    ids = sorted({cid for cid in company_ids if cid})
    if not ids:
        return 0

    rows = (
        await conn.execute(
            text(
                # `distinct on` is the whole computation: Postgres keeps the first row of each
                # (entity, field) group in the stated order, which is exactly "highest
                # confidence, newest wins a tie".
                "select distinct on (entity_id, field)"
                "       entity_id, field, value"
                "  from app.field_values"
                " where entity_type = 'company' and is_current"
                "   and entity_id = any(cast(:ids as uuid[]))"
                " order by entity_id, field, confidence desc, observed_at desc"
            ),
            {"ids": ids},
        )
    ).all()

    best: dict[str, dict[str, Any]] = {}
    for entity_id, field, value in rows:
        best.setdefault(str(entity_id), {})[field] = value

    if not best:
        return 0

    await conn.execute(
        text(
            "update app.companies set best = cast(cast(:best as text) as jsonb)"
            " where id = cast(:id as uuid)"
        ),
        [{"id": company_id, "best": json.dumps(values)} for company_id, values in best.items()],
    )

    await _project_state(conn, best, geography=geography)
    return len(best)


async def _project_state(
    conn: AsyncConnection,
    best: dict[str, dict[str, Any]],
    *,
    geography: StateLookup | None,
) -> None:
    """Fills `companies.state`, which was written as a literal `None` for every company.

    Through the geography rather than straight from the value, because the sources do not agree
    on a spelling: the live graph had `HR` 870, `Haryana` 245, `Delhi` 399 and `DL` 19 for what
    are two states. The same reasoning as cities, and the same rule -- a value the seed cannot
    place as a state is left out rather than stored, because `Gurugram` (a city), `India` and
    `IN` (a country) all arrived in this field too and none of them is a state.
    """
    if geography is None:
        return

    wanted = {
        str(values["state"])
        for values in best.values()
        if isinstance(values.get("state"), str) and values["state"].strip()
    }
    resolved: dict[str, str] = {}
    for name in wanted:
        area = await geography.find_state(name)
        if area is not None:
            resolved[name] = area.name

    updates = [
        {"id": company_id, "state": resolved[str(values["state"])]}
        for company_id, values in best.items()
        if isinstance(values.get("state"), str) and str(values["state"]) in resolved
    ]
    if updates:
        await conn.execute(
            text(
                "update app.companies set state = :state"
                " where id = cast(:id as uuid) and coalesce(state, '') <> :state"
            ),
            updates,
        )
