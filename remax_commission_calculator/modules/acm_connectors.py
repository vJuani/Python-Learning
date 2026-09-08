"""Future property-source connectors. No scraping. No live portal calls."""

from __future__ import annotations

from abc import ABC, abstractmethod


class SourceNotConnectedError(RuntimeError):
    """Raised when a portal connector is not authorized yet."""


class PropertySourceConnector(ABC):
    """Normalize future feeds into ComparableCandidate dicts.

    Implementations must not invent prices. Portal stubs do not touch DB.
    """

    @abstractmethod
    def get_source_name(self) -> str:
        raise NotImplementedError

    def is_connected(self) -> bool:
        return False

    def search_comparables(self, criteria: dict) -> list:
        if not self.is_connected():
            raise SourceNotConnectedError(self.get_source_name())
        return []

    def get_property(self, external_id: str):
        if not self.is_connected():
            raise SourceNotConnectedError(self.get_source_name())
        return None

    def normalize_property(self, raw: dict) -> dict:
        payload = dict(raw or {})
        return {
            "source_type": payload.get("source_type") or self.get_source_name(),
            "external_id": payload.get("external_id") or payload.get("id"),
            "address": payload.get("address") or "",
            "neighborhood": payload.get("neighborhood") or "",
            "jurisdiction": payload.get("jurisdiction") or "",
            "price": payload.get("price"),
            "currency": payload.get("currency") or "USD",
            "covered_m2": payload.get("covered_m2"),
            "total_m2": payload.get("total_m2"),
            "rooms": payload.get("rooms"),
            "bedrooms": payload.get("bedrooms"),
            "bathrooms": payload.get("bathrooms"),
            "parking_spaces": payload.get("parking_spaces"),
            "url": payload.get("url"),
            "observed_at": payload.get("observed_at"),
        }


class JRHInternalConnector(PropertySourceConnector):
    def get_source_name(self) -> str:
        return "internal_property"

    def is_connected(self) -> bool:
        return True


class RemaxConnector(PropertySourceConnector):
    def get_source_name(self) -> str:
        return "remax_web"


class ZonapropConnector(PropertySourceConnector):
    def get_source_name(self) -> str:
        return "zonaprop"


class ArgenpropConnector(PropertySourceConnector):
    def get_source_name(self) -> str:
        return "argenprop"


class MercadoLibreConnector(PropertySourceConnector):
    def get_source_name(self) -> str:
        return "mercadolibre"


def connector_registry():
    return {
        "internal_property": JRHInternalConnector(),
        "remax_web": RemaxConnector(),
        "zonaprop": ZonapropConnector(),
        "argenprop": ArgenpropConnector(),
        "mercadolibre": MercadoLibreConnector(),
    }
