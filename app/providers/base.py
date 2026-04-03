from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    id: str
    name: str
    homepage_url: str
    transport_mode: str
    discovery_mode: str
    detail_mode: str
    auth_mode: str
    enabled: bool
    priority: int
    notes: str = ""


class ProviderAdapter(Protocol):
    definition: ProviderDefinition
