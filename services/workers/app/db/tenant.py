"""Tenant context for worker transactions (mirrors withTenant in @leadforge/db)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


@asynccontextmanager
async def tenant_transaction(
    engine: AsyncEngine, org_id: UUID | str
) -> AsyncIterator[AsyncConnection]:
    """Transaction with app.org_id set transaction-locally (RLS reads it); workers have no user."""
    org = str(UUID(str(org_id)))  # validates the id
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "select set_config('app.org_id', :org, true), set_config('app.user_id', '', true)"
            ),
            {"org": org},
        )
        yield conn
