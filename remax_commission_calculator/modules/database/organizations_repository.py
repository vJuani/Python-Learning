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
    is_active=True
):
    name = name.strip()
    display_name = display_name.strip()
    admin_username = admin_username.strip()

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
                registration_enabled
            )
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 1)
            """,
            (
                organization_id,
                display_name,
                default_language,
                default_currency,
                timezone,
                registration_code_hash
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
                account_status
            )
            VALUES (?, ?, ?, NULL, 1, ?, ?, 'active')
            """,
            (
                admin_username,
                admin_password_hash,
                admin_role,
                organization_id,
                admin_username.lower()
            )
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

    return organization_id
