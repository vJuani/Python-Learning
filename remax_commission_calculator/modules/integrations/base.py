"""
Adapter protocol for external integrations.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from modules.integrations.types import (
    ExternalAgent,
    ExternalProperty,
)


class IntegrationAdapter(Protocol):
    provider: str

    def list_agents(
        self,
        integration: dict,
    ) -> Sequence[ExternalAgent]:
        ...

    def list_properties(
        self,
        integration: dict,
    ) -> Sequence[ExternalProperty]:
        ...
