"""The AI layer (docs/07): one gateway, versioned prompts, schema-validated output."""

from app.ai.factory import build_gateway
from app.ai.gateway import AiGateway
from app.ai.spend import AI_METER
from app.ai.types import AiResult, Prompt, ProviderResult, Tier, TokenUsage

__all__ = [
    "AI_METER",
    "AiGateway",
    "AiResult",
    "Prompt",
    "ProviderResult",
    "Tier",
    "TokenUsage",
    "build_gateway",
]
