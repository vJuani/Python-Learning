from datetime import datetime

from .connection import get_connection
from .tenant import require_organization_id


DEFAULT_LANGUAGE = "es"
DEFAULT_CURRENCY = "USD"
DEFAULT_TIMEZONE = "America/Argentina/Buenos_Aires"
DEFAULT_ACCENT_COLOR = "#0f766e"


def build_settings_dict(row):
    if row is None:
        return None

    return {
        "organization_id": row[0],
        "display_name": row[1],
        "default_language": row[2],
        "default_currency": row[3],
        "timezone": row[4],
        "logo_path": row[5],
        "accent_color": row[6],
        "registration_code_hash": row[7] if len(row) > 7 else None,
        "registration_enabled": (
            bool(row[8]) if len(row) > 8 and row[8] is not None else True
        ),
        "registration_code_rotated_at": (
            row[9] if len(row) > 9 else None
        ),
        "has_registration_code": bool(
            row[7] if len(row) > 7 else None
        )
    }


def get_organization_settings(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            organization_id,
            display_name,
            default_language,
            default_currency,
            timezone,
            logo_path,
            accent_color,
            registration_code_hash,
            registration_enabled,
            registration_code_rotated_at
        FROM organization_settings
        WHERE organization_id = ?
        """,
        (
            organization_id,
        )
    )

    row = cursor.fetchone()
    connection.close()

    return build_settings_dict(row)


def ensure_organization_settings(
    organization_id,
    display_name
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT organization_id
        FROM organization_settings
        WHERE organization_id = ?
        """,
        (
            organization_id,
        )
    )

    if cursor.fetchone() is not None:
        connection.close()
        return

    cursor.execute(
        """
        INSERT INTO organization_settings (
            organization_id,
            display_name,
            default_language,
            default_currency,
            timezone,
            logo_path,
            accent_color,
            registration_enabled
        )
        VALUES (?, ?, ?, ?, ?, NULL, NULL, 1)
        """,
        (
            organization_id,
            display_name.strip(),
            DEFAULT_LANGUAGE,
            DEFAULT_CURRENCY,
            DEFAULT_TIMEZONE
        )
    )

    connection.commit()
    connection.close()


def update_organization_settings(
    organization_id,
    display_name,
    default_language,
    default_currency,
    timezone,
    logo_path,
    accent_color
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE organization_settings
        SET
            display_name = ?,
            default_language = ?,
            default_currency = ?,
            timezone = ?,
            logo_path = ?,
            accent_color = ?
        WHERE organization_id = ?
        """,
        (
            display_name.strip(),
            default_language,
            default_currency,
            timezone,
            logo_path,
            accent_color,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise ValueError(
            "Organization settings not found"
        )

    connection.commit()
    connection.close()


def set_registration_code(
    organization_id,
    registration_code_hash,
    enabled=True
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE organization_settings
        SET
            registration_code_hash = ?,
            registration_enabled = ?,
            registration_code_rotated_at = ?
        WHERE organization_id = ?
        """,
        (
            registration_code_hash,
            1 if enabled else 0,
            datetime.utcnow().replace(
                microsecond=0
            ).isoformat(),
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise ValueError(
            "Organization settings not found"
        )

    connection.commit()
    connection.close()


def set_registration_enabled(organization_id, enabled):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE organization_settings
        SET registration_enabled = ?
        WHERE organization_id = ?
        """,
        (
            1 if enabled else 0,
            organization_id
        )
    )

    connection.commit()
    connection.close()


def find_organization_by_registration_code_hash(code_hash):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            organization_id,
            display_name,
            default_language,
            default_currency,
            timezone,
            logo_path,
            accent_color,
            registration_code_hash,
            registration_enabled,
            registration_code_rotated_at
        FROM organization_settings
        WHERE registration_code_hash = ?
            AND registration_enabled = 1
        """,
        (
            code_hash,
        )
    )

    row = cursor.fetchone()
    connection.close()

    return build_settings_dict(row)


def backfill_organization_settings(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS organization_settings (
            organization_id INTEGER PRIMARY KEY,
            display_name TEXT NOT NULL,
            default_language TEXT NOT NULL DEFAULT 'es',
            default_currency TEXT NOT NULL DEFAULT 'USD',
            timezone TEXT NOT NULL
                DEFAULT 'America/Argentina/Buenos_Aires',
            logo_path TEXT,
            accent_color TEXT,
            registration_code_hash TEXT,
            registration_enabled INTEGER NOT NULL DEFAULT 1,
            registration_code_rotated_at TEXT,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        SELECT
            id,
            name
        FROM organizations
        ORDER BY id
        """
    )

    organizations = cursor.fetchall()

    for organization_id, organization_name in organizations:
        cursor.execute(
            """
            SELECT organization_id
            FROM organization_settings
            WHERE organization_id = ?
            """,
            (
                organization_id,
            )
        )

        if cursor.fetchone() is not None:
            continue

        cursor.execute(
            """
            INSERT INTO organization_settings (
                organization_id,
                display_name,
                default_language,
                default_currency,
                timezone,
                logo_path,
                accent_color,
                registration_enabled
            )
            VALUES (?, ?, ?, ?, ?, NULL, NULL, 1)
            """,
            (
                organization_id,
                organization_name,
                DEFAULT_LANGUAGE,
                DEFAULT_CURRENCY,
                DEFAULT_TIMEZONE
            )
        )
