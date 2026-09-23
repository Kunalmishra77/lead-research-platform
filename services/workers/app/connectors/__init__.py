"""Data-source connectors (docs/08): one module per source, all sharing the same contract."""

from app.connectors.base import BaseConnector
from app.connectors.http_client import CallCost, ConnectorHttpClient
from app.connectors.registry import ConnectorRegistry
from app.connectors.restrictions import restriction_reason
from app.connectors.types import (
    AuthKind,
    Candidate,
    ConnectorContext,
    ConnectorHealth,
    DiscoveryQuery,
    FieldValue,
    RateLimit,
    RawResult,
    SourceRef,
    TosClass,
)
from app.connectors.usage import NullUsageRecorder, SqlUsageRecorder, UsageRecorder, call_unit_key

__all__ = [
    "AuthKind",
    "BaseConnector",
    "CallCost",
    "Candidate",
    "ConnectorContext",
    "ConnectorHealth",
    "ConnectorHttpClient",
    "ConnectorRegistry",
    "DiscoveryQuery",
    "FieldValue",
    "NullUsageRecorder",
    "RateLimit",
    "RawResult",
    "SourceRef",
    "SqlUsageRecorder",
    "TosClass",
    "UsageRecorder",
    "call_unit_key",
    "restriction_reason",
]
