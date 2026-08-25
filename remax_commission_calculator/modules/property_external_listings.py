"""
Domain helpers for manual external property listings (Stage 1).
"""

from __future__ import annotations

from modules.database.property_external_listings_repository import (
    ListingPersistenceError,
    create_property_external_listing,
    delete_property_external_listing,
    find_listing_by_external_id,
    find_listing_by_provider_for_property,
    get_property_external_listing,
    list_property_external_listings,
    update_property_external_listing,
)
from modules.i18n import translate


PROVIDER_REMAX_WEB = "remax_web"
PROVIDER_ORGANIZATION_WEBSITE = "organization_website"
PROVIDER_ZONAPROP = "zonaprop"
PROVIDER_ARGENPROP = "argenprop"
PROVIDER_MERCADOLIBRE = "mercadolibre"
PROVIDER_OTHER = "other"

STRUCTURED_PROVIDERS = (
    PROVIDER_REMAX_WEB,
    PROVIDER_ORGANIZATION_WEBSITE,
    PROVIDER_ZONAPROP,
    PROVIDER_ARGENPROP,
    PROVIDER_MERCADOLIBRE,
)

LISTING_PROVIDERS = STRUCTURED_PROVIDERS + (PROVIDER_OTHER,)

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_RESERVED = "reserved"
STATUS_SOLD = "sold"
STATUS_INACTIVE = "inactive"

LISTING_STATUSES = (
    STATUS_ACTIVE,
    STATUS_PAUSED,
    STATUS_RESERVED,
    STATUS_SOLD,
    STATUS_INACTIVE,
)

PROVIDER_LABEL_KEYS = {
    PROVIDER_REMAX_WEB: "listing_provider_remax_web",
    PROVIDER_ORGANIZATION_WEBSITE: (
        "listing_provider_organization_website"
    ),
    PROVIDER_ZONAPROP: "listing_provider_zonaprop",
    PROVIDER_ARGENPROP: "listing_provider_argenprop",
    PROVIDER_MERCADOLIBRE: "listing_provider_mercadolibre",
    PROVIDER_OTHER: "listing_provider_other",
}

STATUS_LABEL_KEYS = {
    STATUS_ACTIVE: "listing_status_active",
    STATUS_PAUSED: "listing_status_paused",
    STATUS_RESERVED: "listing_status_reserved",
    STATUS_SOLD: "listing_status_sold",
    STATUS_INACTIVE: "listing_status_inactive",
}


def provider_label(provider, language="es", provider_label=None):
    if provider == PROVIDER_OTHER and provider_label:
        return provider_label.strip()

    key = PROVIDER_LABEL_KEYS.get(provider)

    if key is None:
        return provider

    return translate(key, language=language)


def status_label(status, language="es"):
    key = STATUS_LABEL_KEYS.get(status, status)
    return translate(key, language=language)


def listing_view_label_key(provider):
    if provider == PROVIDER_REMAX_WEB:
        return "listing_view_remax"

    return "listing_view_publication"


def provider_options(language="es"):
    return [
        {
            "value": provider,
            "label": provider_label(provider, language=language),
        }
        for provider in LISTING_PROVIDERS
    ]


def status_options(language="es"):
    return [
        {
            "value": status,
            "label": status_label(status, language=language),
        }
        for status in LISTING_STATUSES
    ]


def format_property_display_id(property_id):
    return f"PROP-{int(property_id):06d}"


def validate_listing_form(
    provider,
    url,
    status,
    external_id=None,
    provider_label=None,
):
    errors = []

    provider = (provider or "").strip()

    if provider not in LISTING_PROVIDERS:
        errors.append("err_listing_invalid_provider")

    url = (url or "").strip()

    if url == "":
        errors.append("err_listing_url_required")
    elif not (
        url.startswith("http://")
        or url.startswith("https://")
    ):
        errors.append("err_listing_url_invalid")

    status = (status or "").strip()

    if status not in LISTING_STATUSES:
        errors.append("err_listing_invalid_status")

    label = (provider_label or "").strip()

    if provider == PROVIDER_OTHER and label == "":
        errors.append("err_listing_provider_label_required")

    external_id = (external_id or "").strip()

    return errors, {
        "provider": provider,
        "url": url,
        "status": status,
        "external_id": external_id or None,
        "provider_label": label or None,
    }


def enrich_listing_for_ui(listing, language="es"):
    if listing is None:
        return None

    enriched = dict(listing)
    enriched["provider_display"] = provider_label(
        listing["provider"],
        language=language,
        provider_label=listing.get("provider_label"),
    )
    enriched["status_display"] = status_label(
        listing["status"],
        language=language,
    )
    enriched["view_label_key"] = listing_view_label_key(
        listing["provider"]
    )

    return enriched


def enrich_listings_for_ui(listings, language="es"):
    return [
        enrich_listing_for_ui(listing, language=language)
        for listing in listings
    ]


def load_property_listings(
    property_id,
    organization_id,
    language="es",
):
    listings = list_property_external_listings(
        property_id,
        organization_id,
    )

    return enrich_listings_for_ui(
        listings,
        language=language,
    )


def load_property_listings_for_property(
    property_data,
    language="es",
):
    if property_data is None:
        return []

    return load_property_listings(
        property_data["id"],
        property_data["organization_id"],
        language=language,
    )


def _listing_conflict_context(
    listing,
    *,
    same_property=False,
):
    if listing is None:
        return None

    property_id = listing["property_id"]

    return {
        "same_property": same_property,
        "property_id": property_id,
        "property_display_id": format_property_display_id(
            property_id
        ),
        "address": listing.get("property_address") or "",
    }


def _check_listing_conflicts(
    organization_id,
    property_id,
    parsed,
    *,
    language="es",
):
    provider = parsed["provider"]

    if provider != PROVIDER_OTHER:
        existing_provider = (
            find_listing_by_provider_for_property(
                property_id,
                organization_id,
                provider,
            )
        )

        if existing_provider is not None:
            conflict = _listing_conflict_context(
                existing_provider,
                same_property=True,
            )
            message = translate(
                "listing_duplicate_provider_same_property",
                language=language,
            )

            return [message], conflict

    external_id = parsed.get("external_id")

    if external_id:
        existing_external = find_listing_by_external_id(
            organization_id,
            provider,
            external_id,
        )

        if (
            existing_external is not None
            and existing_external["property_id"] != property_id
        ):
            conflict = _listing_conflict_context(
                existing_external,
                same_property=False,
            )
            message = translate(
                "listing_duplicate_external_id_detail",
                language=language,
                property_display_id=conflict[
                    "property_display_id"
                ],
                address=conflict["address"],
            )

            return [message], conflict

    return [], None


def _resolve_listing_persistence_error(
    error,
    organization_id,
    property_id,
    parsed,
    *,
    language="es",
):
    error_code = str(error)

    if error_code == "listing_duplicate_provider":
        existing = find_listing_by_provider_for_property(
            property_id,
            organization_id,
            parsed["provider"],
        )
        conflict = _listing_conflict_context(
            existing,
            same_property=True,
        )
        message = translate(
            "listing_duplicate_provider_same_property",
            language=language,
        )

        return [message], conflict

    if error_code == "listing_duplicate_external_id":
        existing = find_listing_by_external_id(
            organization_id,
            parsed["provider"],
            parsed.get("external_id"),
        )

        if existing is not None:
            conflict = _listing_conflict_context(
                existing,
                same_property=(
                    existing["property_id"] == property_id
                ),
            )
            message = translate(
                "listing_duplicate_external_id_detail",
                language=language,
                property_display_id=conflict[
                    "property_display_id"
                ],
                address=conflict["address"],
            )

            return [message], conflict

    return [translate(error_code, language=language)], None


def save_new_listing(
    organization_id,
    property_id,
    form_data,
    *,
    user_id=None,
    language="es",
):
    errors, parsed = validate_listing_form(
        form_data.get("provider"),
        form_data.get("url"),
        form_data.get("status"),
        external_id=form_data.get("external_id"),
        provider_label=form_data.get("provider_label"),
    )

    if errors:
        return errors, None, parsed, None

    conflict_errors, conflict = _check_listing_conflicts(
        organization_id,
        property_id,
        parsed,
        language=language,
    )

    if conflict_errors:
        return conflict_errors, None, parsed, conflict

    try:
        listing = create_property_external_listing(
            organization_id,
            property_id,
            parsed["provider"],
            parsed["url"],
            parsed["status"],
            external_id=parsed["external_id"],
            provider_label=parsed["provider_label"],
            created_by_user_id=user_id,
        )

    except ListingPersistenceError as error:
        persistence_errors, conflict = (
            _resolve_listing_persistence_error(
                error,
                organization_id,
                property_id,
                parsed,
                language=language,
            )
        )

        return persistence_errors, None, parsed, conflict

    return [], enrich_listing_for_ui(listing, language=language), parsed, None


def save_existing_listing(
    listing_id,
    organization_id,
    form_data,
    *,
    user_id=None,
    language="es",
):
    errors, parsed = validate_listing_form(
        form_data.get("provider"),
        form_data.get("url"),
        form_data.get("status"),
        external_id=form_data.get("external_id"),
        provider_label=form_data.get("provider_label"),
    )

    if errors:
        return errors, None, parsed, None

    current = get_property_external_listing(
        listing_id,
        organization_id,
    )

    if current is None:
        return ["listing_not_found"], None, parsed, None

    property_id = current["property_id"]
    provider = parsed["provider"]

    if provider != PROVIDER_OTHER:
        existing_provider = (
            find_listing_by_provider_for_property(
                property_id,
                organization_id,
                provider,
            )
        )

        if (
            existing_provider is not None
            and existing_provider["id"] != listing_id
        ):
            conflict = _listing_conflict_context(
                existing_provider,
                same_property=True,
            )
            message = translate(
                "listing_duplicate_provider_same_property",
                language=language,
            )

            return [message], None, parsed, conflict

    external_id = parsed.get("external_id")

    if external_id:
        existing_external = find_listing_by_external_id(
            organization_id,
            provider,
            external_id,
        )

        if (
            existing_external is not None
            and existing_external["property_id"] != property_id
        ):
            conflict = _listing_conflict_context(
                existing_external,
                same_property=False,
            )
            message = translate(
                "listing_duplicate_external_id_detail",
                language=language,
                property_display_id=conflict[
                    "property_display_id"
                ],
                address=conflict["address"],
            )

            return [message], None, parsed, conflict

    try:
        listing = update_property_external_listing(
            listing_id,
            organization_id,
            provider=parsed["provider"],
            url=parsed["url"],
            status=parsed["status"],
            external_id=parsed["external_id"],
            provider_label=parsed["provider_label"],
            updated_by_user_id=user_id,
        )

    except ListingPersistenceError as error:
        persistence_errors, conflict = (
            _resolve_listing_persistence_error(
                error,
                organization_id,
                property_id,
                parsed,
                language=language,
            )
        )

        return persistence_errors, None, parsed, conflict

    if listing is None:
        return ["listing_not_found"], None, parsed, None

    return [], enrich_listing_for_ui(listing, language=language), parsed, None


def remove_listing(listing_id, organization_id):
    return delete_property_external_listing(
        listing_id,
        organization_id,
    )


def get_listing_record(listing_id, organization_id):
    return get_property_external_listing(
        listing_id,
        organization_id,
    )
