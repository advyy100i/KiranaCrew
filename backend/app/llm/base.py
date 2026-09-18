"""LLM provider interface: one JSON completion. Providers never see IDs, prices or the catalog."""
from dataclasses import dataclass, field
from typing import Protocol


class LLMError(Exception):
    pass


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, timeout: float = 30.0) -> LLMResponse: ...
