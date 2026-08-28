"""
Repository for organization billing issuer profiles.
"""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


def _normalize_tax_id(tax_id):
    from modules.billing_issuer_validation import normalize_cuit

    return normalize_cuit(tax_id)


def _sync_default_issuer_setting(
    cursor,
    organization_id,
    profile_id,
):
    cursor.execute(
        """
        UPDATE organization_settings
        SET default_issuer_profile_id = ?
        WHERE organization_id = ?
        """,
        (profile_id, organization_id),
    )


def _clear_default_issuer_setting_if_match(
    cursor,
    organization_id,
    profile_id,
):
    cursor.execute(
        """
        UPDATE organization_settings
        SET default_issuer_profile_id = NULL
        WHERE organization_id = ?
            AND default_issuer_profile_id = ?
        """,
        (organization_id, profile_id),
    )


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


PROFILE_SELECT = """
    SELECT
        id,
        organization_id,
        issuer_type,
        display_name,
        legal_name,
        tax_id,
        tax_condition,
        fiscal_address,
        email,
        is_default,
        is_active,
        point_of_sale,
        created_at,
        updated_at,
        deactivated_at,
        arca_connection_status,
        arca_environment,
        arca_point_of_sale,
        arca_voucher_types,
        arca_last_validated_at,
        arca_certificate_ref,
        arca_provider,
        arca_metadata
    FROM billing_issuer_profiles
"""


def build_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "issuer_type": row[2] or "",
        "display_name": row[3] or "",
        "legal_name": row[4] or "",
        "tax_id": row[5] or "",
        "tax_condition": row[6] or "",
        "fiscal_address": row[7] or "",
        "email": row[8] or "",
        "is_default": bool(row[9]),
        "is_active": bool(row[10]) if row[10] is not None else True,
        "point_of_sale": row[11],
        "created_at": row[12],
        "updated_at": row[13],
        "deactivated_at": row[14],
        "arca_connection_status": (
            row[15] if len(row) > 15 and row[15] else "not_configured"
        ),
        "arca_environment": row[16] if len(row) > 16 else None,
        "arca_point_of_sale": row[17] if len(row) > 17 else None,
        "arca_voucher_types": row[18] if len(row) > 18 else None,
        "arca_last_validated_at": (
            row[19] if len(row) > 19 else None
        ),
        "arca_certificate_ref": (
            row[20] if len(row) > 20 else None
        ),
        "arca_provider": (
            row[21] if len(row) > 21 and row[21] else "arca"
        ),
        "arca_metadata": row[22] if len(row) > 22 else None,
    }


def list_profiles(organization_id, *, active_only=True):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        sql = PROFILE_SELECT + """
            WHERE organization_id = ?
        """
        params = [organization_id]
        if active_only:
            sql += " AND is_active = 1"
        sql += """
            ORDER BY is_default DESC, display_name ASC, id ASC
        """
        cursor.execute(sql, params)
        return [
            build_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def get_profile(organization_id, profile_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            PROFILE_SELECT
            + """
            WHERE organization_id = ?
                AND id = ?
            """,
            (organization_id, profile_id),
        )
        return build_dict(cursor.fetchone())
    finally:
        connection.close()


def upsert_profile(
    organization_id,
    *,
    issuer_type,
    display_name,
    legal_name,
    tax_id,
    profile_id=None,
    tax_condition=None,
    fiscal_address=None,
    email=None,
    point_of_sale=None,
    is_default=False,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    issuer_type = (issuer_type or "").strip()
    if issuer_type not in (
        "organization",
        "broker",
        "other",
    ):
        raise ValueError(
            "issuer_type must be organization, broker, or other"
        )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        existing = None
        if profile_id is not None:
            cursor.execute(
                PROFILE_SELECT
                + """
                WHERE organization_id = ?
                    AND id = ?
                """,
                (organization_id, profile_id),
            )
            existing = cursor.fetchone()

        if existing is None:
            new_id = execute_insert(
                cursor,
                """
                INSERT INTO billing_issuer_profiles (
                    organization_id,
                    issuer_type,
                    display_name,
                    legal_name,
                    tax_id,
                    tax_condition,
                    fiscal_address,
                    email,
                    is_default,
                    is_active,
                    point_of_sale,
                    created_at,
                    updated_at,
                    deactivated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, NULL
                )
                """,
                (
                    organization_id,
                    issuer_type,
                    (display_name or "").strip(),
                    (legal_name or "").strip(),
                    _normalize_tax_id(tax_id),
                    (tax_condition or "").strip() or None,
                    (fiscal_address or "").strip() or None,
                    (email or "").strip() or None,
                    1 if is_default else 0,
                    point_of_sale,
                    now,
                    now,
                ),
            )
            profile_id = new_id
        else:
            cursor.execute(
                """
                UPDATE billing_issuer_profiles
                SET
                    issuer_type = ?,
                    display_name = ?,
                    legal_name = ?,
                    tax_id = ?,
                    tax_condition = ?,
                    fiscal_address = ?,
                    email = ?,
                    point_of_sale = ?,
                    updated_at = ?
                WHERE id = ?
                    AND organization_id = ?
                """,
                (
                    issuer_type,
                    (display_name or "").strip(),
                    (legal_name or "").strip(),
                    _normalize_tax_id(tax_id),
                    (tax_condition or "").strip() or None,
                    (fiscal_address or "").strip() or None,
                    (email or "").strip() or None,
                    point_of_sale,
                    now,
                    profile_id,
                    organization_id,
                ),
            )

        if is_default:
            cursor.execute(
                """
                UPDATE billing_issuer_profiles
                SET is_default = 0,
                    updated_at = ?
                WHERE organization_id = ?
                    AND id <> ?
                """,
                (now, organization_id, profile_id),
            )
            cursor.execute(
                """
                UPDATE billing_issuer_profiles
                SET is_default = 1,
                    updated_at = ?
                WHERE organization_id = ?
                    AND id = ?
                """,
                (now, organization_id, profile_id),
            )
            _sync_default_issuer_setting(
                cursor,
                organization_id,
                profile_id,
            )

        connection.commit()
        return get_profile(organization_id, profile_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_default(organization_id, profile_id):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT id
            FROM billing_issuer_profiles
            WHERE organization_id = ?
                AND id = ?
                AND is_active = 1
            """,
            (organization_id, profile_id),
        )
        if cursor.fetchone() is None:
            raise ValueError(
                "Issuer profile not found or inactive"
            )

        cursor.execute(
            """
            UPDATE billing_issuer_profiles
            SET is_default = 0,
                updated_at = ?
            WHERE organization_id = ?
            """,
            (now, organization_id),
        )
        cursor.execute(
            """
            UPDATE billing_issuer_profiles
            SET is_default = 1,
                updated_at = ?
            WHERE organization_id = ?
                AND id = ?
            """,
            (now, organization_id, profile_id),
        )
        _sync_default_issuer_setting(
            cursor,
            organization_id,
            profile_id,
        )
        connection.commit()
        return get_profile(organization_id, profile_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def deactivate(organization_id, profile_id):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE billing_issuer_profiles
            SET
                is_active = 0,
                is_default = 0,
                deactivated_at = ?,
                updated_at = ?
            WHERE organization_id = ?
                AND id = ?
            """,
            (now, now, organization_id, profile_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Issuer profile not found")

        _clear_default_issuer_setting_if_match(
            cursor,
            organization_id,
            profile_id,
        )

        connection.commit()
        return get_profile(organization_id, profile_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
