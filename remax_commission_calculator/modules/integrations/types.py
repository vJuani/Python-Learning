"""
DTOs for external integration sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ExternalAgent:
    external_id: str
    full_name: str
    email: Optional[str] = None
    is_active: bool = True


@dataclass(frozen=True)
class ExternalProperty:
    external_id: str
    agent_external_id: str
    address: str
    jurisdiction: str
    url: Optional[str]
    listing_provider: str
    listing_status: str = "active"
    property_type: Optional[str] = None
    listing_price: Optional[float] = None
    listing_purpose: Optional[str] = None
    listing_currency: Optional[str] = None
    buyer_side_commission_percent: Optional[float] = None
    seller_side_commission_percent: Optional[float] = None


@dataclass
class SyncResult:
    integration_id: int
    organization_id: int
    run_id: int
    status: str
    agents_created: int = 0
    agents_updated: int = 0
    properties_created: int = 0
    properties_updated: int = 0
    listings_created: int = 0
    listings_updated: int = 0
    listings_deactivated: int = 0
    error_summary: Optional[str] = None
    seen_listing_keys: set = field(default_factory=set)
