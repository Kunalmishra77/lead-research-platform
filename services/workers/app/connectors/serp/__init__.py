"""SERP connector via serper.dev (docs/08, ADR-0006)."""

from app.connectors.serp.connector import SerpConnector
from app.connectors.serp.matching import (
    best_website,
    core_words,
    domain_match,
    homepage_of,
    is_aggregator,
    name_tokens,
    registrable_stem,
)

__all__ = [
    "SerpConnector",
    "best_website",
    "core_words",
    "domain_match",
    "homepage_of",
    "is_aggregator",
    "name_tokens",
    "registrable_stem",
]
