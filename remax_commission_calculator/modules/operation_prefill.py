"""
Property prefill and autocomplete helpers for the new operation form.
"""

from __future__ import annotations

from modules.database import (
    get_organization_settings,
    get_property_record,
    is_property_available_for_operation,
    list_available_properties_for_operation,
    list_property_external_listings,
)


def _display_external_id(property_data):
    external_id = property_data.get("external_id")
    if external_id:
        return external_id
    return f"PROP-{property_data['id']:06d}"


def _resolve_listing_currency(property_id, organization_id, org_settings):
    listings = list_property_external_listings(
        property_id,
        organization_id,
    )
    for listing in listings:
        currency = listing.get("listing_currency")
        if currency in ("USD", "ARS"):
            return currency
    return org_settings.get("default_currency") or "USD"


def _resolve_commission_defaults(
    property_id,
    organization_id,
    org_settings,
):
    buyer = org_settings.get(
        "default_buyer_commission_percent"
    )
    seller = org_settings.get(
        "default_seller_commission_percent"
    )

    listings = list_property_external_listings(
        property_id,
        organization_id,
    )
    for listing in listings:
        listing_buyer = listing.get(
            "buyer_side_commission_percent"
        )
        listing_seller = listing.get(
            "seller_side_commission_percent"
        )
        if listing_buyer is not None:
            buyer = listing_buyer
        if listing_seller is not None:
            seller = listing_seller
        if listing_buyer is not None or listing_seller is not None:
            break

    return float(buyer or 3.0), float(seller or 3.0)


def format_property_option_label(property_data, currency, price):
    external_id = _display_external_id(property_data)
    address = property_data.get("address") or ""
    if price is not None:
        try:
            price_text = f"{float(price):,.0f}".replace(",", ".")
        except (TypeError, ValueError):
            price_text = str(price)
        return f"#{external_id} · {address} · {currency} {price_text}"
    return f"#{external_id} · {address}"


def suggest_available_properties(
    query,
    organization_id,
    *,
    agent_id=None,
    limit=15,
):
    properties = list_available_properties_for_operation(
        organization_id,
        agent_id=agent_id,
        query=query,
        limit=limit,
    )
    org_settings = get_organization_settings(
        organization_id
    )
    results = []

    for property_data in properties:
        currency = _resolve_listing_currency(
            property_data["id"],
            organization_id,
            org_settings,
        )
        price = property_data.get("listing_price")
        results.append({
            "id": property_data["id"],
            "label": format_property_option_label(
                property_data,
                currency,
                price,
            ),
            "external_id": property_data.get("external_id"),
            "address": property_data.get("address"),
            "agent_id": property_data.get("agent_id"),
            "currency": currency,
            "listing_price": price,
        })

    return results


def preliminary_commission_rate(amount, price):
    """Turn a preliminary amount into the rate field Operations already calculates.

    Operations still does original_amount * rate / 100. The form value needs
    enough decimals for that product to land on the preliminary cents.
    Four decimal places are not enough (5340 / 185000 -> 2.8865 -> 5340.03).
    """
    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    if amount in (None, "") or price in (None, ""):
        return None
    try:
        amount_value = Decimal(str(amount))
        price_value = Decimal(str(price))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if amount_value <= 0 or price_value <= 0:
        return None

    target = amount_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    exact = amount_value / price_value * Decimal("100")
    price_float = float(price_value)
    chosen = None
    for places in range(4, 13):
        candidate = exact.quantize(
            Decimal("1").scaleb(-places),
            rounding=ROUND_HALF_UP,
        )
        rebuilt = price_float * float(candidate) / 100.0
        rebuilt_cents = Decimal(rebuilt).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        if (
            rebuilt_cents == target
            and abs(Decimal(rebuilt) - target) <= Decimal("0.01")
        ):
            chosen = candidate
            break
    if chosen is None:
        chosen = exact.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    text = format(chosen, "f").rstrip("0").rstrip(".")
    return text or None


def get_property_operation_prefill(
    property_id,
    organization_id,
):
    property_data = get_property_record(
        property_id,
        organization_id,
    )
    if property_data is None:
        return None

    if not is_property_available_for_operation(
        property_id,
        organization_id,
    ):
        return None

    org_settings = get_organization_settings(
        organization_id
    )
    currency = _resolve_listing_currency(
        property_id,
        organization_id,
        org_settings,
    )
    buyer_rate, seller_rate = _resolve_commission_defaults(
        property_id,
        organization_id,
        org_settings,
    )
    listing_price = property_data.get("listing_price")

    return {
        "property_id": property_id,
        "external_id": property_data.get("external_id"),
        "address": property_data.get("address"),
        "jurisdiction": property_data.get("jurisdiction"),
        "agent_id": property_data.get("agent_id"),
        "agent_name": property_data.get("agent_name"),
        "currency": currency,
        "operation_value": listing_price,
        "buyer_commission_rate": buyer_rate,
        "seller_commission_rate": seller_rate,
        "listing_purpose": property_data.get(
            "listing_purpose"
        ),
    }
