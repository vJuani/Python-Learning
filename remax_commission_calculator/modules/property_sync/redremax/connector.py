"""Read-only RedREMAX inventory connector. Does not write DB. No scraping."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from modules.property_sync.connector import (
    ConnectorCapabilities,
    PropertySourceConnector,
    register_connector,
)
from modules.property_sync.redremax.client import RedRemaxClient
from modules.property_sync.redremax.errors import (
    RedRemaxAuthError,
    RedRemaxConfigError,
    RedRemaxPartialError,
)
from modules.property_sync.redremax.filters import RedRemaxSyncFilterConfig
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX
from modules.property_sync.redremax.normalizer import RedRemaxPropertyNormalizer

logger = logging.getLogger(__name__)


@dataclass
class ListingBatch:
    items: list = field(default_factory=list)
    source_total: int = 0
    pages_fetched: int = 0
    incomplete: bool = False
    office_id: str = ""


def configured_office_id(integration):
    config = (integration or {}).get("config") or {}
    return str(config.get("external_office_id") or "").strip()


class RedRemaxConnector(PropertySourceConnector):
    provider = PROVIDER_REDREMAX
    capabilities = ConnectorCapabilities(
        supports_incremental_sync=True,
        supports_media=True,
        supports_agents=True,
        supports_deleted_status=True,
        supports_webhooks=False,
        media_strategy="remote_reference",
        media_url_kind="stable",
    )

    def __init__(self, *, client=None, normalizer=None, filters=None):
        self.client = client or RedRemaxClient()
        self.normalizer = normalizer or RedRemaxPropertyNormalizer()
        self.filters = filters or RedRemaxSyncFilterConfig()

    def test_connection(self, integration):
        office_id = configured_office_id(integration)
        logger.info(
            "RedREMAX test_connection start office_id=%s",
            office_id or "-",
        )
        if not office_id:
            logger.warning("RedREMAX test_connection missing office_id")
            raise RedRemaxConfigError("redremax_err_office_required")
        token_configured = bool(self.client.resolved_token())
        logger.info(
            "RedREMAX auth configured=%s office_id=%s",
            token_configured,
            office_id,
        )
        if not token_configured:
            raise RedRemaxAuthError("redremax_err_token_missing")
        page = self.client.get_listings(
            office_id=office_id,
            page=1,
            page_size=1,
            filters=self.filters,
        )
        logger.info(
            "RedREMAX test_connection ok office_id=%s source_total_items=%s",
            office_id,
            page.get("total_items") or 0,
        )
        results = page.get("results") or []
        for item in results:
            office = str((item or {}).get("office") or "").strip()
            if office and office != office_id:
                raise RedRemaxConfigError("redremax_warn_office_mismatch")
        return {
            "ok": True,
            "connected": True,
            "office_id": office_id,
            "source_total_items": page.get("total_items") or 0,
        }

    def list_properties(self, integration, *, updated_since=None, cursor=None):
        office_id = configured_office_id(integration)
        if not office_id:
            raise RedRemaxConfigError()
        return self.fetch_office_listings(office_id)

    def fetch_office_listings(self, office_id, *, agent=None):
        """Organization sync must omit `agent`. Tests may pass it explicitly."""
        page = 1
        items = []
        pages_fetched = 0
        source_total = 0
        total_pages = 1
        while page <= total_pages:
            try:
                payload = self.client.get_listings(
                    office_id=office_id,
                    page=page,
                    page_size=self.filters.normalized_page_size(),
                    filters=self.filters,
                    agent=agent,
                )
            except RedRemaxAuthError:
                raise
            except Exception:
                logger.exception(
                    "RedREMAX page failed office_id=%s page=%s",
                    office_id,
                    page,
                )
                if pages_fetched == 0:
                    raise
                raise RedRemaxPartialError(
                    items,
                    page=page,
                    pages_fetched=pages_fetched,
                    source_total=source_total,
                )
            pages_fetched += 1
            source_total = payload.get("total_items") or source_total
            total_pages = max(1, payload.get("total_pages") or 1)
            for raw in payload.get("results") or []:
                normalized, warnings = self.normalizer.normalize(
                    raw, expected_office_id=office_id
                )
                if normalized is None:
                    items.append(
                        {
                            "_skipped": True,
                            "warnings": warnings,
                            "external_id": (raw or {}).get("id"),
                        }
                    )
                    continue
                items.append(normalized)
            page += 1
        return ListingBatch(
            items=items,
            source_total=source_total,
            pages_fetched=pages_fetched,
            office_id=office_id,
        )

    def get_property(self, integration, external_id):
        batch = self.list_properties(integration)
        identity = str(external_id or "").strip()
        for item in batch.items:
            if item.get("external_id") == identity:
                return item
        return None

    def list_property_media(self, integration, external_id):
        item = self.get_property(integration, external_id)
        if not item:
            return []
        return list(item.get("media") or [])


register_connector(RedRemaxConnector())
