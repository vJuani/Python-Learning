"""In-process mock network. No HTTP. Used to prove the hub without a vendor."""

from __future__ import annotations

from copy import deepcopy

from modules.property_sync.connector import (
    ConnectorCapabilities,
    PropertySourceConnector,
    register_connector,
)


PROVIDER_MOCK_NETWORK = "mock_network"

# 1x1 PNG. Local fixture bytes — never downloaded over the network.
MINI_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _photo(external_id, index, cover=False):
    return {
        "external_media_id": f"{external_id}-p{index}",
        "original_url": f"https://example.com/mock/{external_id}/{index}.png",
        "url_kind": "local_fixture",
        "position": index,
        "is_cover": cover,
        "content_type": "image/png",
        "content_bytes": MINI_PNG,
    }


def _photos(external_id, count):
    return [_photo(external_id, index, cover=(index == 0)) for index in range(count)]


def default_catalog():
    return [
        {
            "external_id": "MOCK-001",
            "address": "Italia 1341",
            "neighborhood": "Martínez",
            "locality": "Martínez",
            "jurisdiction": "GBA Norte",
            "price": 250000,
            "currency": "USD",
            "status": "published",
            "type": "apartment",
            "operation_type": "sale",
            "rooms": 3,
            "bedrooms": 2,
            "bathrooms": 1,
            "covered_surface": 90,
            "total_surface": 90,
            "description": "Departamento en Martínez con luz y balcón.",
            "features": ["balcony", "luminosity"],
            "external_agent_id": "mock-agent-1",
            "agent_name": "Martín Gómez",
            "agent_email": "martin.mock@example.com",
            "latitude": -34.4939,
            "longitude": -58.5078,
            "updated_at": "2026-09-01T10:00:00",
            "media": _photos("MOCK-001", 4),
        },
        {
            "external_id": "MOCK-002",
            "address": "Santa Fe 410",
            "neighborhood": "Retiro",
            "locality": "CABA",
            "jurisdiction": "CABA",
            "price": 185000,
            "currency": "USD",
            "status": "available",
            "type": "apartment",
            "operation_type": "sale",
            "rooms": 2,
            "bedrooms": 1,
            "bathrooms": 1,
            "covered_surface": 65,
            "total_surface": 65,
            "description": "Dos ambientes sobre Santa Fe.",
            "external_agent_id": "mock-agent-2",
            "agent_name": "Laura Pérez",
            "latitude": -34.5956,
            "longitude": -58.3772,
            "updated_at": "2026-09-01T11:00:00",
            "media": _photos("MOCK-002", 3),
        },
        {
            "external_id": "MOCK-003",
            "address": "Cabildo 3200",
            "neighborhood": "Núñez",
            "jurisdiction": "CABA",
            "price": 310000,
            "currency": "USD",
            "status": "active",
            "type": "apartment",
            "operation_type": "sale",
            "rooms": 4,
            "bedrooms": 3,
            "bathrooms": 2,
            "covered_surface": 110,
            "total_surface": 120,
            "description": "Piso en Núñez.",
            "latitude": -34.5489,
            "longitude": -58.4628,
            "updated_at": "2026-09-01T12:00:00",
            "media": _photos("MOCK-003", 2),
        },
        {
            "external_id": "MOCK-004",
            "address": "Libertador 15000",
            "neighborhood": "San Isidro",
            "jurisdiction": "GBA Norte",
            "price": 420000,
            "currency": "USD",
            "status": "reserved",
            "type": "house",
            "operation_type": "sale",
            "rooms": 5,
            "bedrooms": 3,
            "bathrooms": 3,
            "covered_surface": 180,
            "total_surface": 240,
            "description": "Casa en San Isidro.",
            "updated_at": "2026-09-01T13:00:00",
            "media": _photos("MOCK-004", 2),
        },
        {
            "external_id": "MOCK-005",
            "address": "Maipú 2300",
            "neighborhood": "Olivos",
            "jurisdiction": "GBA Norte",
            "price": 210000,
            "currency": "USD",
            "status": "available",
            "type": "apartment",
            "operation_type": "rental",
            "rooms": 3,
            "bedrooms": 2,
            "bathrooms": 1,
            "covered_surface": 75,
            "total_surface": 75,
            "description": "Tres ambientes en Olivos.",
            "updated_at": "2026-09-01T14:00:00",
            "media": _photos("MOCK-005", 1),
        },
    ]


_CATALOG = None
_RAISE_ON_LIST = False


def reset_mock_catalog():
    global _CATALOG, _RAISE_ON_LIST
    _CATALOG = default_catalog()
    _RAISE_ON_LIST = False
    return _CATALOG


def get_mock_catalog():
    global _CATALOG
    if _CATALOG is None:
        reset_mock_catalog()
    return _CATALOG


def set_mock_raise_on_list(value):
    global _RAISE_ON_LIST
    _RAISE_ON_LIST = bool(value)


def update_mock_property(external_id, **changes):
    catalog = get_mock_catalog()
    for item in catalog:
        if item["external_id"] == external_id:
            item.update(changes)
            return item
    raise KeyError(external_id)


def remove_mock_photo(external_id, external_media_id):
    item = update_mock_property(external_id)
    item["media"] = [
        photo
        for photo in item.get("media") or []
        if photo.get("external_media_id") != external_media_id
    ]
    return item


class MockPropertySourceConnector(PropertySourceConnector):
    provider = PROVIDER_MOCK_NETWORK
    capabilities = ConnectorCapabilities(
        supports_incremental_sync=True,
        supports_media=True,
        supports_agents=True,
        supports_deleted_status=True,
        supports_webhooks=False,
        media_strategy="managed_copy",
        media_url_kind="local_fixture",
    )

    def test_connection(self, integration):
        return {"ok": True, "provider": self.provider}

    def list_properties(self, integration, *, updated_since=None, cursor=None):
        if _RAISE_ON_LIST:
            raise RuntimeError("mock_provider_unavailable")
        catalog = deepcopy(get_mock_catalog())
        allowed = (integration or {}).get("config", {}).get("external_ids")
        if allowed:
            allowed = {str(item) for item in allowed}
            catalog = [item for item in catalog if item["external_id"] in allowed]
        if updated_since:
            catalog = [
                item
                for item in catalog
                if (item.get("updated_at") or "") >= str(updated_since)
            ]
        return catalog

    def get_property(self, integration, external_id):
        for item in self.list_properties(integration):
            if item["external_id"] == str(external_id):
                return deepcopy(item)
        return None

    def list_property_media(self, integration, external_id):
        item = self.get_property(integration, external_id)
        if not item:
            return []
        return deepcopy(item.get("media") or [])

    def get_sync_cursor(self, integration):
        stamps = [item.get("updated_at") for item in get_mock_catalog() if item.get("updated_at")]
        return max(stamps) if stamps else None


register_connector(MockPropertySourceConnector())
