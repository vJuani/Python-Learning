"""
External property listings repository.
"""

from datetime import datetime

from .connection import (
    IntegrityError,
    execute_insert,
    get_connection,
)
from .tenant import (
    TenantError,
    assert_property_in_organization,
    require_organization_id,
)


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _normalize_external_id(value):
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    return value


def _normalize_optional_url(value):
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    return value


def _build_listing_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "property_id": row[2],
        "provider": row[3],
        "provider_label": row[4],
        "external_id": row[5],
        "url": row[6],
        "status": row[7],
        "listing_currency": row[8],
        "buyer_side_commission_percent": row[9],
        "seller_side_commission_percent": row[10],
        "created_at": row[11],
        "updated_at": row[12],
        "last_synced_at": row[13],
        "created_by_user_id": row[14],
        "updated_by_user_id": row[15],
    }


LISTINGS_BASE_QUERY = """
    SELECT
        property_external_listings.id,
        property_external_listings.organization_id,
        property_external_listings.property_id,
        property_external_listings.provider,
        property_external_listings.provider_label,
        property_external_listings.external_id,
        property_external_listings.url,
        property_external_listings.status,
        property_external_listings.listing_currency,
        property_external_listings.buyer_side_commission_percent,
        property_external_listings.seller_side_commission_percent,
        property_external_listings.created_at,
        property_external_listings.updated_at,
        property_external_listings.last_synced_at,
        property_external_listings.created_by_user_id,
        property_external_listings.updated_by_user_id
    FROM property_external_listings
"""


class ListingPersistenceError(ValueError):
    pass


def _translate_integrity_error(error):
    message = str(error).lower()

    if "external_id" in message:
        return ListingPersistenceError(
            "listing_duplicate_external_id"
        )

    if "provider" in message:
        return ListingPersistenceError(
            "listing_duplicate_provider"
        )

    return ListingPersistenceError(
        "listing_save_failed"
    )


def list_property_external_listings(
    property_id,
    organization_id,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        LISTINGS_BASE_QUERY
        + """
        WHERE property_external_listings.organization_id = ?
            AND property_external_listings.property_id = ?
        ORDER BY
            CASE property_external_listings.provider
                WHEN 'remax_web' THEN 0
                WHEN 'organization_website' THEN 1
                WHEN 'zonaprop' THEN 2
                WHEN 'argenprop' THEN 3
                WHEN 'mercadolibre' THEN 4
                ELSE 5
            END,
            property_external_listings.id
        """,
        (
            organization_id,
            property_id,
        ),
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        _build_listing_dict(row)
        for row in rows
    ]


def _listing_with_property_from_row(row):
    listing = _build_listing_dict(row)

    if listing is None:
        return None

    listing["property_address"] = row[16]
    return listing


LISTING_WITH_PROPERTY_QUERY = """
    SELECT
        property_external_listings.id,
        property_external_listings.organization_id,
        property_external_listings.property_id,
        property_external_listings.provider,
        property_external_listings.provider_label,
        property_external_listings.external_id,
        property_external_listings.url,
        property_external_listings.status,
        property_external_listings.listing_currency,
        property_external_listings.buyer_side_commission_percent,
        property_external_listings.seller_side_commission_percent,
        property_external_listings.created_at,
        property_external_listings.updated_at,
        property_external_listings.last_synced_at,
        property_external_listings.created_by_user_id,
        property_external_listings.updated_by_user_id,
        properties.address AS property_address
    FROM property_external_listings
    INNER JOIN properties
        ON properties.id = property_external_listings.property_id
        AND properties.organization_id
            = property_external_listings.organization_id
"""


def find_listing_by_provider_for_property(
    property_id,
    organization_id,
    provider,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        LISTING_WITH_PROPERTY_QUERY
        + """
        WHERE property_external_listings.organization_id = ?
            AND property_external_listings.property_id = ?
            AND property_external_listings.provider = ?
        LIMIT 1
        """,
        (
            organization_id,
            property_id,
            provider,
        ),
    )

    row = cursor.fetchone()
    connection.close()

    return _listing_with_property_from_row(row)


def find_listing_by_external_id(
    organization_id,
    provider,
    external_id,
):
    organization_id = require_organization_id(
        organization_id
    )
    external_id = _normalize_external_id(external_id)

    if external_id is None:
        return None

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        LISTING_WITH_PROPERTY_QUERY
        + """
        WHERE property_external_listings.organization_id = ?
            AND property_external_listings.provider = ?
            AND property_external_listings.external_id = ?
        LIMIT 1
        """,
        (
            organization_id,
            provider,
            external_id,
        ),
    )

    row = cursor.fetchone()
    connection.close()

    return _listing_with_property_from_row(row)


def get_property_external_listing(
    listing_id,
    organization_id,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        LISTINGS_BASE_QUERY
        + """
        WHERE property_external_listings.id = ?
            AND property_external_listings.organization_id = ?
        """,
        (
            listing_id,
            organization_id,
        ),
    )

    row = cursor.fetchone()
    connection.close()

    return _build_listing_dict(row)


def create_property_external_listing(
    organization_id,
    property_id,
    provider,
    url,
    status,
    *,
    external_id=None,
    provider_label=None,
    created_by_user_id=None,
    last_synced_at=None,
    listing_currency=None,
    buyer_side_commission_percent=None,
    seller_side_commission_percent=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    external_id = _normalize_external_id(external_id)
    url = _normalize_optional_url(url)
    now = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        assert_property_in_organization(
            cursor,
            property_id,
            organization_id,
        )

        listing_id = execute_insert(
            cursor,
            """
            INSERT INTO property_external_listings (
                organization_id,
                property_id,
                provider,
                provider_label,
                external_id,
                url,
                status,
                listing_currency,
                buyer_side_commission_percent,
                seller_side_commission_percent,
                created_at,
                updated_at,
                last_synced_at,
                created_by_user_id,
                updated_by_user_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                property_id,
                provider,
                provider_label,
                external_id,
                url,
                status,
                listing_currency,
                buyer_side_commission_percent,
                seller_side_commission_percent,
                now,
                now,
                last_synced_at,
                created_by_user_id,
                created_by_user_id,
            ),
        )
        connection.commit()

    except TenantError:
        connection.rollback()
        raise

    except IntegrityError as error:
        connection.rollback()
        raise _translate_integrity_error(error) from error

    finally:
        connection.close()

    return get_property_external_listing(
        listing_id,
        organization_id,
    )


def update_property_external_listing(
    listing_id,
    organization_id,
    *,
    provider,
    url,
    status,
    external_id=None,
    provider_label=None,
    updated_by_user_id=None,
    last_synced_at=None,
    listing_currency=None,
    buyer_side_commission_percent=None,
    seller_side_commission_percent=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    external_id = _normalize_external_id(external_id)
    url = _normalize_optional_url(url)
    now = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT property_id
            FROM property_external_listings
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                listing_id,
                organization_id,
            ),
        )
        row = cursor.fetchone()

        if row is None:
            connection.close()
            return None

        assert_property_in_organization(
            cursor,
            row[0],
            organization_id,
        )

        cursor.execute(
            """
            UPDATE property_external_listings
            SET provider = ?,
                provider_label = ?,
                external_id = ?,
                url = ?,
                status = ?,
                listing_currency = ?,
                buyer_side_commission_percent = ?,
                seller_side_commission_percent = ?,
                updated_at = ?,
                updated_by_user_id = ?,
                last_synced_at = COALESCE(?, last_synced_at)
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                provider,
                provider_label,
                external_id,
                url,
                status,
                listing_currency,
                buyer_side_commission_percent,
                seller_side_commission_percent,
                now,
                updated_by_user_id,
                last_synced_at,
                listing_id,
                organization_id,
            ),
        )

        connection.commit()

    except TenantError:
        connection.rollback()
        raise

    except IntegrityError as error:
        connection.rollback()
        raise _translate_integrity_error(error) from error

    finally:
        connection.close()

    return get_property_external_listing(
        listing_id,
        organization_id,
    )


def delete_property_external_listing(
    listing_id,
    organization_id,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT property_id
        FROM property_external_listings
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            listing_id,
            organization_id,
        ),
    )
    row = cursor.fetchone()

    if row is None:
        connection.close()
        return False

    try:
        assert_property_in_organization(
            cursor,
            row[0],
            organization_id,
        )

        cursor.execute(
            """
            DELETE FROM property_external_listings
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                listing_id,
                organization_id,
            ),
        )

        deleted = cursor.rowcount > 0
        connection.commit()

    except TenantError:
        connection.rollback()
        raise

    finally:
        connection.close()

    return deleted


def list_synced_listings_for_provider(
    organization_id,
    provider,
    *,
    agent_id=None,
):
    """
    Listings with external_id for a provider, optionally scoped to an agent.
    """
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    params = [
        organization_id,
        provider,
    ]

    agent_clause = ""

    if agent_id is not None:
        agent_clause = " AND properties.agent_id = ?"
        params.append(agent_id)

    cursor.execute(
        f"""
        {LISTINGS_BASE_QUERY}
        INNER JOIN properties
            ON properties.id
                = property_external_listings.property_id
            AND properties.organization_id
                = property_external_listings.organization_id
        WHERE property_external_listings.organization_id = ?
            AND property_external_listings.provider = ?
            AND property_external_listings.external_id IS NOT NULL
            AND property_external_listings.external_id != ''
            {agent_clause}
        ORDER BY property_external_listings.id
        """,
        params,
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        _build_listing_dict(row)
        for row in rows
    ]


def mark_listing_inactive(
    listing_id,
    organization_id,
    *,
    last_synced_at=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()

    if last_synced_at is None:
        last_synced_at = now

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE property_external_listings
        SET
            status = 'inactive',
            updated_at = ?,
            last_synced_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            now,
            last_synced_at,
            listing_id,
            organization_id,
        ),
    )

    updated = cursor.rowcount > 0
    connection.commit()
    connection.close()

    return updated
