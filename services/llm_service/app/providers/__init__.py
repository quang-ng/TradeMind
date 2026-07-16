from common.config import LLMServiceSettings

from .anthropic_provider import AnthropicProvider
from .base import Provider
from .ollama_provider import OllamaProvider


def get_provider(settings: LLMServiceSettings) -> Provider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(api_key=settings.llm_api_key, model=settings.anthropic_model)
    if settings.llm_provider == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url, model=settings.ollama_model)
    raise ValueError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
