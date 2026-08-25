"""
In-memory fixtures for stub_fixture provider.
"""

from __future__ import annotations

from modules.integrations.types import (
    ExternalAgent,
    ExternalProperty,
)

FIXTURE_DATA_HOUSE = "data_house"
FIXTURE_INDEPENDENT_AGENT = "independent_agent"


def _data_house_payload():
    agents = [
        ExternalAgent(
            external_id="DH-AGENT-1",
            full_name="Nieves Achard",
        ),
        ExternalAgent(
            external_id="DH-AGENT-2",
            full_name="Juan Perez",
        ),
        ExternalAgent(
            external_id="DH-AGENT-3",
            full_name="Pedro Garcia",
        ),
    ]

    properties = [
        ExternalProperty(
            external_id="DH-PROP-101",
            agent_external_id="DH-AGENT-1",
            address="Libertador 1000",
            jurisdiction="CABA",
            url="https://www.remax.com.ar/listings/dh-101",
            listing_provider="remax_web",
            listing_status="active",
            property_type="apartment",
            listing_price=120000,
            listing_purpose="sale",
        ),
        ExternalProperty(
            external_id="DH-PROP-102",
            agent_external_id="DH-AGENT-1",
            address="Cabildo 200",
            jurisdiction="CABA",
            url="https://www.remax.com.ar/listings/dh-102",
            listing_provider="remax_web",
            listing_status="active",
            property_type="house",
            listing_price=2500,
            listing_purpose="rental",
        ),
        ExternalProperty(
            external_id="DH-PROP-201",
            agent_external_id="DH-AGENT-2",
            address="Santa Fe 3000",
            jurisdiction="CABA",
            url="https://www.remax.com.ar/listings/dh-201",
            listing_provider="remax_web",
            listing_status="active",
            property_type="apartment",
            listing_price=95000,
            listing_purpose="sale",
        ),
        ExternalProperty(
            external_id="DH-PROP-301",
            agent_external_id="DH-AGENT-3",
            address="Melo 500",
            jurisdiction="PBA",
            url="https://www.remax.com.ar/listings/dh-301",
            listing_provider="remax_web",
            listing_status="active",
            property_type="ph",
            listing_price=80000,
            listing_purpose="sale",
        ),
    ]

    return {
        "agents": agents,
        "properties": properties,
    }


def _independent_agent_payload():
    return {
        "agents": [],
        "properties": [
            ExternalProperty(
                external_id="SOLO-PROP-1",
                agent_external_id="SOLO-AGENT",
                address="Corrientes 4500",
                jurisdiction="CABA",
                url="https://www.remax.com.ar/listings/solo-1",
                listing_provider="remax_web",
                listing_status="active",
                property_type="apartment",
                listing_price=110000,
                listing_purpose="sale",
            ),
            ExternalProperty(
                external_id="SOLO-PROP-2",
                agent_external_id="SOLO-AGENT",
                address="Rivadavia 900",
                jurisdiction="CABA",
                url="https://www.remax.com.ar/listings/solo-2",
                listing_provider="remax_web",
                listing_status="active",
                property_type="office",
                listing_price=1800,
                listing_purpose="rental",
            ),
        ],
    }


FIXTURES = {
    FIXTURE_DATA_HOUSE: _data_house_payload,
    FIXTURE_INDEPENDENT_AGENT: _independent_agent_payload,
}


def load_fixture(fixture_key):
    factory = FIXTURES.get(fixture_key)

    if factory is None:
        raise ValueError(f"unknown_fixture:{fixture_key}")

    return factory()
