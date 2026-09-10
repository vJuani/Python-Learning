from datetime import datetime

from modules.listings_normalize import normalize_neighborhood
from modules.property_features import (
    features_to_json,
    normalize_property_features,
)
from modules.property_types import COMMERCIAL_STATUS_AVAILABLE

from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import (
    TenantError,
    assert_agent_in_organization,
    require_organization_id
)


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

ASSIGNMENT_SOURCE_MANUAL = "manual"

UNSET = object()


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _optional_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_property_dict(row):
    external_id = None
    if len(row) > 16 and row[16]:
        external_id = str(row[16]).strip() or None

    features_raw = row[27] if len(row) > 27 else None

    return {
        "id": row[0],
        "address": row[1],
        "jurisdiction": row[2],
        "organization_id": row[3],
        "agent_id": row[4],
        "agent_name": row[5],
        "status": row[6] if len(row) > 6 else STATUS_APPROVED,
        "rejection_reason": row[7] if len(row) > 7 else None,
        "reviewed_by_user_id": row[8] if len(row) > 8 else None,
        "reviewed_at": row[9] if len(row) > 9 else None,
        "created_by_user_id": row[10] if len(row) > 10 else None,
        "submitted_at": row[11] if len(row) > 11 else None,
        "property_type": row[12] if len(row) > 12 else None,
        "listing_price": row[13] if len(row) > 13 else None,
        "listing_purpose": row[14] if len(row) > 14 else None,
        "last_synced_at": row[15] if len(row) > 15 else None,
        "external_id": external_id,
        "listing_currency": row[17] if len(row) > 17 else None,
        "neighborhood": row[18] if len(row) > 18 else None,
        "rooms": row[19] if len(row) > 19 else None,
        "bedrooms": row[20] if len(row) > 20 else None,
        "bathrooms": row[21] if len(row) > 21 else None,
        "covered_m2": row[22] if len(row) > 22 else None,
        "total_m2": row[23] if len(row) > 23 else None,
        "parking_spaces": row[24] if len(row) > 24 else None,
        "description": row[25] if len(row) > 25 else None,
        "commercial_status": row[26] if len(row) > 26 else None,
        "features_json": features_raw,
        "features": normalize_property_features(features_raw),
        "formatted_address": row[28] if len(row) > 28 else None,
        "locality": row[29] if len(row) > 29 else None,
        "administrative_area": row[30] if len(row) > 30 else None,
        "country": row[31] if len(row) > 31 else None,
        "postal_code": row[32] if len(row) > 32 else None,
        "google_place_id": row[33] if len(row) > 33 else None,
        "latitude": row[34] if len(row) > 34 else None,
        "longitude": row[35] if len(row) > 35 else None,
        "geocoded_at": row[36] if len(row) > 36 else None,
        "geocode_status": row[37] if len(row) > 37 else None,
        "external_source": row[38] if len(row) > 38 else None,
        "external_updated_at": row[39] if len(row) > 39 else None,
        "sync_status": row[40] if len(row) > 40 else None,
        "external_url": row[41] if len(row) > 41 else None,
        "sync_error": row[42] if len(row) > 42 else None,
        "sync_hash": row[43] if len(row) > 43 else None,
        "is_externally_managed": bool(row[44]) if len(row) > 44 and row[44] is not None else False,
        "external_status": row[45] if len(row) > 45 else None,
        "location_source": row[46] if len(row) > 46 else None,
        "title": row[47] if len(row) > 47 else None,
        "external_metadata_json": row[48] if len(row) > 48 else None,
        "agent_assignment_source": row[49] if len(row) > 49 else None,
    }


PROPERTIES_BASE_QUERY = """
    SELECT
        properties.id,
        properties.address,
        properties.jurisdiction,
        properties.organization_id,
        properties.agent_id,
        agents.name,
        properties.status,
        properties.rejection_reason,
        properties.reviewed_by_user_id,
        properties.reviewed_at,
        properties.created_by_user_id,
        properties.submitted_at,
        properties.property_type,
        properties.listing_price,
        properties.listing_purpose,
        properties.last_synced_at,
        properties.external_id,
        properties.listing_currency,
        properties.neighborhood,
        properties.rooms,
        properties.bedrooms,
        properties.bathrooms,
        properties.covered_m2,
        properties.total_m2,
        properties.parking_spaces,
        properties.description,
        properties.commercial_status,
        properties.features_json,
        properties.formatted_address,
        properties.locality,
        properties.administrative_area,
        properties.country,
        properties.postal_code,
        properties.google_place_id,
        properties.latitude,
        properties.longitude,
        properties.geocoded_at,
        properties.geocode_status,
        properties.external_source,
        properties.external_updated_at,
        properties.sync_status,
        properties.external_url,
        properties.sync_error,
        properties.sync_hash,
        properties.is_externally_managed,
        properties.external_status,
        properties.location_source,
        properties.title,
        properties.external_metadata_json,
        properties.agent_assignment_source
    FROM properties
    LEFT JOIN agents
        ON properties.agent_id = agents.id
        AND agents.organization_id
            = properties.organization_id
"""


def count_properties(
    organization_id,
    status=None,
    agent_id=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    query = """
        SELECT COUNT(*)
        FROM properties
        WHERE organization_id = ?
    """
    params = [organization_id]

    if status is not None:
        query += " AND status = ?"
        params.append(status)

    if agent_id is not None:
        query += " AND agent_id = ?"
        params.append(agent_id)

    cursor.execute(query, params)
    count = cursor.fetchone()[0]
    connection.close()

    return count


def count_pending_properties(organization_id):
    return count_properties(
        organization_id,
        status=STATUS_PENDING
    )


def get_property_ids_used_in_operations(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT DISTINCT property_id
        FROM operations
        WHERE organization_id = ?
        """,
        (organization_id,),
    )
    used_ids = {
        row[0]
        for row in cursor.fetchall()
        if row[0] is not None
    }
    connection.close()
    return used_ids


def is_property_available_for_operation(
    property_id,
    organization_id,
):
    organization_id = require_organization_id(
        organization_id
    )

    property_data = get_property_record(
        property_id,
        organization_id,
    )
    if property_data is None:
        return False

    if property_data.get("status") != STATUS_APPROVED:
        return False

    used_ids = get_property_ids_used_in_operations(
        organization_id
    )
    return property_id not in used_ids


def list_available_properties_for_operation(
    organization_id,
    *,
    agent_id=None,
    query=None,
    limit=20,
):
    organization_id = require_organization_id(
        organization_id
    )
    limit = max(1, min(int(limit or 20), 50))

    connection = get_connection()
    cursor = connection.cursor()

    sql = (
        PROPERTIES_BASE_QUERY
        + """
        WHERE properties.organization_id = ?
            AND properties.status = ?
            AND properties.id NOT IN (
                SELECT property_id
                FROM operations
                WHERE organization_id = ?
            )
        """
    )
    params = [
        organization_id,
        STATUS_APPROVED,
        organization_id,
    ]

    if agent_id is not None:
        sql += " AND properties.agent_id = ?"
        params.append(agent_id)

    if query is not None and str(query).strip():
        needle = f"%{str(query).strip()}%"
        sql += """
            AND (
                properties.external_id LIKE ?
                OR properties.address LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM property_external_listings pel
                    WHERE pel.organization_id
                        = properties.organization_id
                        AND pel.property_id = properties.id
                        AND pel.external_id LIKE ?
                )
            )
        """
        params.extend([needle, needle, needle])

    sql += " ORDER BY properties.id LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    connection.close()

    return [
        _build_property_dict(row)
        for row in rows
    ]


def get_properties(
    organization_id,
    agent_id=None,
    status=None,
    include_all_statuses=False
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    query = PROPERTIES_BASE_QUERY + """
        WHERE properties.organization_id = ?
    """
    params = [organization_id]

    if agent_id is not None:
        query += " AND properties.agent_id = ?"
        params.append(agent_id)

    if status is not None:
        query += " AND properties.status = ?"
        params.append(status)
    elif not include_all_statuses:
        query += " AND properties.status = ?"
        params.append(STATUS_APPROVED)

    query += " ORDER BY properties.id"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    connection.close()

    return [
        _build_property_dict(row)
        for row in rows
    ]


def get_property_record(
    property_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        PROPERTIES_BASE_QUERY
        + """
        WHERE properties.id = ?
            AND properties.organization_id = ?
        """,
        (
            property_id,
            organization_id
        )
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return _build_property_dict(row)


def add_property(
    address,
    jurisdiction,
    organization_id,
    agent_id=None,
    status=STATUS_APPROVED,
    created_by_user_id=None,
    submitted_at=None,
    property_type=None,
    listing_price=None,
    listing_purpose=None,
    external_id=None,
    last_synced_at=None,
    listing_currency=None,
    neighborhood=None,
    rooms=None,
    bedrooms=None,
    bathrooms=None,
    covered_m2=None,
    total_m2=None,
    parking_spaces=None,
    description=None,
    commercial_status=COMMERCIAL_STATUS_AVAILABLE,
    features=None,
    features_json=None,
    formatted_address=None,
    locality=None,
    administrative_area=None,
    country=None,
    postal_code=None,
    google_place_id=None,
    latitude=None,
    longitude=None,
    geocoded_at=None,
    geocode_status=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    if agent_id is not None:
        connection = get_connection()
        cursor = connection.cursor()

        try:
            assert_agent_in_organization(
                cursor,
                agent_id,
                organization_id
            )
        finally:
            connection.close()

    cleaned_external_id = None
    if external_id is not None:
        cleaned_external_id = str(external_id).strip() or None

    stored_features = features_to_json(features)
    if stored_features is None and features_json is not None:
        stored_features = features_to_json(features_json)

    currency = _optional_text(listing_currency)
    if currency:
        currency = currency.upper()

    connection = get_connection()
    cursor = connection.cursor()

    property_id = execute_insert(
        cursor,
        """
        INSERT INTO properties (
            address,
            jurisdiction,
            organization_id,
            agent_id,
            status,
            created_by_user_id,
            submitted_at,
            property_type,
            listing_price,
            listing_purpose,
            external_id,
            last_synced_at,
            listing_currency,
            neighborhood,
            rooms,
            bedrooms,
            bathrooms,
            covered_m2,
            total_m2,
            parking_spaces,
            description,
            commercial_status,
            features_json,
            formatted_address,
            locality,
            administrative_area,
            country,
            postal_code,
            google_place_id,
            latitude,
            longitude,
            geocoded_at,
            geocode_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            address.strip(),
            jurisdiction.strip(),
            organization_id,
            agent_id,
            status,
            created_by_user_id,
            submitted_at,
            property_type,
            listing_price,
            listing_purpose,
            cleaned_external_id,
            last_synced_at,
            currency,
            normalize_neighborhood(neighborhood),
            rooms,
            bedrooms,
            bathrooms,
            covered_m2,
            total_m2,
            parking_spaces,
            _optional_text(description),
            commercial_status,
            stored_features,
            _optional_text(formatted_address),
            _optional_text(locality),
            _optional_text(administrative_area),
            _optional_text(country),
            _optional_text(postal_code),
            _optional_text(google_place_id),
            latitude,
            longitude,
            _optional_text(geocoded_at),
            _optional_text(geocode_status),
        )
    )

    connection.commit()
    connection.close()

    return property_id


def _normalize_optional_currency(value):
    currency = _optional_text(value)
    if currency:
        return currency.upper()
    return None


def _normalize_optional_features(value):
    if value is None or value == "":
        return None
    return features_to_json(value)


def update_property(
    property_id,
    address,
    jurisdiction,
    organization_id,
    agent_id=None,
    property_type=UNSET,
    listing_price=UNSET,
    listing_purpose=UNSET,
    external_id=UNSET,
    listing_currency=UNSET,
    neighborhood=UNSET,
    rooms=UNSET,
    bedrooms=UNSET,
    bathrooms=UNSET,
    covered_m2=UNSET,
    total_m2=UNSET,
    parking_spaces=UNSET,
    description=UNSET,
    commercial_status=UNSET,
    features=UNSET,
    features_json=UNSET,
    formatted_address=UNSET,
    locality=UNSET,
    administrative_area=UNSET,
    country=UNSET,
    postal_code=UNSET,
    google_place_id=UNSET,
    latitude=UNSET,
    longitude=UNSET,
    geocoded_at=UNSET,
    geocode_status=UNSET,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    if agent_id is not None:
        assert_agent_in_organization(
            cursor,
            agent_id,
            organization_id
        )

    assignments = [
        "address = ?",
        "jurisdiction = ?",
        "agent_id = ?",
    ]
    params = [
        address.strip(),
        jurisdiction.strip(),
        agent_id,
    ]
    cursor.execute(
        """
        SELECT agent_id
        FROM properties
        WHERE id = ?
            AND organization_id = ?
        """,
        (property_id, organization_id),
    )
    current_agent_row = cursor.fetchone()
    if current_agent_row is not None:
        current_agent_id = current_agent_row[0]
        same_agent = current_agent_id is None and agent_id is None
        if not same_agent:
            try:
                same_agent = (
                    current_agent_id is not None
                    and agent_id is not None
                    and int(current_agent_id) == int(agent_id)
                )
            except (TypeError, ValueError):
                same_agent = False
        if not same_agent:
            assignments.append("agent_assignment_source = ?")
            params.append(ASSIGNMENT_SOURCE_MANUAL)

    optional_fields = {
        "property_type": (
            property_type,
            _optional_text,
        ),
        "listing_price": (listing_price, None),
        "listing_purpose": (
            listing_purpose,
            _optional_text,
        ),
        "external_id": (
            external_id,
            _optional_text,
        ),
        "listing_currency": (
            listing_currency,
            _normalize_optional_currency,
        ),
        "neighborhood": (
            neighborhood,
            normalize_neighborhood,
        ),
        "rooms": (rooms, None),
        "bedrooms": (bedrooms, None),
        "bathrooms": (bathrooms, None),
        "covered_m2": (covered_m2, None),
        "total_m2": (total_m2, None),
        "parking_spaces": (parking_spaces, None),
        "description": (description, _optional_text),
        "commercial_status": (
            commercial_status,
            _optional_text,
        ),
        "features_json": (
            features if features is not UNSET else features_json,
            _normalize_optional_features,
        ),
        "formatted_address": (formatted_address, _optional_text),
        "locality": (locality, _optional_text),
        "administrative_area": (administrative_area, _optional_text),
        "country": (country, _optional_text),
        "postal_code": (postal_code, _optional_text),
        "google_place_id": (google_place_id, _optional_text),
        "latitude": (latitude, None),
        "longitude": (longitude, None),
        "geocoded_at": (geocoded_at, _optional_text),
        "geocode_status": (geocode_status, _optional_text),
    }

    location_passed = any(
        optional_fields[name][0] is not UNSET
        for name in (
            "formatted_address",
            "locality",
            "administrative_area",
            "country",
            "postal_code",
            "google_place_id",
            "latitude",
            "longitude",
            "geocoded_at",
            "geocode_status",
        )
    )
    if not location_passed:
        cursor.execute(
            """
            SELECT address, google_place_id, latitude, longitude
            FROM properties
            WHERE id = ?
                AND organization_id = ?
            """,
            (property_id, organization_id),
        )
        current = cursor.fetchone()
        if current is not None:
            from modules.maps.location import (
                addresses_differ_substantially,
                has_coordinates,
            )

            current_row = {
                "google_place_id": current[1],
                "latitude": current[2],
                "longitude": current[3],
            }
            if (
                (current[1] or has_coordinates(current_row))
                and addresses_differ_substantially(current[0], address)
            ):
                from modules.maps.config import GEOCODE_STALE

                optional_fields["formatted_address"] = (None, _optional_text)
                optional_fields["locality"] = (None, _optional_text)
                optional_fields["administrative_area"] = (None, _optional_text)
                optional_fields["country"] = (None, _optional_text)
                optional_fields["postal_code"] = (None, _optional_text)
                optional_fields["google_place_id"] = (None, _optional_text)
                optional_fields["latitude"] = (None, None)
                optional_fields["longitude"] = (None, None)
                optional_fields["geocoded_at"] = (None, _optional_text)
                optional_fields["geocode_status"] = (GEOCODE_STALE, _optional_text)

    for column_name, (value, transform) in optional_fields.items():
        if value is UNSET:
            continue
        assignments.append(f"{column_name} = ?")
        params.append(transform(value) if transform else value)

    params.extend([property_id, organization_id])

    cursor.execute(
        f"""
        UPDATE properties
        SET {", ".join(assignments)}
        WHERE id = ?
            AND organization_id = ?
        """,
        params,
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property not found in organization."
        )

    connection.commit()
    connection.close()


def apply_external_agent_assignment(
    property_id,
    organization_id,
    agent_id,
    source,
):
    """Assign an agent from an external mapping. Never overwrites a manual assignment."""
    organization_id = require_organization_id(organization_id)
    if not source or source == ASSIGNMENT_SOURCE_MANUAL:
        raise ValueError("external assignment source required")
    connection = get_connection()
    cursor = connection.cursor()
    try:
        assert_agent_in_organization(cursor, agent_id, organization_id)
        cursor.execute(
            """
            SELECT agent_id, agent_assignment_source
            FROM properties
            WHERE id = ?
                AND organization_id = ?
            """,
            (property_id, organization_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise TenantError("Property not found in organization.")
        current_agent_id, assignment_source = row
        if assignment_source == ASSIGNMENT_SOURCE_MANUAL:
            return False
        same_agent = (
            current_agent_id is not None
            and int(current_agent_id) == int(agent_id)
            and assignment_source == source
        )
        if same_agent:
            return False
        cursor.execute(
            """
            UPDATE properties
            SET agent_id = ?,
                agent_assignment_source = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (agent_id, source, property_id, organization_id),
        )
        if cursor.rowcount == 0:
            raise TenantError("Property not found in organization.")
        connection.commit()
        return True
    finally:
        connection.close()


def update_property_status(
    property_id,
    organization_id,
    status,
    rejection_reason=None,
    reviewed_by_user_id=None,
    reviewed_at=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE properties
        SET
            status = ?,
            rejection_reason = ?,
            reviewed_by_user_id = ?,
            reviewed_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            status,
            rejection_reason,
            reviewed_by_user_id,
            reviewed_at,
            property_id,
            organization_id,
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property not found in organization."
        )

    connection.commit()
    connection.close()


def update_property_from_sync(
    property_id,
    organization_id,
    **fields
):
    organization_id = require_organization_id(
        organization_id
    )

    allowed = {
        "address",
        "jurisdiction",
        "agent_id",
        "property_type",
        "listing_price",
        "listing_purpose",
        "external_id",
        "last_synced_at",
    }
    updates = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if not updates:
        return

    connection = get_connection()
    cursor = connection.cursor()

    if updates.get("agent_id") is not None:
        assert_agent_in_organization(
            cursor,
            updates["agent_id"],
            organization_id,
        )

    set_parts = []
    params = []

    for key, value in updates.items():
        set_parts.append(f"{key} = ?")
        params.append(value)

    params.extend([property_id, organization_id])

    cursor.execute(
        f"""
        UPDATE properties
        SET {", ".join(set_parts)}
        WHERE id = ?
            AND organization_id = ?
        """,
        params,
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property not found in organization."
        )

    connection.commit()
    connection.close()


def delete_property(
    property_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM properties
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            property_id,
            organization_id,
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property not found in organization."
        )

    connection.commit()
    connection.close()


def filter_properties(
    organization_id,
    agent_id=None,
    property_id=None,
    address=None,
    jurisdiction=None,
    property_type=None,
    listing_purpose=None,
    min_price=None,
    max_price=None,
    min_listing_price=None,
    max_listing_price=None,
    neighborhood=None,
    listing_currency=None,
    commercial_status=None,
    status=None,
    include_all_statuses=False,
    only_with_operations=False,
    only_without_operations=False,
    center_lat=None,
    center_lng=None,
    radius_m=None,
    exclude_property_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    query = PROPERTIES_BASE_QUERY
    conditions = [
        "properties.organization_id = ?"
    ]
    params = [organization_id]

    if agent_id is not None:
        conditions.append(
            "properties.agent_id = ?"
        )
        params.append(agent_id)

    if property_id is not None:
        conditions.append(
            "properties.id = ?"
        )
        params.append(property_id)

    if address is not None:
        like = f"%{address}%"
        conditions.append(
            "(properties.address LIKE ? OR COALESCE(properties.external_id, '') LIKE ?)"
        )
        params.extend([like, like])

    if jurisdiction is not None:
        conditions.append(
            "properties.jurisdiction = ?"
        )
        params.append(jurisdiction)

    if property_type is not None:
        conditions.append(
            "properties.property_type = ?"
        )
        params.append(property_type)

    if listing_purpose is not None:
        conditions.append(
            "properties.listing_purpose = ?"
        )
        params.append(listing_purpose)

    neighborhood_value = normalize_neighborhood(neighborhood)
    if neighborhood_value is not None:
        conditions.append(
            "LOWER(properties.neighborhood) LIKE ?"
        )
        params.append(f"%{neighborhood_value.lower()}%")

    if listing_currency is not None:
        conditions.append(
            "properties.listing_currency = ?"
        )
        params.append(str(listing_currency).strip().upper())

    if commercial_status is not None:
        conditions.append(
            "properties.commercial_status = ?"
        )
        params.append(commercial_status)

    if min_listing_price is not None:
        conditions.append(
            "properties.listing_price >= ?"
        )
        params.append(min_listing_price)

    if max_listing_price is not None:
        conditions.append(
            "properties.listing_price <= ?"
        )
        params.append(max_listing_price)

    if status is not None:
        conditions.append(
            "properties.status = ?"
        )
        params.append(status)
    elif not include_all_statuses:
        conditions.append(
            "properties.status = ?"
        )
        params.append(STATUS_APPROVED)

    if only_with_operations:
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM operations
                WHERE operations.property_id
                    = properties.id
                    AND operations.organization_id
                        = properties.organization_id
            )
            """
        )

    if only_without_operations:
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM operations
                WHERE operations.property_id
                    = properties.id
                    AND operations.organization_id
                        = properties.organization_id
            )
            """
        )

    if exclude_property_id is not None:
        conditions.append("properties.id != ?")
        params.append(exclude_property_id)

    from modules.maps.geo import bounding_box, bbox_sql_clauses, filter_by_radius
    from modules.maps.location import parse_coordinate

    geo_lat = parse_coordinate(center_lat, kind="lat")
    geo_lng = parse_coordinate(center_lng, kind="lng")
    try:
        geo_radius = float(radius_m) if radius_m not in (None, "") else None
    except (TypeError, ValueError):
        geo_radius = None
    geo_active = (
        geo_lat is not None
        and geo_lng is not None
        and geo_radius is not None
        and geo_radius > 0
    )
    if geo_active:
        box = bounding_box(geo_lat, geo_lng, geo_radius)
        fragment, box_params = bbox_sql_clauses(box, table="properties")
        if fragment:
            conditions.append(fragment)
            params.extend(box_params)

    if min_price is not None or max_price is not None:
        query += """
            LEFT JOIN operations
                ON operations.property_id
                    = properties.id
                AND operations.organization_id
                    = properties.organization_id
        """

    if min_price is not None:
        conditions.append(
            "operations.sale_price >= ?"
        )
        params.append(min_price)

    if max_price is not None:
        conditions.append(
            "operations.sale_price <= ?"
        )
        params.append(max_price)

    query += (
        " WHERE "
        + " AND ".join(conditions)
    )
    query += " ORDER BY properties.id"

    cursor.execute(
        query,
        params
    )

    rows = cursor.fetchall()
    connection.close()

    properties = [
        _build_property_dict(row)
        for row in rows
    ]
    if geo_active:
        return filter_by_radius(
            properties,
            geo_lat,
            geo_lng,
            geo_radius,
            exclude_id=exclude_property_id,
        )
    return properties


UNAVAILABLE_FOR_MATCH = ("sold", "rented", "withdrawn")


def list_match_candidates(
    organization_id,
    *,
    agent_id=None,
    property_types=None,
    listing_purpose=None,
    listing_currency=None,
    max_listing_price=None,
    center_lat=None,
    center_lng=None,
    radius_m=None,
    limit=300,
):
    """Approved, commercially available listings for the matcher."""
    organization_id = require_organization_id(organization_id)

    connection = get_connection()
    cursor = connection.cursor()

    conditions = [
        "properties.organization_id = ?",
        "properties.status = ?",
        """
        (
            properties.commercial_status IS NULL
            OR properties.commercial_status NOT IN ({})
        )
        """.format(
            ", ".join("?" for _ in UNAVAILABLE_FOR_MATCH)
        ),
    ]
    params = [organization_id, STATUS_APPROVED, *UNAVAILABLE_FOR_MATCH]

    if agent_id is not None:
        conditions.append("properties.agent_id = ?")
        params.append(agent_id)

    types = [value for value in (property_types or []) if value]
    if types:
        placeholders = ", ".join("?" for _ in types)
        conditions.append(
            f"("
            f"properties.property_type IN ({placeholders})"
            f" OR properties.property_type IS NULL"
            f")"
        )
        params.extend(types)

    if listing_purpose is not None:
        conditions.append(
            "("
            "properties.listing_purpose = ?"
            " OR properties.listing_purpose IS NULL"
            ")"
        )
        params.append(listing_purpose)

    if listing_currency is not None and max_listing_price is not None:
        conditions.append(
            "("
            "properties.listing_currency IS NULL"
            " OR properties.listing_currency != ?"
            " OR properties.listing_price IS NULL"
            " OR properties.listing_price <= ?"
            ")"
        )
        params.extend(
            [
                str(listing_currency).strip().upper(),
                max_listing_price,
            ]
        )

    from modules.maps.geo import bounding_box, bbox_sql_clauses, filter_by_radius
    from modules.maps.location import parse_coordinate

    geo_lat = parse_coordinate(center_lat, kind="lat")
    geo_lng = parse_coordinate(center_lng, kind="lng")
    try:
        geo_radius = float(radius_m) if radius_m not in (None, "") else None
    except (TypeError, ValueError):
        geo_radius = None
    geo_active = (
        geo_lat is not None
        and geo_lng is not None
        and geo_radius is not None
        and geo_radius > 0
    )
    if geo_active:
        box = bounding_box(geo_lat, geo_lng, geo_radius)
        fragment, box_params = bbox_sql_clauses(box, table="properties")
        if fragment:
            conditions.append(fragment)
            params.extend(box_params)

    try:
        limit_value = max(1, int(limit))
    except (TypeError, ValueError):
        limit_value = 300

    cursor.execute(
        PROPERTIES_BASE_QUERY
        + " WHERE "
        + " AND ".join(conditions)
        + " ORDER BY properties.id LIMIT ?",
        (*params, limit_value),
    )
    rows = cursor.fetchall()
    connection.close()

    properties = [_build_property_dict(row) for row in rows]
    if geo_active:
        return filter_by_radius(
            properties,
            geo_lat,
            geo_lng,
            geo_radius,
        )
    return properties
