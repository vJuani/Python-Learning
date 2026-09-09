"""Property inventory source connector. Distinct from ACM comparable stubs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectorCapabilities:
    supports_incremental_sync: bool = False
    supports_media: bool = False
    supports_agents: bool = False
    supports_deleted_status: bool = False
    supports_webhooks: bool = False
    media_strategy: str = "remote_reference"
    media_url_kind: str = "stable"


class PropertySourceConnector:
    """External inventory feed. Does not scrape. Does not write to DB."""

    provider = ""
    capabilities = ConnectorCapabilities()

    def test_connection(self, integration):
        return {"ok": True}

    def list_properties(self, integration, *, updated_since=None, cursor=None):
        return []

    def get_property(self, integration, external_id):
        return None

    def list_property_media(self, integration, external_id):
        return []

    def get_agent_reference(self, raw):
        raw = raw or {}
        return {
            "external_agent_id": raw.get("external_agent_id"),
            "agent_name": raw.get("agent_name"),
            "email": raw.get("agent_email") or raw.get("email"),
        }

    def get_sync_cursor(self, integration):
        return None


_CONNECTORS = {}


def register_connector(connector):
    _CONNECTORS[connector.provider] = connector
    return connector


def get_connector(provider):
    connector = _CONNECTORS.get(str(provider or "").strip())
    if connector is None:
        raise ValueError(f"unsupported_property_source:{provider}")
    return connector


def connector_registry():
    return dict(_CONNECTORS)
