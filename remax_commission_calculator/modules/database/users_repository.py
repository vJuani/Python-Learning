from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import (
    TenantError,
    assert_agent_in_organization,
    require_organization_id
)


def build_user_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "username": row[1],
        "password_hash": row[2],
        "role": row[3],
        "agent_id": row[4],
        "agent_name": row[5],
        "is_active": bool(row[6]),
        "organization_id": row[7],
        "organization_name": row[8],
        "email": row[9],
        "first_name": row[10],
        "last_name": row[11],
        "phone": row[12],
        "account_status": row[13] or "active"
    }


USERS_BASE_QUERY = """
    SELECT
        users.id,
        users.username,
        users.password_hash,
        users.role,
        users.agent_id,
        agents.name,
        users.is_active,
        users.organization_id,
        organizations.name,
        users.email,
        users.first_name,
        users.last_name,
        users.phone,
        users.account_status
    FROM users
    JOIN organizations
        ON users.organization_id = organizations.id
    LEFT JOIN agents
        ON users.agent_id = agents.id
        AND agents.organization_id
            = users.organization_id
"""


def get_users(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        USERS_BASE_QUERY
        + """
        WHERE users.organization_id = ?
            AND users.role != 'guest'
        ORDER BY users.username
        """,
        (
            organization_id,
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        build_user_dict(row)
        for row in rows
    ]


def get_user_by_id(user_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        USERS_BASE_QUERY
        + """
        WHERE users.id = ?
        """,
        (
            user_id,
        )
    )

    row = cursor.fetchone()
    connection.close()

    return build_user_dict(row)


def get_user_by_username(
    username,
    organization_id=None
):
    connection = get_connection()
    cursor = connection.cursor()

    if organization_id is None:
        cursor.execute(
            USERS_BASE_QUERY
            + """
            WHERE LOWER(users.username) = LOWER(?)
                OR LOWER(COALESCE(users.email, '')) = LOWER(?)
            ORDER BY users.id
            """,
            (
                username.strip(),
                username.strip()
            )
        )
        rows = cursor.fetchall()
        connection.close()

        if len(rows) == 0:
            return None

        if len(rows) == 1:
            return build_user_dict(rows[0])

        return [
            build_user_dict(row)
            for row in rows
        ]

    organization_id = require_organization_id(
        organization_id
    )

    cursor.execute(
        USERS_BASE_QUERY
        + """
        WHERE users.organization_id = ?
            AND (
                LOWER(users.username) = LOWER(?)
                OR LOWER(COALESCE(users.email, '')) = LOWER(?)
            )
        """,
        (
            organization_id,
            username.strip(),
            username.strip()
        )
    )

    row = cursor.fetchone()
    connection.close()

    return build_user_dict(row)


def get_user_by_agent_id(agent_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        USERS_BASE_QUERY
        + """
        WHERE users.organization_id = ?
            AND users.agent_id = ?
            AND users.is_active = 1
        ORDER BY users.id
        LIMIT 1
        """,
        (
            organization_id,
            agent_id
        )
    )

    row = cursor.fetchone()
    connection.close()

    return build_user_dict(row)


def get_user_by_email(email, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        USERS_BASE_QUERY
        + """
        WHERE users.organization_id = ?
            AND LOWER(COALESCE(users.email, users.username)) = LOWER(?)
        """,
        (
            organization_id,
            email.strip()
        )
    )

    row = cursor.fetchone()
    connection.close()

    return build_user_dict(row)


def add_user(
    username,
    password_hash,
    role,
    organization_id,
    agent_id=None,
    is_active=True,
    email=None,
    first_name=None,
    last_name=None,
    phone=None,
    account_status="active"
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

    email_value = (
        email.strip().lower()
        if email
        else username.strip().lower()
    )

    user_id = execute_insert(
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
            first_name,
            last_name,
            phone,
            account_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            username.strip(),
            password_hash,
            role,
            agent_id,
            1 if is_active else 0,
            organization_id,
            email_value,
            first_name,
            last_name,
            phone,
            account_status
        )
    )

    connection.commit()
    connection.close()

    return user_id


def update_user(
    user_id,
    username,
    role,
    organization_id,
    agent_id=None,
    is_active=True,
    password_hash=None,
    email=None,
    first_name=None,
    last_name=None,
    phone=None,
    account_status=None
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

    email_value = (
        email.strip().lower()
        if email
        else username.strip().lower()
    )
    status_value = account_status or "active"

    if password_hash is None:
        cursor.execute(
            """
            UPDATE users
            SET
                username = ?,
                role = ?,
                agent_id = ?,
                is_active = ?,
                email = ?,
                first_name = ?,
                last_name = ?,
                phone = ?,
                account_status = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                username.strip(),
                role,
                agent_id,
                1 if is_active else 0,
                email_value,
                first_name,
                last_name,
                phone,
                status_value,
                user_id,
                organization_id
            )
        )
    else:
        cursor.execute(
            """
            UPDATE users
            SET
                username = ?,
                password_hash = ?,
                role = ?,
                agent_id = ?,
                is_active = ?,
                email = ?,
                first_name = ?,
                last_name = ?,
                phone = ?,
                account_status = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                username.strip(),
                password_hash,
                role,
                agent_id,
                1 if is_active else 0,
                email_value,
                first_name,
                last_name,
                phone,
                status_value,
                user_id,
                organization_id
            )
        )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "User was not found in this organization."
        )

    connection.commit()
    connection.close()


def update_user_password(user_id, password_hash):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE users
        SET password_hash = ?
        WHERE id = ?
        """,
        (
            password_hash,
            user_id,
        ),
    )

    connection.commit()
    connection.close()


def delete_user(user_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM users
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            user_id,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "User was not found in this organization."
        )

    connection.commit()
    connection.close()


def count_users(organization_id=None):
    connection = get_connection()
    cursor = connection.cursor()

    if organization_id is None:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM users
            """
        )
    else:
        organization_id = require_organization_id(
            organization_id
        )
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE organization_id = ?
            """,
            (
                organization_id,
            )
        )

    count = cursor.fetchone()[0]
    connection.close()

    return count


def count_users_by_role(organization_id, role):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM users
        WHERE organization_id = ?
            AND role = ?
        """,
        (
            organization_id,
            role
        )
    )

    count = cursor.fetchone()[0]
    connection.close()

    return count
