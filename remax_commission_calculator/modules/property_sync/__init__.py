"""Property Sync Hub: connectors → normalize → JRH Property. No second model."""

from modules.property_sync.connector import (
    ConnectorCapabilities,
    PropertySourceConnector,
    get_connector,
)
from modules.property_sync.mock import (
    MockPropertySourceConnector,
    reset_mock_catalog,
)
from modules.property_sync.service import (
    dry_run_property_sync,
    ensure_mock_integration,
    ensure_redremax_integration,
    run_property_sync,
    sync_external_property,
    test_property_source_connection,
)

__all__ = (
    "ConnectorCapabilities",
    "MockPropertySourceConnector",
    "PropertySourceConnector",
    "dry_run_property_sync",
    "ensure_mock_integration",
    "ensure_redremax_integration",
    "get_connector",
    "reset_mock_catalog",
    "run_property_sync",
    "sync_external_property",
    "test_property_source_connection",
)
