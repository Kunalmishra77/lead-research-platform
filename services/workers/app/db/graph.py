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

None of these is tenant data. The graph is shared and filled only from public sources, so a
customer's own imports never reach it (ADR-0007). The policies are `USING (true)` for
`app_worker`, but everything still runs inside `tenant_transaction` so a task's own row and the
graph rows it produces commit or fail together.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from uuid6 import uuid7

from app.connectors.types import Candidate
from app.db.tenant import tenant_transaction
from app.normalize.domains import is_platform_host, registrable_domain

#: `company_locations.phone_e164` is CHECKed against this shape — no spaces, no punctuation.
_E164_MIN_DIGITS = 7
_E164_MAX_DIGITS = 15


@dataclass(frozen=True, slots=True)
class Stored:
    """What one candidate turned into."""

    company_id: str
    location_id: str | None
    #: False when this place was already in the graph. Only a new one is worth charging for.
    is_new: bool
    values_written: int


class GraphRepo(Protocol):
    async def store(
        self, org_id: str, candidates: list[Candidate], *, source_id: str
    ) -> list[Stored]: ...


class SqlGraphRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def store(
        self, org_id: str, candidates: list[Candidate], *, source_id: str
    ) -> list[Stored]:
        """Writes a batch of candidates, one transaction for the batch.

        One transaction so a task that dies halfway leaves nothing behind to reconcile: either
        the search's results are in the graph or they are not.
        """
        stored: list[Stored] = []
        async with tenant_transaction(self._engine, org_id) as conn:
            for candidate in candidates:
                stored.append(await self._store_one(conn, candidate, source_id=source_id))
        return stored

    async def _store_one(
        self, conn: AsyncConnection, candidate: Candidate, *, source_id: str
    ) -> Stored:
        place_id = candidate.external_id
        existing = await self._location_company(conn, place_id)
        host = registrable_domain(candidate.url)
        # A platform host identifies the platform, not the business. `primary_domain` is UNIQUE,
        # so storing one would merge every business that builds its site there into whichever
        # arrived first -- two unrelated clinics becoming one company, unrecoverably.
        on_platform = is_platform_host(candidate.url)
        domain = None if on_platform else host

        if existing is not None:
            company_id, is_new = existing, False
        else:
            company_id = await self._resolve_company(conn, candidate, domain=domain)
            is_new = True

        location_id = await self._upsert_location(
            conn, candidate, company_id=company_id, place_id=place_id
        )
        if host:
            await self._record_domain(conn, company_id, host, is_platform=on_platform)
        written = await self._write_values(
            conn, candidate, company_id=company_id, source_id=source_id
        )
        return Stored(
            company_id=company_id,
            location_id=location_id,
            is_new=is_new,
            values_written=written,
        )

    async def _location_company(self, conn: AsyncConnection, place_id: str) -> str | None:
        """The company this place already belongs to, if we have seen it before."""
        if not place_id:
            return None
        row = (
            await conn.execute(
                text("select company_id from app.company_locations where google_place_id = :p"),
                {"p": place_id},
            )
        ).first()
        return str(row[0]) if row else None

    async def _resolve_company(
        self, conn: AsyncConnection, candidate: Candidate, *, domain: str | None
    ) -> str:
        """The company for a place we have not seen, creating one if nothing matches.

        A domain is the only thing that can tie two places to one business here, so two branches
        of a chain with one website become one company with two locations — which is right — and
        two unrelated shops with no website stay separate, which is also right. Anything cleverer
        is resolution's job (docs/06 §6), not discovery's.
        """
        if domain:
            row = (
                await conn.execute(
                    text(
                        "insert into app.companies"
                        " (id, canonical_name, normalized_name, primary_domain, country,"
                        "  city, state)"
                        " values (:id, :name, :norm, :domain, :country, :city, :state)"
                        " on conflict (primary_domain) do update"
                        "   set canonical_name = app.companies.canonical_name"
                        " returning id"
                    ),
                    self._company_params(candidate, domain),
                )
            ).one()
            return str(row[0])

        row = (
            await conn.execute(
                text(
                    "insert into app.companies"
                    " (id, canonical_name, normalized_name, country, city, state)"
                    " values (:id, :name, :norm, :country, :city, :state)"
                    " returning id"
                ),
                self._company_params(candidate, None),
            )
        ).one()
        return str(row[0])

    def _company_params(self, candidate: Candidate, domain: str | None) -> dict[str, Any]:
        return {
            "id": str(uuid7()),
            "name": candidate.name,
            "norm": normalized_name(candidate.name),
            "domain": domain,
            "country": _country(candidate.country),
            "city": candidate.city,
            "state": None,
        }

    async def _upsert_location(
        self, conn: AsyncConnection, candidate: Candidate, *, company_id: str, place_id: str
    ) -> str | None:
        if not place_id:
            return None
        row = (
            await conn.execute(
                text(
                    "insert into app.company_locations"
                    " (id, company_id, address, city, country, lat, lng, google_place_id,"
                    "  phone_e164)"
                    " values (:id, :company, :address, :city, :country, :lat, :lng, :place,"
                    "  :phone)"
                    " on conflict (google_place_id) do update"
                    "   set address = coalesce(excluded.address, app.company_locations.address),"
                    "       city = coalesce(excluded.city, app.company_locations.city),"
                    "       lat = coalesce(excluded.lat, app.company_locations.lat),"
                    "       lng = coalesce(excluded.lng, app.company_locations.lng),"
                    "       phone_e164 = coalesce(excluded.phone_e164,"
                    "                             app.company_locations.phone_e164)"
                    " returning id"
                ),
                {
                    "id": str(uuid7()),
                    "company": company_id,
                    "address": candidate.address,
                    "city": candidate.city,
                    "country": _country(candidate.country),
                    "lat": candidate.lat,
                    "lng": candidate.lng,
                    "place": place_id,
                    "phone": to_e164(candidate.phone),
                },
            )
        ).one()
        return str(row[0])

    async def _record_domain(
        self, conn: AsyncConnection, company_id: str, domain: str, *, is_platform: bool
    ) -> None:
        """Every domain we saw, flagged with whether it can identify this business.

        The unique index on `domain` is partial on `not is_platform`, so a hundred businesses may
        each have their own `sites.google.com` row while a real domain still belongs to one.
        """
        await conn.execute(
            text(
                "insert into app.company_domains"
                " (id, company_id, domain, is_primary, is_platform)"
                " values (:id, :company, :domain, :primary, :platform)"
                " on conflict (company_id, domain) do nothing"
            ),
            {
                "id": str(uuid7()),
                "company": company_id,
                "domain": domain,
                "primary": not is_platform,
                "platform": is_platform,
            },
        )

    async def _write_values(
        self, conn: AsyncConnection, candidate: Candidate, *, company_id: str, source_id: str
    ) -> int:
        """Appends this observation and retires the one it replaces.

        Never an update: the old row keeps its own `observed_at` and evidence, which is what lets
        a value that was true last month still say so. `is_current` is the only column the worker
        may change, and that is exactly the column this needs.
        """
        written = 0
        for value in candidate.values:
            value_id = str(uuid7())
            await conn.execute(
                text(
                    "insert into app.field_values"
                    " (id, entity_type, entity_id, field, value, source_id, source_url,"
                    "  method, derivation, confidence, observed_at)"
                    " values (:id, 'company', :entity, :field, cast(cast(:value as text) as jsonb),"
                    "  :source, :url, cast(:method as app.value_method), :derivation,"
                    "  :confidence, :observed)"
                ),
                {
                    "id": value_id,
                    "entity": company_id,
                    "field": value.field,
                    "value": json.dumps(value.value),
                    "source": source_id,
                    "url": value.source_url,
                    "method": value.method,
                    "derivation": value.derivation,
                    "confidence": value.confidence,
                    "observed": value.observed_at,
                },
            )
            await conn.execute(
                text(
                    "update app.field_values set is_current = false"
                    " where entity_type = 'company' and entity_id = :entity and field = :field"
                    "   and id <> :id and is_current"
                ),
                {"entity": company_id, "field": value.field, "id": value_id},
            )
            written += 1
        return written


def to_e164(phone: str | None) -> str | None:
    """A phone number in the one shape the column accepts, or None.

    Only a number that already states its country code can be normalised by stripping
    separators. A national number ("020 1234 5678") needs country-specific rules to know which
    digits are a trunk prefix, and inventing a country code would put a real call through to the
    wrong country. Those numbers are still stored as a `phone` field value with their provenance
    — they are simply not claimed to be E.164 here. Proper normalisation arrives with
    `app/normalize/phone.py` in Phase 3.
    """
    if not phone:
        return None
    text_ = phone.strip()
    if not text_.startswith("+"):
        return None
    digits = "".join(c for c in text_[1:] if c.isdigit())
    if not _E164_MIN_DIGITS <= len(digits) <= _E164_MAX_DIGITS or digits.startswith("0"):
        return None
    return f"+{digits}"


def normalized_name(name: str) -> str:
    """`companies.normalized_name` is NOT NULL and is what later resolution blocks on."""
    folded = "".join(c if c.isalnum() else " " for c in name.casefold())
    return " ".join(folded.split())


def _country(code: str | None) -> str | None:
    """ISO-3166 alpha-2 upper case, or nothing — the column CHECKs `^[A-Z]{2}$`."""
    if not code or len(code) != 2 or not code.isalpha():
        return None
    return code.upper()
