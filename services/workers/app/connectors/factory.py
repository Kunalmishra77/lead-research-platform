"""Building the connector registry a worker uses (docs/08 capability map).

A source whose key is missing is left out rather than registered and failed at call time: the
planner asks the registry what can fill a field, and a connector that cannot run must not be part
of that answer.
"""

import structlog
from redis.asyncio import Redis

from app.config import Settings
from app.connectors.google_places import GooglePlacesConnector, PlaceSearchCache
from app.connectors.http_client import ConnectorHttpClient
from app.connectors.registry import ConnectorRegistry
from app.connectors.serp import SerpConnector
from app.metering.free_tier import FreeTierGuard, FreeTierLimit, NullFreeTierGuard
from app.metering.usage import UsageRecorder

#: Honest, with a contact URL, as docs/08 requires of every request we make.
USER_AGENT = "LeadForgeBot/1.0 (+https://leadforge.example/bot)"


def build_connectors(
    *,
    settings: Settings,
    redis: Redis,
    usage: UsageRecorder | None = None,
    log: structlog.stdlib.BoundLogger | None = None,
) -> tuple[ConnectorRegistry, list[ConnectorHttpClient]]:
    """Returns the registry and the clients it owns, which the caller closes when the job ends."""
    logger = log or structlog.get_logger("leadforge.connectors")
    registry = ConnectorRegistry()
    clients: list[ConnectorHttpClient] = []

    if settings.GOOGLE_PLACES_API_KEY and settings.GOOGLE_PLACES_ENABLED:
        client = ConnectorHttpClient(
            source_key=GooglePlacesConnector.key,
            rate_limit=GooglePlacesConnector.rate_limit,
            user_agent=USER_AGENT,
            usage=usage,
            redis=redis,
            log=logger.bind(source=GooglePlacesConnector.key),
        )
        clients.append(client)
        registry.register(
            GooglePlacesConnector(
                client,
                settings.GOOGLE_PLACES_API_KEY,
                cache=PlaceSearchCache(redis),
                free_tier=_places_allowance(settings, redis, logger),
            )
        )
    elif not settings.GOOGLE_PLACES_API_KEY:
        logger.warning("GOOGLE_PLACES_API_KEY is not set; google_places is not available")
    else:
        # Places content may be kept for 30 days and must then be deleted (ADR-0011). Until the
        # sweeper that does the deleting exists, a key on its own must not start storing it.
        logger.warning(
            "google_places is configured but disabled; set GOOGLE_PLACES_ENABLED once the "
            "30-day sweeper is in place (ADR-0011)"
        )

    if settings.SERPER_API_KEY:
        serp_client = ConnectorHttpClient(
            source_key=SerpConnector.key,
            rate_limit=SerpConnector.rate_limit,
            user_agent=USER_AGENT,
            usage=usage,
            redis=redis,
            log=logger.bind(source=SerpConnector.key),
        )
        clients.append(serp_client)
        registry.register(SerpConnector(serp_client, settings.SERPER_API_KEY))
    else:
        logger.warning("SERPER_API_KEY is not set; serp is not available")

    return registry, clients


async def close_clients(clients: list[ConnectorHttpClient]) -> None:
    for client in clients:
        await client.aclose()


def _places_allowance(
    settings: Settings, redis: Redis, log: structlog.stdlib.BoundLogger
) -> FreeTierLimit:
    """The cap that keeps Places inside its free monthly allowance, or none for a paid account.

    This exists because the guard was written, tested, and then wired to nothing: the allowance
    was documented in a comment while the code would have sailed past it at $35 per thousand
    without a different response to notice. An operator running this with no budget needs the
    limit to be a mechanism, not a note.
    """
    limit = settings.GOOGLE_PLACES_FREE_CALLS_PER_MONTH
    if limit is None:
        log.info("google_places has no free-tier cap; calls past any allowance will be billed")
        return NullFreeTierGuard()
    guard = FreeTierGuard(
        redis,
        sku=GooglePlacesConnector.meter,
        monthly_limit=limit,
        headroom=settings.GOOGLE_PLACES_FREE_HEADROOM,
    )
    log.info(
        "google_places capped to its free allowance",
        monthly_limit=limit,
        headroom=settings.GOOGLE_PLACES_FREE_HEADROOM,
    )
    return guard
