from common.config import LLMServiceSettings

from .anthropic_provider import AnthropicProvider
from .base import Provider


def get_provider(settings: LLMServiceSettings) -> Provider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(api_key=settings.llm_api_key, model=settings.anthropic_model)
    raise ValueError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
