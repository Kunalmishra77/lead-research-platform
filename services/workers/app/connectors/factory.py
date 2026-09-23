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
            log=logger.bind(source=GooglePlacesConnector.key),
        )
        clients.append(client)
        registry.register(
            GooglePlacesConnector(
                client,
                settings.GOOGLE_PLACES_API_KEY,
                cache=PlaceSearchCache(redis),
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

    return registry, clients


async def close_clients(clients: list[ConnectorHttpClient]) -> None:
    for client in clients:
        await client.aclose()
