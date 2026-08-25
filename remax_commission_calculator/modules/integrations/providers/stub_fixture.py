"""
Stub adapter backed by in-memory fixtures (no network).
"""

from __future__ import annotations

from modules.database.organization_integrations_repository import (
    PROVIDER_STUB_FIXTURE,
)
from modules.integrations.providers import load_fixture
from modules.integrations.types import ExternalAgent, ExternalProperty


class StubFixtureAdapter:
    provider = PROVIDER_STUB_FIXTURE

    def _payload(self, integration):
        config = integration.get("config") or {}
        fixture_key = config.get("fixture_key")

        if not fixture_key:
            raise ValueError("stub_fixture_key_required")

        return load_fixture(fixture_key)

    def list_agents(self, integration):
        if integration.get("scope_type") != "organization":
            return []

        payload = self._payload(integration)
        return list(payload["agents"])

    def list_properties(self, integration):
        payload = self._payload(integration)
        properties = list(payload["properties"])

        if integration.get("scope_type") == "agent":
            # Agent scope: fixture properties belong to the anchor agent.
            return properties

        return properties
