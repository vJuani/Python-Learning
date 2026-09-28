from .connection import (
    execute_insert,
    get_connection,
)
from .organization_settings_repository import (
    ensure_organization_settings
)
from modules.config import (
    BACKEND_SQLITE,
    get_database_backend,
)


class OrganizationProvisioningError(Exception):
    pass


def get_organizations():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            name,
            is_active
        FROM organizations
        ORDER BY id
        """
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        {
            "id": row[0],
            "name": row[1],
            "is_active": bool(row[2])
        }
        for row in rows
    ]


def get_organization_by_id(organization_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            name,
            is_active
        FROM organizations
        WHERE id = ?
        """,
        (
            organization_id,
        )
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "is_active": bool(row[2])
    }


def provision_organization(
    name,
    display_name,
    default_language,
    default_currency,
    timezone,
    admin_username,
    admin_password_hash,
    admin_role,
    registration_code_hash=None,
    is_active=True,
    *,
    admin_email=None,
    first_name="",
    last_name="",
    country="",
    region="",
    city="",
    accent_color=None,
    marketing_phone="",
    marketing_email="",
    marketing_website="",
    legal_office_name="",
    legal_broker_name="",
    legal_broker_license="",
    legal_footer_line="",
):
    name = name.strip()
    display_name = display_name.strip()
    admin_username = admin_username.strip()
    stored_email = (
        admin_username.lower()
        if admin_email is None
        else admin_email.strip().lower()
    )

    if len(admin_username) > 64 or len(stored_email) > 64:
        raise OrganizationProvisioningError(
            "Admin username exceeds 64 characters."
        )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        # SQLite: explicit BEGIN. PostgreSQL (psycopg) already
        # opens a transaction on the first statement.
        if get_database_backend() == BACKEND_SQLITE:
            cursor.execute("BEGIN")

        organization_id = execute_insert(
            cursor,
            """
            INSERT INTO organizations (
                name,
                is_active
            )
            VALUES (?, ?)
            """,
            (
                name,
                1 if is_active else 0
            )
        )

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
                registration_code_hash,
                registration_enabled,
                marketing_brand_name,
                marketing_phone,
                marketing_email,
                marketing_website,
                legal_office_name,
                legal_broker_name,
                legal_broker_license,
                legal_footer_line,
                country,
                region,
                city
            )
            VALUES (
                ?, ?, ?, ?, ?, NULL, ?, ?, 1,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                display_name,
                default_language,
                default_currency,
                timezone,
                accent_color,
                registration_code_hash,
                display_name,
                marketing_phone.strip(),
                marketing_email.strip(),
                marketing_website.strip(),
                legal_office_name.strip(),
                legal_broker_name.strip(),
                legal_broker_license.strip(),
                legal_footer_line.strip(),
                country.strip(),
                region.strip(),
                city.strip(),
            )
        )

        admin_user_id = execute_insert(
            cursor,
            """
            INSERT INTO users (
                username,
                password_hash,
                role,
                agent_id,
                is_active,
                organization_id,
                email,
                account_status,
                first_name,
                last_name
            )
            VALUES (?, ?, ?, NULL, 1, ?, ?, 'active', ?, ?)
            """,
            (
                admin_username,
                admin_password_hash,
                admin_role,
                organization_id,
                stored_email,
                first_name.strip(),
                last_name.strip(),
            )
        )

        from modules.database.treasury_accounts_repository import (
            ensure_legacy_default_accounts,
        )

        ensure_legacy_default_accounts(
            cursor,
            organization_id,
            created_by_user_id=admin_user_id,
        )
        connection.commit()

    except Exception as error:
        connection.rollback()
        connection.close()

        raise OrganizationProvisioningError(
            "Organization provisioning failed and was "
            f"rolled back: {error}"
        ) from error

    connection.close()

    return {
        "organization_id": organization_id,
        "admin_user_id": admin_user_id
    }


def add_organization(name, is_active=True):
    connection = get_connection()
    cursor = connection.cursor()

    organization_id = execute_insert(
        cursor,
        """
        INSERT INTO organizations (
            name,
            is_active
        )
        VALUES (?, ?)
        """,
        (
            name.strip(),
            1 if is_active else 0
        )
    )

    connection.commit()
    connection.close()

    ensure_organization_settings(
        organization_id,
        name
    )

    from .treasury_accounts_repository import (
        ensure_legacy_default_accounts,
    )

    bootstrap = get_connection()
    bootstrap_cursor = bootstrap.cursor()
    try:
        ensure_legacy_default_accounts(
            bootstrap_cursor,
            organization_id,
        )
        bootstrap.commit()
    finally:
        bootstrap.close()

    return organization_id
