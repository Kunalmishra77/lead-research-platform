"""Recording a fetch, against a real Postgres (task 3.6, ADR-0019).

Live because the properties worth testing are the ones the database enforces: the partition the
row lands in, the cross-partition unique key that makes dedupe one lookup, and the CHECKs that
stop a row claiming a body it does not have. A fake would agree with whatever the code does.

Skipped unless `DATABASE_URL_MIGRATIONS` is set, like the rest of the live suite. The worker role
may only INSERT here, so the reads use the owner.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.crawl.rawstore import LocalRawStore
from app.db.raw_documents import SqlRawDocuments, sha256

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_MIGRATIONS"),
    reason="needs a database; set DATABASE_URL_MIGRATIONS to run",
)

PAGE = b"<html><body>Dr Sharma's clinic, 98765 43210</body></html>"
OTHER = b"<html><body>Dr Sharma's clinic, new number 98765 00000</body></html>"


@pytest_asyncio.fixture
async def recorder(tmp_path: Path) -> AsyncIterator[tuple[SqlRawDocuments, AsyncEngine, Path]]:
    """A recorder on a real database and a real directory, cleaned up afterwards.

    The rows it writes are deleted by url_hash at the end: `raw_documents` is partitioned and
    shared, and a test that leaves rows behind makes the next measurement of the live graph wrong.
    """
    url = os.environ["DATABASE_URL_MIGRATIONS"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url, connect_args={"statement_cache_size": 0})
    store = LocalRawStore(tmp_path)
    try:
        yield SqlRawDocuments(engine, store), engine, tmp_path
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("delete from app.raw_documents where url like 'https://test.invalid/%'")
            )
            await conn.execute(
                text(
                    "delete from app.raw_document_keys k where not exists"
                    " (select 1 from app.raw_documents d"
                    "   where d.id = k.raw_document_id)"
                )
            )
        await engine.dispose()


async def _source_id(engine: AsyncEngine) -> str:
    async with engine.connect() as conn:
        row = (
            await conn.execute(text("select id from app.sources where key = 'website'"))
        ).first()
    assert row is not None, "run `pnpm db:seed`"
    return str(row[0])


async def _row(engine: AsyncEngine, document_id: str) -> dict[str, object]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("select * from app.raw_documents where id = cast(:id as uuid)"),
                {"id": document_id},
            )
        ).mappings()
        return dict(next(iter(row)))


async def test_a_page_is_recorded_and_its_body_kept(
    recorder: tuple[SqlRawDocuments, AsyncEngine, Path],
) -> None:
    repo, engine, root = recorder
    url = "https://test.invalid/clinic"

    result = await repo.record(
        url=url,
        source_id=await _source_id(engine),
        access_status="ok",
        http_status=200,
        content_type="text/html; charset=utf-8",
        body=PAGE,
    )

    assert result.stored is True
    assert result.deduplicated is False
    assert result.raw_document_id is not None

    row = await _row(engine, result.raw_document_id)
    assert row["url"] == url
    assert row["access_status"] == "ok"
    assert row["http_status"] == 200
    assert bytes(row["url_hash"]) == sha256(url)  # type: ignore[arg-type]
    assert bytes(row["content_hash"]) == sha256(PAGE)  # type: ignore[arg-type]
    assert row["storage_key"] is not None
    assert (root / str(row["storage_key"])).is_file()


async def test_the_same_body_again_is_recorded_but_not_stored_twice(
    recorder: tuple[SqlRawDocuments, AsyncEngine, Path],
) -> None:
    """docs/06 §4.9: skip storing when the content hash is unchanged.

    The fetch is still a row -- "we checked on this date and it had not changed" is worth
    knowing and costs 200 bytes -- and the second row points at the first body, which is the
    copy any value was actually read from.
    """
    repo, engine, root = recorder
    source_id = await _source_id(engine)
    url = "https://test.invalid/unchanged"

    first = await repo.record(
        url=url, source_id=source_id, access_status="ok", http_status=200, body=PAGE
    )
    second = await repo.record(
        url=url, source_id=source_id, access_status="ok", http_status=200, body=PAGE
    )

    assert first.stored is True
    assert second.stored is False
    assert second.deduplicated is True
    assert second.raw_document_id == first.raw_document_id, "it points at the copy we have"

    objects = [p for p in root.rglob("*.html.gz")]
    assert len(objects) == 1, "one body on disk, however many times the page was fetched"

    async with engine.connect() as conn:
        fetches = (
            await conn.execute(
                text("select count(*) from app.raw_documents where url = :url"), {"url": url}
            )
        ).scalar()
    assert fetches == 2, "both fetches are recorded"


async def test_a_changed_page_is_a_second_body(
    recorder: tuple[SqlRawDocuments, AsyncEngine, Path],
) -> None:
    repo, engine, root = recorder
    source_id = await _source_id(engine)
    url = "https://test.invalid/changed"

    first = await repo.record(url=url, source_id=source_id, access_status="ok", body=PAGE)
    second = await repo.record(url=url, source_id=source_id, access_status="ok", body=OTHER)

    assert second.stored is True
    assert second.raw_document_id != first.raw_document_id
    assert len(list(root.rglob("*.html.gz"))) == 2


async def test_the_same_body_at_a_different_url_is_stored_again(
    recorder: tuple[SqlRawDocuments, AsyncEngine, Path],
) -> None:
    """The key is (url_hash, content_hash), not the content alone. Two sites sharing a template
    are two pages, and a value read from one must not cite the other's URL."""
    repo, engine, root = recorder
    source_id = await _source_id(engine)

    one = await repo.record(url="https://test.invalid/a", source_id=source_id,
                            access_status="ok", body=PAGE)
    two = await repo.record(url="https://test.invalid/b", source_id=source_id,
                            access_status="ok", body=PAGE)

    assert one.raw_document_id != two.raw_document_id
    assert len(list(root.rglob("*.html.gz"))) == 2


async def test_a_door_that_was_shut_is_recorded_with_nothing_stored(
    recorder: tuple[SqlRawDocuments, AsyncEngine, Path],
) -> None:
    """CLAUDE.md: the crawler stops and never evades, so a refusal is an outcome.

    "This door was shut on this date" is the answer to "why has this company no email", and the
    row is the only place that answer lives.
    """
    repo, engine, root = recorder
    url = "https://test.invalid/members-only"

    result = await repo.record(
        url=url, source_id=await _source_id(engine), access_status="restricted", http_status=403
    )

    assert result.stored is False
    assert result.raw_document_id is None, "nothing for a value to point at"
    assert list(root.rglob("*.html.gz")) == []

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "select access_status, http_status, content_hash, storage_key"
                    "  from app.raw_documents where url = :url"
                ),
                {"url": url},
            )
        ).first()
    assert row is not None
    assert row[0] == "restricted"
    assert row[1] == 403
    assert row[2] is None
    assert row[3] is None


async def test_a_failed_fetch_is_recorded_too(
    recorder: tuple[SqlRawDocuments, AsyncEngine, Path],
) -> None:
    repo, engine, _root = recorder
    result = await repo.record(
        url="https://test.invalid/dead", source_id=await _source_id(engine), access_status="error"
    )
    assert result.raw_document_id is None

    async with engine.connect() as conn:
        status = (
            await conn.execute(
                text("select access_status from app.raw_documents where url = :url"),
                {"url": "https://test.invalid/dead"},
            )
        ).scalar()
    assert status == "error"


async def test_a_value_can_point_at_the_document_it_was_read_from(
    recorder: tuple[SqlRawDocuments, AsyncEngine, Path],
) -> None:
    """The whole reason the key table exists.

    `field_values.raw_document_id` has been a column pointing at nothing since migration 0013.
    `raw_documents` is partitioned by `fetched_at`, so nothing can reference it directly -- every
    foreign key must name the partition key, and a value does not know when its page was
    fetched. ADR-0003's answer is the small non-partitioned key table, and this proves the
    foreign key it carries actually accepts what the recorder returns.
    """
    repo, engine, _root = recorder
    result = await repo.record(
        url="https://test.invalid/linkable",
        source_id=await _source_id(engine),
        access_status="ok",
        body=PAGE,
    )
    assert result.raw_document_id is not None

    async with engine.connect() as conn:
        accepted = (
            await conn.execute(
                text(
                    "select count(*) from app.raw_document_keys"
                    " where raw_document_id = cast(:id as uuid)"
                ),
                {"id": result.raw_document_id},
            )
        ).scalar()
    assert accepted == 1
