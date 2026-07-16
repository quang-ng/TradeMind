from abc import ABC, abstractmethod


class Provider(ABC):
    """One interface, per PROJECT.md Section 8.4. Implementations return the
    raw text of the model's response and raise on any transport/provider
    failure; retry and timeout policy live outside the provider."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier used to build Signal.model_name (PROJECT.md
        Section 7.1: `provider:model-version`)."""

    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str) -> str: ...
