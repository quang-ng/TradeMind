from abc import ABC, abstractmethod


class Provider(ABC):
    """One interface, per PROJECT.md Section 8.4. Implementations return the
    raw text of the model's response and raise on any transport/provider
    failure; retry and timeout policy live outside the provider."""

    @property
    def provider_name(self) -> str:
        """`provider` half of Signal.model_name's `provider:model-version`
        (PROJECT.md Section 7.1). Derived from the class name so it always
        matches whichever concrete Provider actually ran the call, even when
        that was chosen per-request via `AnalyzeRequest.provider_override`
        rather than the service's own env default."""
        return type(self).__name__.removesuffix("Provider").lower()

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier used to build Signal.model_name (PROJECT.md
        Section 7.1: `provider:model-version`)."""

    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str) -> str: ...
