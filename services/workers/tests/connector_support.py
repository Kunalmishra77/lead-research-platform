"""Shared helpers for the connector tests: a client wired to respx and a usage recorder."""

import httpx
import structlog

from app.connectors.http_client import CallCost, ConnectorHttpClient
from app.connectors.types import RateLimit
from app.metering.context import CallContext

URL = "https://api.example.test/search"
FREE = CallCost(meter="api_example_source")
COST = CallCost(meter="api_example_source", cost_micros=4000)
ORG = "11111111-1111-7111-8111-111111111111"
JOB = "22222222-2222-7222-8222-222222222222"
USER_AGENT = "LeadForgeBot/1.0 (+https://leadforge.example/bot)"


def make_ctx(*, research_job_id: str | None = JOB) -> CallContext:
    return CallContext(
        org_id=ORG,
        research_job_id=research_job_id,
        trace_id="0af7651916cd43dd8448eb211c80319c",
        log=structlog.get_logger("test"),
    )


class RecordingUsage:
    """Stands in for app.usage_events: the same unit_key is only ever charged once."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        #: Whether each call was booked (False = the unit key had already been charged).
        self.recorded: list[bool] = []
        self._seen: set[str] = set()

    async def record(
        self,
        *,
        org_id: str,
        research_job_id: str | None,
        meter: str,
        unit_key: str,
        cost_micros: int,
        units: int = 1,
        credits: int = 0,
    ) -> bool:
        self.calls.append(
            {
                "org_id": org_id,
                "research_job_id": research_job_id,
                "meter": meter,
                "unit_key": unit_key,
                "cost_micros": cost_micros,
            }
        )
        if unit_key in self._seen:
            self.recorded.append(False)
            return False
        self._seen.add(unit_key)
        self.recorded.append(True)
        return True


def make_client(
    *,
    usage: RecordingUsage | None = None,
    rate_limit: RateLimit | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    max_attempts: int = 3,
    follow_redirects: bool = True,
    max_redirects: int = 5,
) -> ConnectorHttpClient:
    return ConnectorHttpClient(
        source_key="example_source",
        rate_limit=rate_limit or RateLimit(requests=100, per_seconds=1.0, concurrency=4),
        user_agent=USER_AGENT,
        usage=usage,
        max_bytes=max_bytes,
        max_attempts=max_attempts,
        # http2 negotiation is pointless against a mock transport and needs a real connection.
        client=httpx.AsyncClient(
            timeout=5.0,
            headers={"user-agent": USER_AGENT},
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
        ),
    )
