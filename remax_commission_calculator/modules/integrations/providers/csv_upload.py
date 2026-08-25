"""
CSV upload adapter: reads staged payload from integration config.
"""

from __future__ import annotations

from modules.database.organization_integrations_repository import (
    PROVIDER_CSV_UPLOAD,
)
from modules.integrations.csv_import import (
    payload_to_external,
)


class CsvUploadAdapter:
    provider = PROVIDER_CSV_UPLOAD

    def _payload(self, integration):
        config = integration.get("config") or {}
        payload = config.get("payload")

        if not isinstance(payload, dict):
            raise ValueError("csv_upload_payload_required")

        return payload

    def list_agents(self, integration):
        if integration.get("scope_type") != "organization":
            return []

        agents, _properties = payload_to_external(
            self._payload(integration)
        )
        return agents

    def list_properties(self, integration):
        _agents, properties = payload_to_external(
            self._payload(integration)
        )
        return properties
