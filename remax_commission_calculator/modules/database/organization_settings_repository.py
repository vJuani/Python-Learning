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
        ),
        "legal_name": (
            row[10] if len(row) > 10 and row[10] else ""
        ),
        "tax_id": (
            row[11] if len(row) > 11 and row[11] else ""
        ),
        "tax_condition": (
            row[12] if len(row) > 12 and row[12] else ""
        ),
        "fiscal_address": (
            row[13] if len(row) > 13 and row[13] else ""
        ),
        "trade_name": (
            row[14] if len(row) > 14 and row[14] else ""
        ),
        "billing_email": (
            row[15] if len(row) > 15 and row[15] else ""
        ),
        "default_payment_condition": (
            row[16]
            if len(row) > 16 and row[16]
            else "cuenta_corriente"
        ),
        "default_buyer_commission_percent": (
            float(row[17])
            if len(row) > 17 and row[17] is not None
            else 3.0
        ),
        "default_seller_commission_percent": (
            float(row[18])
            if len(row) > 18 and row[18] is not None
            else 3.0
        ),
        "default_invoice_description": (
            row[19]
            if len(row) > 19 and row[19]
            else "Asesoramiento Integral de Gestión"
        ),
        "default_invoice_service_type": (
            row[20]
            if len(row) > 20 and row[20]
            else "services"
        ),
        "default_invoice_currency": (
            row[21]
            if len(row) > 21 and row[21]
            else "ARS"
        ),
        "agents_can_invoice": (
            bool(row[22])
            if len(row) > 22 and row[22] is not None
            else True
        ),
        "office_can_invoice": (
            bool(row[23])
            if len(row) > 23 and row[23] is not None
            else True
        ),
        "default_issuer_profile_id": (
            row[24] if len(row) > 24 else None
        ),
    }


SETTINGS_SELECT = """
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
            registration_code_rotated_at,
            legal_name,
            tax_id,
            tax_condition,
            fiscal_address,
            trade_name,
            billing_email,
            default_payment_condition,
            default_buyer_commission_percent,
            default_seller_commission_percent,
            default_invoice_description,
            default_invoice_service_type,
            default_invoice_currency,
            agents_can_invoice,
            office_can_invoice,
            default_issuer_profile_id
        FROM organization_settings
"""


def get_organization_settings(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        SETTINGS_SELECT
        + """
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


def update_organization_billing_fields(
    organization_id,
    *,
    legal_name=None,
    tax_id=None,
    tax_condition=None,
    fiscal_address=None,
    trade_name=None,
    billing_email=None,
    default_payment_condition=None,
    default_buyer_commission_percent=None,
    default_seller_commission_percent=None,
    default_invoice_description=None,
    default_invoice_service_type=None,
    default_invoice_currency=None,
    agents_can_invoice=None,
    office_can_invoice=None,
    default_issuer_profile_id=None,
):
    """
    Update organization fiscal / billing profile fields.
    Only non-None kwargs are written.
    """
    organization_id = require_organization_id(
        organization_id
    )

    clauses = []
    params = []

    if legal_name is not None:
        clauses.append("legal_name = ?")
        params.append(legal_name.strip())

    if tax_id is not None:
        clauses.append("tax_id = ?")
        params.append(tax_id.strip())

    if tax_condition is not None:
        clauses.append("tax_condition = ?")
        params.append(tax_condition.strip())

    if fiscal_address is not None:
        clauses.append("fiscal_address = ?")
        params.append(fiscal_address.strip())

    if trade_name is not None:
        clauses.append("trade_name = ?")
        params.append(trade_name.strip())

    if billing_email is not None:
        clauses.append("billing_email = ?")
        params.append(billing_email.strip())

    if default_payment_condition is not None:
        clauses.append("default_payment_condition = ?")
        params.append(default_payment_condition.strip())

    if default_buyer_commission_percent is not None:
        clauses.append(
            "default_buyer_commission_percent = ?"
        )
        params.append(default_buyer_commission_percent)

    if default_seller_commission_percent is not None:
        clauses.append(
            "default_seller_commission_percent = ?"
        )
        params.append(default_seller_commission_percent)

    if default_invoice_description is not None:
        clauses.append("default_invoice_description = ?")
        params.append(default_invoice_description.strip())

    if default_invoice_service_type is not None:
        clauses.append("default_invoice_service_type = ?")
        params.append(default_invoice_service_type.strip())

    if default_invoice_currency is not None:
        clauses.append("default_invoice_currency = ?")
        params.append(default_invoice_currency.strip())

    if agents_can_invoice is not None:
        clauses.append("agents_can_invoice = ?")
        params.append(1 if agents_can_invoice else 0)

    if office_can_invoice is not None:
        clauses.append("office_can_invoice = ?")
        params.append(1 if office_can_invoice else 0)

    if default_issuer_profile_id is not None:
        clauses.append("default_issuer_profile_id = ?")
        params.append(default_issuer_profile_id)

    if not clauses:
        return

    params.append(organization_id)
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        f"""
        UPDATE organization_settings
        SET {", ".join(clauses)}
        WHERE organization_id = ?
        """,
        params,
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
        SETTINGS_SELECT
        + """
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
