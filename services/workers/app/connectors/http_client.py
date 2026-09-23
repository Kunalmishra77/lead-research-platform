"""The only way a connector reaches the network (docs/08).

One place enforces what every source must obey from us: per-connector rate limits and concurrency,
timeouts and a response size cap, an honest User-Agent, retries for transient failures only,
`Retry-After`, blocked-access detection that never tries to work around a block, cost metering and
tracing. (robots.txt belongs to the Phase 3 crawler, not here: these are official APIs.)

Metering covers delivered calls, not attempts: a call that ends in a retry loop, a 429 or an
oversized body records nothing, so `usage_events` stays a record of what we actually got back.
A provider that bills failed attempts would need per-attempt metering; none of the Phase 2 ones do.
"""

import asyncio
import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Self

import httpx
import structlog
from aiolimiter import AsyncLimiter
from opentelemetry import trace

from app.connectors.restrictions import restriction_reason
from app.connectors.types import RateLimit
from app.jobs.errors import (
    AccessRestrictedError,
    InvalidInputError,
    ParseFailedError,
    RateLimitedError,
    TransientError,
)
from app.metering.context import CallContext
from app.metering.usage import NullUsageRecorder, UsageRecorder, call_unit_key

_tracer = trace.get_tracer("leadforge.connectors")

#: Retried (with backoff) because they are usually temporary.
RETRYABLE_STATUS = frozenset({408, 425, 500, 502, 503, 504})
DEFAULT_TIMEOUT_S = 15.0
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
MAX_RETRY_AFTER_S = 300.0

#: Headers describing the *wire* body; the response we hand back is already decoded.
_TRANSFER_HEADERS = (b"content-encoding", b"content-length", b"transfer-encoding")


@dataclass(frozen=True, slots=True)
class CallCost:
    """What one call costs us internally, and how it is metered (docs/11)."""

    meter: str
    cost_micros: int = 0


class ConnectorHttpClient:
    """Shared HTTP client for one connector. Create it per job, close it when done."""

    def __init__(
        self,
        *,
        source_key: str,
        rate_limit: RateLimit,
        user_agent: str,
        usage: UsageRecorder | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
        log: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._source_key = source_key
        self._usage = usage or NullUsageRecorder()
        self._max_bytes = max_bytes
        self._max_attempts = max_attempts
        self._log = log or structlog.get_logger("leadforge.connectors").bind(source=source_key)
        self._limiter = AsyncLimiter(rate_limit.requests, rate_limit.per_seconds)
        self._semaphore = asyncio.Semaphore(rate_limit.concurrency)
        self._client = client or httpx.AsyncClient(
            http2=True,
            timeout=timeout_s,
            follow_redirects=True,
            max_redirects=5,
            headers={"user-agent": user_agent},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        cost: CallCost,
        ctx: CallContext | None = None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """One metered, rate-limited call.

        Raises a classified `JobError` for anything a connector must not simply parse: a block, a
        rate limit, a transient failure that outlived its retries, an undecodable or oversized
        body. Other non-2xx responses are returned as they are — only the connector knows whether
        its source means "no such place" by 404 or "bad query" by 400.
        """
        if not url.lower().startswith(("http://", "https://")):
            raise InvalidInputError(f"unsupported url scheme: {url}")
        if cost.cost_micros > 0 and (ctx is None or ctx.research_job_id is None):
            # An unattributable paid call would spend money off the books (docs/11).
            raise InvalidInputError(
                f"{self._source_key}: a paid call needs an org and research job to meter against"
            )
        request = self._client.build_request(
            method, url, headers=dict(headers or {}), params=params, json=json, content=content
        )
        log = self._log.bind(
            url=str(request.url.copy_with(query=None)),
            org_id=ctx.org_id if ctx else None,
            job_id=ctx.research_job_id if ctx else None,
            trace_id=ctx.trace_id if ctx else None,
        )
        with _tracer.start_as_current_span(f"{self._source_key} {method}") as span:
            span.set_attribute("http.request.method", method)
            span.set_attribute("server.address", request.url.host)
            response = await self._send_with_retries(request, span, log)
        await self._meter(cost, request, ctx)
        return response

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def _send_with_retries(
        self, request: httpx.Request, span: trace.Span, log: structlog.stdlib.BoundLogger
    ) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._send_once(request)
            except httpx.TooManyRedirects as exc:
                # A redirect loop is usually a consent or login wall dressed up as navigation.
                log.warning(
                    "source restricted access",
                    reason="redirect_loop",
                    error_class="access_restricted",
                )
                raise AccessRestrictedError(f"redirect_loop: {exc}") from exc
            except httpx.DecodingError as exc:
                raise ParseFailedError(f"undecodable response body: {exc}") from exc
            except httpx.RequestError as exc:
                # Timeouts, resets, DNS failures: worth another attempt.
                last = TransientError(f"{type(exc).__name__}: {exc}")
            else:
                span.set_attribute("http.response.status_code", response.status_code)
                blocked = restriction_reason(
                    response.status_code, response.headers, response.content
                )
                if blocked:
                    # Never retry and never evade: the source told us to stop (docs/08).
                    log.warning(
                        "source restricted access",
                        status=response.status_code,
                        reason=blocked,
                        error_class="access_restricted",
                    )
                    raise AccessRestrictedError(f"{blocked} ({response.status_code})")
                if response.status_code == 429:
                    raise RateLimitedError(
                        f"rate limited by {request.url.host}",
                        retry_after_s=retry_after_seconds(response.headers),
                    )
                if response.status_code in RETRYABLE_STATUS:
                    last = TransientError(f"http {response.status_code} from {request.url.host}")
                else:
                    return response
            if attempt < self._max_attempts:
                await asyncio.sleep(backoff_delay(attempt))
        raise last or TransientError("request failed")

    async def _send_once(self, request: httpx.Request) -> httpx.Response:
        async with self._semaphore, self._limiter:
            response = await self._client.send(request, stream=True)
            body = bytearray()
            try:
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > self._max_bytes:
                        raise ParseFailedError(
                            f"response larger than {self._max_bytes} bytes from {request.url.host}"
                        )
            finally:
                await response.aclose()
            # Hand back a fully read response so callers can use .json()/.content freely.
            # `aiter_bytes` already decoded the body, so the wire headers must not travel
            # with it: httpx would otherwise try to gunzip bytes that are already plain.
            return httpx.Response(
                status_code=response.status_code,
                headers=_decoded_headers(response.headers),
                content=bytes(body),
                request=request,
            )

    async def _meter(self, cost: CallCost, request: httpx.Request, ctx: CallContext | None) -> None:
        if cost.cost_micros <= 0 or ctx is None:
            return
        await self._usage.record(
            org_id=ctx.org_id,
            research_job_id=ctx.research_job_id,
            meter=cost.meter,
            unit_key=call_unit_key(self._source_key, str(request.url), request.content or None),
            cost_micros=cost.cost_micros,
        )


def _decoded_headers(headers: httpx.Headers) -> httpx.Headers:
    """The response headers minus everything that described the compressed wire body."""
    return httpx.Headers(
        [(name, value) for name, value in headers.raw if name.lower() not in _TRANSFER_HEADERS]
    )


def backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter, capped, so a struggling source is not hammered."""
    return min(2.0**attempt * 0.25, 4.0) * (0.5 + random.random())  # noqa: S311 - jitter, not crypto


def retry_after_seconds(headers: httpx.Headers) -> float | None:
    """`Retry-After` in either RFC 9110 form: delay-seconds, or an HTTP date."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(max(float(raw), 0.0), MAX_RETRY_AFTER_S)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return min(max((when - datetime.now(UTC)).total_seconds(), 0.0), MAX_RETRY_AFTER_S)
