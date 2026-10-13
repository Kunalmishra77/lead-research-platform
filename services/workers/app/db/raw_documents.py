"""Recording what a fetch returned (task 3.6, docs/04, ADR-0019).

One row per fetch, whether or not there is a body. `url_hash`, `content_hash`, `http_status`,
`bytes`, `access_status` and `fetched_at` together say "this URL answered with exactly these
bytes at this time", and a content hash proves a page has not changed since without keeping a
copy of it. A refusal is worth a row too: "this door was shut on this date" is the answer to
"why has this company no email", and CLAUDE.md's rule is that the crawler stops rather than
evades, so a `restricted` row is an outcome and not a failure.

Dedupe is the point of `raw_document_keys`. docs/06 §4.9 says to skip storing when the content
hash is unchanged, and that table makes the question one indexed lookup on (url_hash,
content_hash). A body we already have is recorded again -- the fetch happened -- but no object is
written and the new row points at the key the first one created, which is also what
`field_values.raw_document_id` points at. A value re-extracted from the same body refers to the
copy it was read from, which is the first.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from uuid6 import uuid7

from app.crawl.rawstore import RawStore, storage_key

AccessStatus = Literal["ok", "restricted", "error"]


@dataclass(frozen=True, slots=True)
class Recorded:
    """What a fetch became: the id a value can point at, and whether a body was kept."""

    #: The id in `raw_document_keys` -- the first document with this body, which is the one a
    #: value was read from. None when there was no body, so nothing to point at.
    raw_document_id: str | None
    stored: bool
    #: True when this body was already on disk, so nothing was written.
    deduplicated: bool = False


def sha256(data: bytes | str) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).digest()


class RawDocumentsRepo(Protocol):
    async def record(
        self,
        *,
        url: str,
        source_id: str,
        access_status: AccessStatus,
        http_status: int | None = None,
        content_type: str | None = None,
        body: bytes | None = None,
    ) -> Recorded: ...


class SqlRawDocuments:
    """Writes the row and, when there is a new body, the object.

    The object goes first. A row whose `storage_key` points at nothing would be a lie the
    retention sweeper could not detect; a file with no row is an orphan the sweeper deletes by
    age, which costs a few kilobytes until it does.
    """

    def __init__(self, engine: AsyncEngine, store: RawStore) -> None:
        self._engine = engine
        self._store = store

    async def record(
        self,
        *,
        url: str,
        source_id: str,
        access_status: AccessStatus,
        http_status: int | None = None,
        content_type: str | None = None,
        body: bytes | None = None,
    ) -> Recorded:
        fetched_at = datetime.now(UTC)
        url_digest = sha256(url)
        content_digest = sha256(body) if body else None

        async with self._engine.begin() as conn:
            existing = None
            if content_digest is not None:
                existing = (
                    await conn.execute(
                        text(
                            "select raw_document_id, storage_key from app.raw_document_keys"
                            " where url_hash = :url_hash and content_hash = :content_hash"
                        ),
                        {"url_hash": url_digest, "content_hash": content_digest},
                    )
                ).first()

            document_id = str(uuid7())
            key: str | None = None
            size: int | None = None

            if content_digest is None:
                # A refusal or an error. Recorded, with nothing to store and nothing for a value
                # to point at.
                pass
            elif existing is not None:
                # docs/06 §4.9: the body is unchanged, so no object is written. The fetch is
                # still recorded, because "we checked on this date and it had not changed" is
                # worth knowing and costs a row.
                key = existing[1]
                size = len(body) if body else None
            else:
                key = storage_key(url_digest.hex(), fetched_at)
                size = await self._store.put(key, body or b"")
                await conn.execute(
                    text(
                        "insert into app.raw_document_keys"
                        " (raw_document_id, url_hash, content_hash, fetched_at, storage_key)"
                        " values (:id, :url_hash, :content_hash, :fetched, :key)"
                        " on conflict (url_hash, content_hash) do nothing"
                    ),
                    {
                        "id": document_id,
                        "url_hash": url_digest,
                        "content_hash": content_digest,
                        "fetched": fetched_at,
                        "key": key,
                    },
                )

            await conn.execute(
                text(
                    "insert into app.raw_documents"
                    " (id, url, url_hash, source_id, fetched_at, http_status, content_type,"
                    "  content_hash, storage_key, bytes, access_status)"
                    " values (:id, :url, :url_hash, :source, :fetched, :status, :content_type,"
                    "  :content_hash, :key, :bytes,"
                    "  cast(:access as app.access_status))"
                ),
                {
                    "id": document_id,
                    "url": url[:2048],
                    "url_hash": url_digest,
                    "source": source_id,
                    "fetched": fetched_at,
                    "status": http_status,
                    "content_type": (content_type or None) and content_type[:255],
                    "content_hash": content_digest,
                    # Only the row that owns the object names it: the constraint allows a
                    # `storage_key` only on an `ok` row, and pointing two rows at one file would
                    # make the retention sweep delete a body another row still claims.
                    "key": key if existing is None else None,
                    "bytes": size,
                    "access": access_status,
                },
            )

        if content_digest is None:
            return Recorded(raw_document_id=None, stored=False)
        if existing is not None:
            return Recorded(
                raw_document_id=str(existing[0]), stored=False, deduplicated=True
            )
        return Recorded(raw_document_id=document_id, stored=True)
