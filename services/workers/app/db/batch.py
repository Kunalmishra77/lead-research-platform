"""Writing many candidates in a handful of statements instead of a handful each.

Measured, and the reason this exists: storing one candidate cost about twenty-six round trips --
a location lookup, a company, a location, a domain, two per field value, a lead and a link. The
round trip from the VPS to its database is 452 ms. That is 11.7 seconds for one business, and
forty-nine minutes for the two hundred and fifty a single search now returns.

It was always like this. Google Places handed back twenty results at a time, so a task took two
or three minutes and nobody looked; the place index returns the lot at once and the same code
took the afternoon. The fix is not a faster database, it is not asking it twenty-six questions
per business.

Every statement here writes a whole batch, so the count stops depending on how many candidates
there are -- about thirteen round trips for a chunk of a hundred.

Two ways to send a batch, and the difference is not a preference:

* A list of parameter dictionaries goes through asyncpg's `executemany`, which is **one** round
  trip for any number of rows -- 78 ms for two hundred, the same as a single `select 1`. It
  cannot return rows: asking it to raises `ResourceClosedError: This result object does not
  return rows`.
* A multi-row `values (...), (...)` statement can return rows, and costs one round trip too.

So anything whose result is read back is written the second way, and everything else the first.

Three rules the batching has to respect, each of which Postgres enforces by refusing:

* `ON CONFLICT DO UPDATE` cannot touch the same row twice in one statement, so anything keyed on
  a conflict target is deduplicated here first.
* Ids are generated here rather than read back where that is possible, so a row's id is known
  before the insert and the results can be reassembled without another query.
* Retiring superseded values must not retire the ones just written, which in a batch means
  excluding a list of ids rather than a single one.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from uuid6 import uuid7

from app.connectors.types import Candidate, FieldValue
from app.normalize.domains import is_platform_host, registrable_domain
from app.normalize.names import matching_key
from app.normalize.phones import to_e164


@dataclass(slots=True)
class Prepared:
    """One candidate's rows, worked out before anything is sent."""

    candidate: Candidate
    place_id: str
    host: str | None
    on_platform: bool
    domain: str | None
    #: Filled in as the batch progresses.
    company_id: str = ""
    location_id: str | None = None
    lead_id: str | None = None
    is_new: bool = False
    values_written: int = 0


@dataclass(slots=True)
class ValueRow:
    """A field value with the id it will be written under."""

    id: str
    entity_id: str
    value: FieldValue


def _values_clause(
    rows: Sequence[dict[str, Any]], columns: Sequence[str]
) -> tuple[str, dict[str, Any]]:
    """A multi-row `values (...), (...)` clause and the parameters it binds.

    Used wherever an id has to come back, because `executemany` cannot return rows. Parameters
    are numbered per row and never interpolated: the only thing built from a string here is the
    shape of the clause.
    """
    chunks: list[str] = []
    params: dict[str, Any] = {}
    for i, row in enumerate(rows):
        names = []
        for column in columns:
            key = f"{column}_{i}"
            params[key] = row[column]
            names.append(f":{key}")
        chunks.append(f"({', '.join(names)})")
    return ", ".join(chunks), params


def prepare(candidates: Sequence[Candidate]) -> list[Prepared]:
    """Everything derivable from a candidate without touching the database."""
    prepared: list[Prepared] = []
    for candidate in candidates:
        host = registrable_domain(candidate.url)
        # A platform host identifies the platform, not the business. `primary_domain` is UNIQUE,
        # so storing one would merge every business that builds its site there into whichever
        # arrived first -- two unrelated clinics becoming one company, unrecoverably.
        on_platform = is_platform_host(candidate.url)
        prepared.append(
            Prepared(
                candidate=candidate,
                place_id=candidate.external_id,
                host=host,
                on_platform=on_platform,
                domain=None if on_platform else host,
            )
        )
    return prepared


async def claim_places(conn: AsyncConnection, place_ids: Sequence[str]) -> None:
    """Takes a transaction-scoped lock on each place this chunk is about to write.

    Without it, two tasks searching overlapping areas find the same business at the same moment
    and both get as far as creating a company for it before either writes a location. One then
    wins the conflict on `google_place_id` and the other's company is left with no location and
    no way to ever get one -- a duplicate of a real business, with a lead pointing at it. A
    single live run produced 217 of them.

    A lock rather than a cleanup, because the cleanup would mean granting the worker DELETE on
    the shared company graph, and a role that can delete companies will one day delete the wrong
    ones. Nothing is created that has to be undone instead: whoever asks second waits, and by
    the time it looks the place up the company is there to be reused.

    Only tasks that overlap on an actual place wait for each other; disjoint searches never
    touch the same lock. The ids are sorted so every transaction takes them in the same order,
    which is what makes a deadlock between two overlapping chunks impossible. `hashtextextended`
    narrows a place id to the bigint a lock is keyed on; a collision costs two unrelated places
    a needless wait and nothing else.
    """
    ids = sorted({p for p in place_ids if p})
    if not ids:
        return
    await conn.execute(
        text(
            "select pg_advisory_xact_lock(hashtextextended(place, 0))"
            " from unnest(cast(:ids as text[])) as place"
        ),
        {"ids": ids},
    )


async def known_companies(conn: AsyncConnection, place_ids: Sequence[str]) -> dict[str, str]:
    """Which of these places we have already seen, and the company each belongs to."""
    ids = [p for p in dict.fromkeys(place_ids) if p]
    if not ids:
        return {}
    rows = (
        await conn.execute(
            text(
                "select google_place_id, company_id from app.company_locations"
                " where google_place_id in :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )
    ).all()
    return {str(r[0]): str(r[1]) for r in rows}


async def insert_companies(conn: AsyncConnection, pending: Sequence[Prepared]) -> None:
    """Creates a company for each candidate that needs one, setting `company_id` on each.

    The two cases behave differently under conflict. A company with a domain upserts on it, so
    two branches of a chain with one website become one company with two locations -- which is
    right -- and the id has to be read back, because the row that wins may be one that was
    already there. A company without a domain cannot collide with anything, so its generated id
    is final and the insert needs no result at all.
    """
    # One company per place, before anything else. Two candidates for the same place are the
    # same business, and the per-candidate version got that for free: the second one looked the
    # place up, found the location the first had just written, and reused its company. A batch
    # has no such ordering -- every lookup happens before every write -- so it has to be said.
    #
    # Leaving it unsaid cost 76 companies with no location at all, 7.7% of the graph, each one
    # a duplicate of a real business with a lead pointing at it.
    first_for_place: dict[str, Prepared] = {}
    followers: list[tuple[Prepared, Prepared]] = []
    fresh: list[Prepared] = []
    for p in pending:
        leader = first_for_place.get(p.place_id) if p.place_id else None
        if leader is None:
            if p.place_id:
                first_for_place[p.place_id] = p
            fresh.append(p)
        else:
            followers.append((p, leader))

    with_domain = [p for p in fresh if p.domain]
    without = [p for p in fresh if not p.domain]

    if without:
        rows = [_company_params(p, None) for p in without]
        await conn.execute(
            text(
                "insert into app.companies"
                " (id, canonical_name, normalized_name, country, city, state)"
                " values (:id, :name, :norm, :country, :city, :state)"
            ),
            rows,
        )
        for p, row in zip(without, rows, strict=True):
            p.company_id = str(row["id"])

    if not with_domain:
        _copy_to_followers(followers)
        return

    # One row per domain: `ON CONFLICT` refuses to touch a row twice in one statement, and two
    # candidates sharing a domain are exactly that.
    first_for_domain: dict[str, Prepared] = {}
    for p in with_domain:
        first_for_domain.setdefault(str(p.domain), p)

    rows = [_company_params(p, str(p.domain)) for p in first_for_domain.values()]
    clause, params = _values_clause(
        rows, ("id", "name", "norm", "domain", "country", "city", "state")
    )
    returned = (
        await conn.execute(
            text(
                "insert into app.companies"  # noqa: S608 -- numbered placeholders, see below
                " (id, canonical_name, normalized_name, primary_domain, country, city, state)"
                f" values {clause}"
                " on conflict (primary_domain) do update"
                "   set canonical_name = app.companies.canonical_name"
                " returning id, primary_domain"
            ),
            params,
        )
    ).all()
    # The id is read from the result rather than assumed: a domain already in the graph keeps
    # the company it already had, and the no-op `do update` exists so that row comes back.
    by_domain = {str(r[1]): str(r[0]) for r in returned}
    for p in with_domain:
        p.company_id = by_domain[str(p.domain)]

    _copy_to_followers(followers)


def _copy_to_followers(followers: Sequence[tuple[Prepared, Prepared]]) -> None:
    """Gives every candidate sharing a place the company its first occurrence was given."""
    for follower, leader in followers:
        follower.company_id = leader.company_id


async def upsert_locations(conn: AsyncConnection, pending: Sequence[Prepared]) -> None:
    """One row per place, setting `location_id` on each candidate that has one."""
    first_for_place: dict[str, Prepared] = {}
    for p in pending:
        if p.place_id:
            first_for_place.setdefault(p.place_id, p)
    if not first_for_place:
        return

    rows = [
        {
            "id": str(uuid7()),
            "company": p.company_id,
            "address": p.candidate.address,
            "city": p.candidate.city,
            "country": _country(p.candidate.country),
            "lat": p.candidate.lat,
            "lng": p.candidate.lng,
            "place": p.place_id,
            # The country, not just the digits: `085277 87137` is a real Indian mobile and
            # nothing at all without knowing it is India. 481 numbers were being dropped here
            # for want of a `+` (see app/normalize/phones.py for the measurement).
            "phone": to_e164(p.candidate.phone, _country(p.candidate.country)),
        }
        for p in first_for_place.values()
    ]
    clause, params = _values_clause(
        rows, ("id", "company", "address", "city", "country", "lat", "lng", "place", "phone")
    )
    returned = (
        await conn.execute(
            text(
                "insert into app.company_locations"  # noqa: S608 -- numbered placeholders
                " (id, company_id, address, city, country, lat, lng, google_place_id,"
                "  phone_e164)"
                f" values {clause}"
                " on conflict (google_place_id) do update"
                "   set address = coalesce(excluded.address, app.company_locations.address),"
                "       city = coalesce(excluded.city, app.company_locations.city),"
                "       lat = coalesce(excluded.lat, app.company_locations.lat),"
                "       lng = coalesce(excluded.lng, app.company_locations.lng),"
                "       phone_e164 = coalesce(excluded.phone_e164,"
                "                             app.company_locations.phone_e164)"
                " returning id, google_place_id, company_id"
            ),
            params,
        )
    ).all()
    # `company_id` is read back, not assumed. Two tasks searching overlapping areas find the
    # same business at the same moment: both look it up and find nothing, both create a
    # company, and then one of them wins the conflict on `google_place_id`. The loser's company
    # has no location and never will -- 217 of them appeared in a single live run before this
    # line existed. The place says who owns it; whoever asked second adopts that answer.
    by_place = {str(r[1]): (str(r[0]), str(r[2])) for r in returned}
    for p in pending:
        won = by_place.get(p.place_id)
        if won is None:
            continue
        p.location_id, p.company_id = won[0], won[1]


async def record_domains(conn: AsyncConnection, pending: Sequence[Prepared]) -> None:
    """Every domain seen, flagged with whether it can identify the business.

    The unique index on `domain` is partial on `not is_platform`, so a hundred businesses may
    each have their own `sites.google.com` row while a real domain still belongs to one.
    """
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for p in pending:
        if not p.host or (p.company_id, p.host) in seen:
            continue
        seen.add((p.company_id, p.host))
        rows.append(
            {
                "id": str(uuid7()),
                "company": p.company_id,
                "domain": p.host,
                "primary": not p.on_platform,
                "platform": p.on_platform,
            }
        )
    if not rows:
        return
    await conn.execute(
        text(
            "insert into app.company_domains"
            " (id, company_id, domain, is_primary, is_platform)"
            " values (:id, :company, :domain, :primary, :platform)"
            " on conflict (company_id, domain) do nothing"
        ),
        rows,
    )


async def write_values(conn: AsyncConnection, rows: Sequence[ValueRow], *, source_id: str) -> int:
    """Appends observations and retires the ones they replace, in two statements for the lot.

    Never an update: the old row keeps its own `observed_at` and evidence, which is what lets a
    value that was true last month still say so. `is_current` is the only column the worker may
    change, and that is exactly the column this needs.
    """
    if not rows:
        return 0

    await conn.execute(
        text(
            "insert into app.field_values"
            " (id, entity_type, entity_id, field, value, source_id, source_url,"
            "  method, derivation, confidence, observed_at)"
            " values (:id, 'company', :entity, :field, cast(cast(:value as text) as jsonb),"
            "  :source, :url, cast(:method as app.value_method), :derivation,"
            "  :confidence, :observed)"
        ),
        [
            {
                "id": r.id,
                "entity": r.entity_id,
                "field": r.value.field,
                "value": json.dumps(r.value.value),
                "source": source_id,
                "url": r.value.source_url,
                "method": r.value.method,
                "derivation": r.value.derivation,
                "confidence": r.value.confidence,
                "observed": r.value.observed_at,
            }
            for r in rows
        ],
    )

    # Retire what these replace -- including the ones this very batch superseded.
    #
    # A batch can hold several values for one (entity, field): two candidates sharing a domain
    # resolve to one company, and a chain's branches do exactly that. Excluding every id just
    # written left all of them current, which is the one thing `is_current` exists to prevent;
    # a real run of 250 gyms produced five such pairs. So only the last value for each pair is
    # spared, and the earlier ones are retired alongside whatever was there before -- which is
    # what the per-candidate version did implicitly, by running once per candidate.
    winners: dict[tuple[str, str], str] = {}
    for r in rows:
        winners[(r.entity_id, r.value.field)] = r.id
    clause, params = _values_clause(
        [{"entity": e, "field": f} for e, f in sorted(winners)], ("entity", "field")
    )
    params["written"] = list(winners.values())
    await conn.execute(
        text(
            "update app.field_values fv set is_current = false"  # noqa: S608 -- placeholders
            f" from (values {clause}) as t(entity_id, field)"
            " where fv.entity_type = 'company' and fv.is_current"
            "   and fv.entity_id = cast(t.entity_id as uuid) and fv.field = t.field"
            "   and fv.id not in :written"
        ).bindparams(bindparam("written", expanding=True)),
        params,
    )
    return len(rows)


async def deliver(
    conn: AsyncConnection,
    pending: Sequence[Prepared],
    *,
    org_id: str,
    workspace_id: str,
    research_job_id: str,
) -> None:
    """Hands these companies to the workspace and records which were new to it.

    `DO NOTHING` rather than an update: a workspace that already holds a lead may have changed
    its status, assigned it or corrected a value, and a search running again must not undo any
    of that. It is also why `app_worker` has no UPDATE grant on the table.

    `is_new` has to mean "this customer did not have this before", not "nobody had it": it is
    what the job is charged on. It comes from what the insert actually inserted, not from a
    count taken beforehand, so two workers racing on the same company cannot both bill for it.
    """
    first_for_company: dict[str, Prepared] = {}
    for p in pending:
        if p.company_id:
            first_for_company.setdefault(p.company_id, p)
    if not first_for_company:
        return

    rows = [
        {
            "id": str(uuid7()),
            "org": org_id,
            "workspace": workspace_id,
            "company": company_id,
            "job": research_job_id,
        }
        for company_id in first_for_company
    ]
    clause, params = _values_clause(rows, ("id", "org", "workspace", "company", "job"))
    inserted = (
        await conn.execute(
            text(
                "insert into app.leads"  # noqa: S608 -- numbered placeholders
                " (id, org_id, workspace_id, company_id, research_job_id)"
                f" values {clause}"
                " on conflict do nothing"
                " returning id, company_id"
            ),
            params,
        )
    ).all()
    new_by_company = {str(r[1]): str(r[0]) for r in inserted}

    # Whatever did not insert, this workspace already held. One query for all of them.
    already = [c for c in first_for_company if c not in new_by_company]
    held_by_company: dict[str, str] = {}
    if already:
        held = (
            await conn.execute(
                text(
                    "select id, company_id from app.leads"
                    " where workspace_id = :workspace and person_id is null"
                    "   and company_id in :companies"
                ).bindparams(bindparam("companies", expanding=True)),
                {"workspace": workspace_id, "companies": already},
            )
        ).all()
        held_by_company = {str(r[1]): str(r[0]) for r in held}

    for p in pending:
        if p.company_id in new_by_company:
            p.lead_id, p.is_new = new_by_company[p.company_id], True
        else:
            p.lead_id, p.is_new = held_by_company.get(p.company_id), False


async def link(
    conn: AsyncConnection,
    pending: Sequence[Prepared],
    *,
    org_id: str,
    research_job_id: str,
) -> None:
    """Records that this job surfaced these leads, new to the workspace or not (ADR-0015).

    Without these rows a job that re-finds leads it delivered last month reports that it found
    nothing: `deliver` correctly inserts no lead, so `leads.research_job_id` still names the
    earlier job and the grid -- which filters on it -- comes back empty.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in pending:
        if p.lead_id is None or p.lead_id in seen:
            continue
        seen.add(p.lead_id)
        rows.append({"org": org_id, "job": research_job_id, "lead": p.lead_id, "is_new": p.is_new})
    if not rows:
        return
    await conn.execute(
        text(
            "insert into app.research_job_leads (org_id, research_job_id, lead_id, is_new)"
            " values (:org, :job, :lead, :is_new)"
            " on conflict do nothing"
        ),
        rows,
    )


def value_rows(pending: Iterable[Prepared]) -> list[ValueRow]:
    """Every value the batch will write, each with the id it will be written under."""
    rows: list[ValueRow] = []
    for p in pending:
        for value in p.candidate.values:
            rows.append(ValueRow(id=str(uuid7()), entity_id=p.company_id, value=value))
        p.values_written = len(p.candidate.values)
    return rows


def _company_params(p: Prepared, domain: str | None) -> dict[str, Any]:
    return {
        "id": str(uuid7()),
        "name": p.candidate.name,
        "norm": normalized_name(p.candidate.name),
        "domain": domain,
        "country": _country(p.candidate.country),
        "city": p.candidate.city,
        "state": None,
    }


def normalized_name(name: str) -> str:
    """`companies.normalized_name` is NOT NULL and is what later resolution blocks on.

    The rule itself is `app/normalize/names.py` (ADR-0018). It stays reachable from here because
    every caller and test knows it from here, and because the column it fills is written two
    statements below this line.
    """
    return matching_key(name)


def _country(code: str | None) -> str | None:
    """ISO-3166 alpha-2 upper case, or nothing -- the column CHECKs `^[A-Z]{2}$`."""
    if not code or len(code) != 2 or not code.isalpha():
        return None
    return code.upper()
