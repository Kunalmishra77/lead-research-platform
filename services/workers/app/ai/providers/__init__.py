"""Provider adapters (docs/07). Only these modules import a provider SDK."""

from app.ai.providers.base import ModelProvider
from app.ai.providers.openai_provider import OpenAIProvider

__all__ = ["ModelProvider", "OpenAIProvider"]
