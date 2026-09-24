"""Remembering what a search found, for as long as we are allowed to (task 2.8).

A repeated search is a repeated $0.035, so the answer is cached. What is cached is the whole
candidate rather than only its place id: an id on its own is not a result, and rehydrating one
costs a details call at $0.020 — dearer than the search we were trying to avoid.

That means Places content lives in Redis, which the terms allow for 30 days (ADR-0011). The key
expires on exactly that clock, so this cache sweeps itself, and nothing here may be given a
longer life than `PLACES_CONTENT_TTL_S`.

Keyed by everything that changes the answer — the term, the filters and the tile — because two
different questions must never share an entry, least of all across orgs. The key also carries a
version of what an entry *contains*, because an entry that is merely out of shape is worse than
no entry: see `SCHEMA_VERSION`.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from redis.asyncio import Redis

from app.connectors.google_places.tiling import Tile
from app.connectors.types import Candidate, FieldValue

KEY_PREFIX = "places:search:"

#: Bumped whenever what a cached candidate holds changes. Without it, an entry written by an
#: older deploy is read back by a newer one and quietly loses whatever the old shape did not
#: know about — which is precisely what happened when candidates began carrying their own field
#: values: every cache hit produced a business with no name, address or phone behind it, no
#: provenance at all, and the job was charged for it as a new lead. A wrong answer that looks
#: right is worse than paying for the search again.
SCHEMA_VERSION = 2

#: The longest Places content may be kept (ADR-0011). Not a tuning knob.
PLACES_CONTENT_TTL_S = 30 * 24 * 3600

#: A tile's corners to five decimal places is about a metre: precise enough that two different
#: tiles never collide, coarse enough that floating-point noise does not miss a cache hit.
_TILE_PRECISION = 5


@dataclass(frozen=True, slots=True)
class CachedSearch:
    """One tile's worth of candidates, and whether Google had more it would not show."""

    candidates: list[Candidate]
    truncated: bool


def cache_key(source_key: str, fingerprint: str, tile: Tile) -> str:
    corners = (
        round(tile.min_lat, _TILE_PRECISION),
        round(tile.min_lng, _TILE_PRECISION),
        round(tile.max_lat, _TILE_PRECISION),
        round(tile.max_lng, _TILE_PRECISION),
    )
    digest = sha256(
        "\x1f".join((source_key, fingerprint, *(str(c) for c in corners))).encode()
    ).hexdigest()
    return f"{KEY_PREFIX}v{SCHEMA_VERSION}:{source_key}:{digest[:32]}"


class PlaceSearchCache:
    """Shared across orgs on purpose: the key covers the whole question, so a hit means the same
    question about the same public businesses, and the answer holds no tenant data."""

    def __init__(self, redis: Redis, *, ttl_s: int = PLACES_CONTENT_TTL_S) -> None:
        self._redis = redis
        # Never longer than the terms allow, whatever a caller asks for.
        self._ttl_s = min(ttl_s, PLACES_CONTENT_TTL_S)

    async def get(self, source_key: str, fingerprint: str, tile: Tile) -> CachedSearch | None:
        key = cache_key(source_key, fingerprint, tile)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            stored = json.loads(raw)
            return CachedSearch(
                candidates=[_candidate(c) for c in stored["candidates"]],
                truncated=bool(stored.get("truncated", False)),
            )
        except (ValueError, KeyError, TypeError):
            # A malformed entry is not worth failing a job over; pay for the search again.
            await self._redis.delete(key)
            return None

    async def put(
        self, source_key: str, fingerprint: str, tile: Tile, search: CachedSearch
    ) -> None:
        if not search.candidates:
            return
        payload = json.dumps(
            {
                "candidates": [_as_dict(c) for c in search.candidates],
                "truncated": search.truncated,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        await self._redis.set(cache_key(source_key, fingerprint, tile), payload, ex=self._ttl_s)


def _as_dict(candidate: Candidate) -> dict[str, Any]:
    return {
        "source_key": candidate.source_key,
        "external_id": candidate.external_id,
        "name": candidate.name,
        "url": candidate.url,
        "phone": candidate.phone,
        "address": candidate.address,
        "city": candidate.city,
        "country": candidate.country,
        "lat": candidate.lat,
        "lng": candidate.lng,
        "source_url": candidate.source_url,
        "observed_at": candidate.observed_at.isoformat() if candidate.observed_at else None,
        "raw": candidate.raw,
        # The values this search already paid for. Dropping them here was not a smaller cache,
        # it was a lead with nothing behind it (CLAUDE.md: no value without provenance).
        "values": [_value_as_dict(v) for v in candidate.values],
    }


def _value_as_dict(value: FieldValue) -> dict[str, Any]:
    return {
        "entity_type": value.entity_type,
        "field": value.field,
        "value": value.value,
        "source_key": value.source_key,
        "source_url": value.source_url,
        "observed_at": value.observed_at.isoformat(),
        "method": value.method,
        "confidence": value.confidence,
        "derivation": value.derivation,
        "model": value.model,
        "prompt_version": value.prompt_version,
    }


def _value(data: dict[str, Any]) -> FieldValue:
    return FieldValue(
        entity_type=data["entity_type"],
        field=data["field"],
        value=data["value"],
        source_key=data["source_key"],
        source_url=data["source_url"],
        observed_at=datetime.fromisoformat(data["observed_at"]),
        method=data["method"],
        confidence=data["confidence"],
        derivation=data.get("derivation"),
        model=data.get("model"),
        prompt_version=data.get("prompt_version"),
    )


def _candidate(data: dict[str, Any]) -> Candidate:
    observed = data.get("observed_at")
    return Candidate(
        source_key=data["source_key"],
        external_id=data["external_id"],
        name=data["name"],
        url=data.get("url"),
        phone=data.get("phone"),
        address=data.get("address"),
        city=data.get("city"),
        country=data.get("country"),
        lat=data.get("lat"),
        lng=data.get("lng"),
        source_url=data.get("source_url", ""),
        observed_at=datetime.fromisoformat(observed) if observed else None,
        raw=data.get("raw", {}),
        values=tuple(_value(v) for v in data.get("values", ())),
    )
