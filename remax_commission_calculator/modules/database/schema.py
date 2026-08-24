import os
import shutil
from datetime import datetime

from .connection import (
    get_connection,
    get_database_path
)


DEFAULT_ORGANIZATION_NAME = "Inmobiliaria Principal"
DEFAULT_ORGANIZATION_ID = 1


class MigrationError(Exception):
    pass


def _column_exists(cursor, table_name, column_name):
    cursor.execute(
        f"PRAGMA table_info({table_name})"
    )

    columns = [
        row[1]
        for row in cursor.fetchall()
    ]

    return column_name in columns


def _table_exists(cursor, table_name):
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
            AND name = ?
        """,
        (
            table_name,
        )
    )

    return cursor.fetchone() is not None


def _count_rows(cursor, table_name):
    if not _table_exists(cursor, table_name):
        return 0

    cursor.execute(
        f"SELECT COUNT(*) FROM {table_name}"
    )

    return cursor.fetchone()[0]


def _backup_database():
    database_path = get_database_path()

    if not os.path.exists(database_path):
        return None

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )
    backup_name = (
        f"{database_path}.bak.{stamp}"
    )
    shutil.copy2(
        database_path,
        backup_name
    )

    return backup_name


def _users_needs_rebuild(cursor):
    if not _table_exists(cursor, "users"):
        return False

    cursor.execute(
        "PRAGMA index_list(users)"
    )
    indexes = cursor.fetchall()

    for index in indexes:
        index_name = index[1]
        is_unique = index[2]

        if not is_unique:
            continue

        cursor.execute(
            f"PRAGMA index_info({index_name})"
        )
        columns = [
            row[2]
            for row in cursor.fetchall()
        ]

        if columns == [
            "organization_id",
            "username"
        ]:
            return False

    return True


def _rebuild_users_table(cursor):
    cursor.execute(
        """
        CREATE TABLE users_migrated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            agent_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            organization_id INTEGER NOT NULL,

            UNIQUE (organization_id, username),

            FOREIGN KEY (agent_id)
                REFERENCES agents(id)
                ON DELETE SET NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT
        )
        """
    )

    cursor.execute(
        """
        INSERT INTO users_migrated (
            id,
            username,
            password_hash,
            role,
            agent_id,
            is_active,
            organization_id
        )
        SELECT
            id,
            username,
            password_hash,
            role,
            agent_id,
            is_active,
            organization_id
        FROM users
        """
    )

    cursor.execute("DROP TABLE users")
    cursor.execute(
        "ALTER TABLE users_migrated RENAME TO users"
    )


def _validate_migration(cursor, before_counts):
    after_counts = {
        "agents": _count_rows(cursor, "agents"),
        "properties": _count_rows(
            cursor,
            "properties"
        ),
        "operations": _count_rows(
            cursor,
            "operations"
        ),
        "users": _count_rows(cursor, "users")
    }

    for table_name, before in before_counts.items():
        after = after_counts[table_name]

        if after != before:
            raise MigrationError(
                f"Count mismatch for {table_name}: "
                f"before={before}, after={after}"
            )

    for table_name in (
        "agents",
        "properties",
        "operations",
        "users"
    ):
        if not _table_exists(cursor, table_name):
            continue

        if not _column_exists(
            cursor,
            table_name,
            "organization_id"
        ):
            raise MigrationError(
                f"{table_name} missing organization_id"
            )

        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM {table_name}
            WHERE organization_id IS NULL
            """
        )

        null_count = cursor.fetchone()[0]

        if null_count > 0:
            raise MigrationError(
                f"{table_name} has {null_count} "
                "rows without organization_id"
            )

    if _table_exists(cursor, "users"):
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM users
            LEFT JOIN agents
                ON users.agent_id = agents.id
            WHERE users.agent_id IS NOT NULL
                AND (
                    agents.id IS NULL
                    OR agents.organization_id
                        != users.organization_id
                )
            """
        )

        if cursor.fetchone()[0] > 0:
            raise MigrationError(
                "users.agent_id crosses organizations"
            )

    if _table_exists(cursor, "operations"):
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM operations
            JOIN agents
                ON operations.agent_id = agents.id
            JOIN properties
                ON operations.property_id
                    = properties.id
            WHERE operations.organization_id
                    != agents.organization_id
                OR operations.organization_id
                    != properties.organization_id
                OR agents.organization_id
                    != properties.organization_id
            """
        )

        if cursor.fetchone()[0] > 0:
            raise MigrationError(
                "operations have cross-tenant relations"
            )

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM organizations
        WHERE id = ?
        """,
        (
            DEFAULT_ORGANIZATION_ID,
        )
    )

    if cursor.fetchone()[0] == 0:
        raise MigrationError(
            "Default organization was not created"
        )


def _migrate_workflow_and_ownership(cursor):
    if (
        _table_exists(cursor, "properties")
        and not _column_exists(
            cursor,
            "properties",
            "agent_id"
        )
    ):
        cursor.execute(
            """
            ALTER TABLE properties
            ADD COLUMN agent_id INTEGER
            """
        )

    if _table_exists(cursor, "properties"):
        cursor.execute(
            """
            UPDATE properties
            SET agent_id = (
                SELECT operations.agent_id
                FROM operations
                WHERE operations.property_id
                    = properties.id
                    AND operations.organization_id
                        = properties.organization_id
                ORDER BY operations.id DESC
                LIMIT 1
            )
            WHERE agent_id IS NULL
            """
        )

    if _table_exists(cursor, "operations"):
        if not _column_exists(
            cursor,
            "operations",
            "status"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN status TEXT
                NOT NULL DEFAULT 'approved'
                """
            )

        if not _column_exists(
            cursor,
            "operations",
            "rejection_reason"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN rejection_reason TEXT
                """
            )

        if not _column_exists(
            cursor,
            "operations",
            "created_by_user_id"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN created_by_user_id INTEGER
                """
            )

        if not _column_exists(
            cursor,
            "operations",
            "reviewed_by_user_id"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN reviewed_by_user_id INTEGER
                """
            )

        if not _column_exists(
            cursor,
            "operations",
            "reviewed_at"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN reviewed_at TEXT
                """
            )

        cursor.execute(
            """
            UPDATE operations
            SET status = 'approved'
            WHERE status IS NULL
                OR status = ''
            """
        )

    if _table_exists(cursor, "users"):
        cursor.execute(
            """
            UPDATE users
            SET role = 'guest'
            WHERE role = 'reader'
            """
        )


def _migrate_access_and_registration(cursor):
    if _table_exists(cursor, "users"):
        if not _column_exists(cursor, "users", "email"):
            cursor.execute(
                """
                ALTER TABLE users
                ADD COLUMN email TEXT
                """
            )

        if not _column_exists(cursor, "users", "first_name"):
            cursor.execute(
                """
                ALTER TABLE users
                ADD COLUMN first_name TEXT
                """
            )

        if not _column_exists(cursor, "users", "last_name"):
            cursor.execute(
                """
                ALTER TABLE users
                ADD COLUMN last_name TEXT
                """
            )

        if not _column_exists(cursor, "users", "phone"):
            cursor.execute(
                """
                ALTER TABLE users
                ADD COLUMN phone TEXT
                """
            )

        if not _column_exists(cursor, "users", "account_status"):
            cursor.execute(
                """
                ALTER TABLE users
                ADD COLUMN account_status TEXT
                NOT NULL DEFAULT 'active'
                """
            )

        cursor.execute(
            """
            UPDATE users
            SET email = username
            WHERE email IS NULL
                OR email = ''
            """
        )

        cursor.execute(
            """
            UPDATE users
            SET account_status = 'active'
            WHERE account_status IS NULL
                OR account_status = ''
            """
        )

        cursor.execute(
            """
            UPDATE users
            SET
                is_active = 0,
                account_status = 'legacy_guest'
            WHERE role = 'guest'
                AND account_status != 'legacy_guest'
            """
        )

    if _table_exists(cursor, "organization_settings"):
        if not _column_exists(
            cursor,
            "organization_settings",
            "registration_code_hash"
        ):
            cursor.execute(
                """
                ALTER TABLE organization_settings
                ADD COLUMN registration_code_hash TEXT
                """
            )

        if not _column_exists(
            cursor,
            "organization_settings",
            "registration_enabled"
        ):
            cursor.execute(
                """
                ALTER TABLE organization_settings
                ADD COLUMN registration_enabled INTEGER
                NOT NULL DEFAULT 1
                """
            )

        if not _column_exists(
            cursor,
            "organization_settings",
            "registration_code_rotated_at"
        ):
            cursor.execute(
                """
                ALTER TABLE organization_settings
                ADD COLUMN registration_code_rotated_at TEXT
                """
            )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS registration_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            rejection_reason TEXT,
            reviewed_by_user_id INTEGER,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            email_verified_at TEXT,
            approved_user_id INTEGER,
            approved_agent_id INTEGER,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verification_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_request_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            invalidated_at TEXT,
            last_sent_at TEXT,

            FOREIGN KEY (registration_request_id)
                REFERENCES registration_requests(id)
                ON DELETE CASCADE
        )
        """
    )

    if _table_exists(cursor, "email_verification_tokens"):
        token_columns = (
            ("attempt_count", """
                ALTER TABLE email_verification_tokens
                ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0
            """),
            ("invalidated_at", """
                ALTER TABLE email_verification_tokens
                ADD COLUMN invalidated_at TEXT
            """),
            ("last_sent_at", """
                ALTER TABLE email_verification_tokens
                ADD COLUMN last_sent_at TEXT
            """)
        )

        for column_name, ddl in token_columns:
            if not _column_exists(
                cursor,
                "email_verification_tokens",
                column_name
            ):
                cursor.execute(ddl)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS organization_guest_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            label TEXT,
            created_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked_at TEXT,
            last_used_at TEXT,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE CASCADE
        )
        """
    )


def _migrate_property_approvals_and_notifications(cursor):
    if _table_exists(cursor, "properties"):
        property_columns = (
            ("status", """
                ALTER TABLE properties
                ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'
            """),
            ("rejection_reason", """
                ALTER TABLE properties
                ADD COLUMN rejection_reason TEXT
            """),
            ("reviewed_by_user_id", """
                ALTER TABLE properties
                ADD COLUMN reviewed_by_user_id INTEGER
            """),
            ("reviewed_at", """
                ALTER TABLE properties
                ADD COLUMN reviewed_at TEXT
            """),
            ("created_by_user_id", """
                ALTER TABLE properties
                ADD COLUMN created_by_user_id INTEGER
            """),
            ("submitted_at", """
                ALTER TABLE properties
                ADD COLUMN submitted_at TEXT
            """)
        )

        for column_name, ddl in property_columns:
            if not _column_exists(
                cursor,
                "properties",
                column_name
            ):
                cursor.execute(ddl)

        cursor.execute(
            """
            UPDATE properties
            SET status = 'approved'
            WHERE status IS NULL
                OR status = ''
            """
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS property_change_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            property_id INTEGER NOT NULL,
            requested_by_user_id INTEGER NOT NULL,
            proposed_address TEXT NOT NULL,
            proposed_jurisdiction TEXT NOT NULL,
            proposed_agent_id INTEGER,
            status TEXT NOT NULL,
            rejection_reason TEXT,
            reviewed_by_user_id INTEGER,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,

            FOREIGN KEY (property_id)
                REFERENCES properties(id)
                ON DELETE RESTRICT
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            payload_json TEXT,
            is_read INTEGER NOT NULL DEFAULT 0,
            actor_user_id INTEGER,
            created_at TEXT NOT NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,

            FOREIGN KEY (user_id)
                REFERENCES users(id)
                ON DELETE CASCADE
        )
        """
    )


def _migrate_vat_documents(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_vat_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            operation_id INTEGER NOT NULL,
            doc_type TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            uploaded_by_user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (operation_id)
                REFERENCES operations(id)
                ON DELETE CASCADE,
            FOREIGN KEY (uploaded_by_user_id)
                REFERENCES users(id)
                ON DELETE RESTRICT,

            CHECK (
                doc_type IN (
                    'martillero_client',
                    'agent_client'
                )
            ),
            UNIQUE (
                organization_id,
                operation_id,
                doc_type
            )
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_vat_docs_org_operation
        ON operation_vat_documents (
            organization_id,
            operation_id
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_vat_docs_operation
        ON operation_vat_documents (operation_id)
        """
    )


def migrate_schema():
    backup_path = _backup_database()

    connection = get_connection()
    cursor = connection.cursor()

    before_counts = {
        "agents": _count_rows(cursor, "agents"),
        "properties": _count_rows(
            cursor,
            "properties"
        ),
        "operations": _count_rows(
            cursor,
            "operations"
        ),
        "users": _count_rows(cursor, "users")
    }

    try:
        cursor.execute("BEGIN")

        if not _column_exists(
            cursor,
            "operations",
            "currency"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN currency TEXT
                NOT NULL DEFAULT 'USD'
                """
            )

        if not _column_exists(
            cursor,
            "operations",
            "original_amount"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN original_amount REAL
                """
            )

        if not _column_exists(
            cursor,
            "operations",
            "exchange_rate"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN exchange_rate REAL
                NOT NULL DEFAULT 1
                """
            )

        cursor.execute(
            """
            UPDATE operations
            SET original_amount = sale_price
            WHERE original_amount IS NULL
            """
        )

        cursor.execute(
            """
            UPDATE operations
            SET currency = 'USD'
            WHERE currency IS NULL
                OR currency = ''
            """
        )

        cursor.execute(
            """
            UPDATE operations
            SET exchange_rate = 1
            WHERE exchange_rate IS NULL
                OR exchange_rate <= 0
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM organizations
            """
        )

        if cursor.fetchone()[0] == 0:
            cursor.execute(
                """
                INSERT INTO organizations (
                    id,
                    name,
                    is_active
                )
                VALUES (?, ?, 1)
                """,
                (
                    DEFAULT_ORGANIZATION_ID,
                    DEFAULT_ORGANIZATION_NAME
                )
            )

        for table_name in (
            "agents",
            "properties",
            "operations",
            "users"
        ):
            if not _table_exists(cursor, table_name):
                continue

            if not _column_exists(
                cursor,
                table_name,
                "organization_id"
            ):
                cursor.execute(
                    f"""
                    ALTER TABLE {table_name}
                    ADD COLUMN organization_id INTEGER
                    """
                )

        if _table_exists(cursor, "users"):
            if not _column_exists(
                cursor,
                "users",
                "agent_id"
            ):
                cursor.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN agent_id INTEGER
                    """
                )

            if not _column_exists(
                cursor,
                "users",
                "is_active"
            ):
                cursor.execute(
                    """
                    ALTER TABLE users
                    ADD COLUMN is_active INTEGER
                    NOT NULL DEFAULT 1
                    """
                )

        for table_name in (
            "agents",
            "properties",
            "operations",
            "users"
        ):
            if not _table_exists(cursor, table_name):
                continue

            cursor.execute(
                f"""
                UPDATE {table_name}
                SET organization_id = ?
                WHERE organization_id IS NULL
                """,
                (
                    DEFAULT_ORGANIZATION_ID,
                )
            )

        if (
            _table_exists(cursor, "users")
            and _users_needs_rebuild(cursor)
        ):
            _rebuild_users_table(cursor)

        from .organization_settings_repository import (
            backfill_organization_settings
        )

        backfill_organization_settings(cursor)
        _migrate_workflow_and_ownership(cursor)
        _migrate_access_and_registration(cursor)
        _migrate_property_approvals_and_notifications(cursor)
        _migrate_vat_documents(cursor)

        _validate_migration(
            cursor,
            before_counts
        )

        connection.commit()

    except Exception as error:
        connection.rollback()
        connection.close()

        message = (
            "Organization migration failed "
            f"and was rolled back: {error}"
        )

        if backup_path is not None:
            message += (
                f" Backup available at: {backup_path}"
            )

        raise MigrationError(message) from error

    connection.close()

    return backup_path


def create_tables():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            organization_id INTEGER NOT NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            organization_id INTEGER NOT NULL,
            agent_id INTEGER,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,

            FOREIGN KEY (agent_id)
                REFERENCES agents(id)
                ON DELETE RESTRICT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            operation_date TEXT NOT NULL,

            agent_id INTEGER NOT NULL,
            property_id INTEGER NOT NULL,
            organization_id INTEGER NOT NULL,

            was_invoiced TEXT NOT NULL,

            vat_amount REAL NOT NULL,

            sale_price REAL NOT NULL,
            commission_rate REAL NOT NULL,

            total_commission REAL NOT NULL,
            commission_after_abao REAL NOT NULL,

            abao REAL NOT NULL,
            martillero REAL NOT NULL,

            agent_payment REAL NOT NULL,
            office_payment REAL NOT NULL,
            office_total REAL NOT NULL,

            currency TEXT NOT NULL DEFAULT 'USD',
            original_amount REAL,
            exchange_rate REAL NOT NULL DEFAULT 1,

            status TEXT NOT NULL DEFAULT 'approved',
            rejection_reason TEXT,
            created_by_user_id INTEGER,
            reviewed_by_user_id INTEGER,
            reviewed_at TEXT,

            FOREIGN KEY (agent_id)
                REFERENCES agents(id)
                ON DELETE RESTRICT,

            FOREIGN KEY (property_id)
                REFERENCES properties(id)
                ON DELETE RESTRICT,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            agent_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            organization_id INTEGER NOT NULL,

            UNIQUE (organization_id, username),

            FOREIGN KEY (agent_id)
                REFERENCES agents(id)
                ON DELETE SET NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registration_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            rejection_reason TEXT,
            reviewed_by_user_id INTEGER,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            email_verified_at TEXT,
            approved_user_id INTEGER,
            approved_agent_id INTEGER,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_verification_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_request_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            invalidated_at TEXT,
            last_sent_at TEXT,

            FOREIGN KEY (registration_request_id)
                REFERENCES registration_requests(id)
                ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organization_guest_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            label TEXT,
            created_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked_at TEXT,
            last_used_at TEXT,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE CASCADE
        )
    """)

    connection.commit()
    connection.close()

    migrate_schema()
