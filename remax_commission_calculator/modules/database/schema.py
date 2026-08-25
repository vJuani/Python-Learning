import os
import shutil
from pathlib import Path
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


def _migrate_operation_documents(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_documents (
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
                    'agent_client',
                    'uif_form',
                    'uif_additional',
                    'transfer_receipt',
                    'reservation_deposit',
                    'deed_contract',
                    'other'
                )
            )
        )
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_operation_documents_structured_unique
        ON operation_documents (
            organization_id,
            operation_id,
            doc_type
        )
        WHERE doc_type != 'other'
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_operation_documents_org_operation
        ON operation_documents (
            organization_id,
            operation_id
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_operation_documents_operation
        ON operation_documents (operation_id)
        """
    )

    if _table_exists(cursor, "operation_vat_documents"):
        cursor.execute(
            """
            INSERT OR IGNORE INTO operation_documents (
                id,
                organization_id,
                operation_id,
                doc_type,
                stored_name,
                original_filename,
                content_type,
                size_bytes,
                uploaded_by_user_id,
                created_at,
                updated_at
            )
            SELECT
                id,
                organization_id,
                operation_id,
                doc_type,
                stored_name,
                original_filename,
                content_type,
                size_bytes,
                uploaded_by_user_id,
                created_at,
                updated_at
            FROM operation_vat_documents
            """
        )
        cursor.execute(
            "DROP TABLE operation_vat_documents"
        )


PROPERTY_EXTERNAL_LISTINGS_CREATE_SQL = """
    CREATE TABLE property_external_listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        property_id INTEGER NOT NULL,
        provider TEXT NOT NULL,
        provider_label TEXT,
        external_id TEXT,
        url TEXT,
        status TEXT NOT NULL,
        listing_currency TEXT,
        buyer_side_commission_percent REAL,
        seller_side_commission_percent REAL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_synced_at TEXT,
        created_by_user_id INTEGER,
        updated_by_user_id INTEGER,

        FOREIGN KEY (organization_id)
            REFERENCES organizations(id)
            ON DELETE RESTRICT,
        FOREIGN KEY (property_id)
            REFERENCES properties(id)
            ON DELETE RESTRICT,
        FOREIGN KEY (created_by_user_id)
            REFERENCES users(id)
            ON DELETE SET NULL,
        FOREIGN KEY (updated_by_user_id)
            REFERENCES users(id)
            ON DELETE SET NULL,

        CHECK (
            provider IN (
                'remax_web',
                'organization_website',
                'zonaprop',
                'argenprop',
                'mercadolibre',
                'other'
            )
        ),
        CHECK (
            status IN (
                'active',
                'paused',
                'reserved',
                'negotiation',
                'sold',
                'inactive'
            )
        ),
        CHECK (
            listing_currency IS NULL
            OR listing_currency IN ('USD', 'ARS')
        ),
        CHECK (
            url IS NULL
            OR url LIKE 'http://%'
            OR url LIKE 'https://%'
        )
    )
"""


def _property_external_listings_sql(cursor):
    cursor.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
            AND name = 'property_external_listings'
        """
    )
    row = cursor.fetchone()

    if row is None or row[0] is None:
        return ""

    return row[0]


def _property_external_listings_needs_upgrade(cursor):
    if not _table_exists(cursor, "property_external_listings"):
        return False

    sql = _property_external_listings_sql(cursor)

    if "negotiation" not in sql:
        return True

    if "listing_currency" not in sql:
        return True

    if "buyer_side_commission_percent" not in sql:
        return True

    # Legacy NOT NULL url without nullable support.
    if "url TEXT NOT NULL" in sql:
        return True

    return False


def _rebuild_property_external_listings(cursor):
    cursor.execute("PRAGMA foreign_keys = OFF")

    cursor.execute(
        """
        ALTER TABLE property_external_listings
        RENAME TO property_external_listings_legacy
        """
    )

    cursor.execute(PROPERTY_EXTERNAL_LISTINGS_CREATE_SQL)

    legacy_cols = {
        row[1]
        for row in cursor.execute(
            "PRAGMA table_info(property_external_listings_legacy)"
        ).fetchall()
    }

    has_currency = "listing_currency" in legacy_cols
    has_buyer = "buyer_side_commission_percent" in legacy_cols
    has_seller = "seller_side_commission_percent" in legacy_cols

    currency_expr = (
        "listing_currency"
        if has_currency
        else "NULL"
    )
    buyer_expr = (
        "buyer_side_commission_percent"
        if has_buyer
        else "NULL"
    )
    seller_expr = (
        "seller_side_commission_percent"
        if has_seller
        else "NULL"
    )

    cursor.execute(
        f"""
        INSERT INTO property_external_listings (
            id,
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
        )
        SELECT
            id,
            organization_id,
            property_id,
            provider,
            provider_label,
            external_id,
            CASE
                WHEN url IS NULL OR url = '' THEN NULL
                ELSE url
            END,
            CASE
                WHEN status = 'negotiation' THEN 'negotiation'
                WHEN status IN (
                    'active',
                    'paused',
                    'reserved',
                    'sold',
                    'inactive'
                ) THEN status
                ELSE 'inactive'
            END,
            {currency_expr},
            {buyer_expr},
            {seller_expr},
            created_at,
            updated_at,
            last_synced_at,
            created_by_user_id,
            updated_by_user_id
        FROM property_external_listings_legacy
        """
    )

    cursor.execute(
        "DROP TABLE property_external_listings_legacy"
    )
    cursor.execute("PRAGMA foreign_keys = ON")


def _migrate_property_external_listings(cursor):
    if not _table_exists(cursor, "property_external_listings"):
        cursor.execute(
            PROPERTY_EXTERNAL_LISTINGS_CREATE_SQL.replace(
                "CREATE TABLE property_external_listings",
                "CREATE TABLE IF NOT EXISTS property_external_listings",
                1,
            )
        )
    elif _property_external_listings_needs_upgrade(cursor):
        _rebuild_property_external_listings(cursor)

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_property_external_listings_org_property
        ON property_external_listings (
            organization_id,
            property_id
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_property_external_listings_org_provider
        ON property_external_listings (
            organization_id,
            provider
        )
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_property_external_listings_structured_provider
        ON property_external_listings (
            organization_id,
            property_id,
            provider
        )
        WHERE provider != 'other'
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_property_external_listings_external_id
        ON property_external_listings (
            organization_id,
            provider,
            external_id
        )
        WHERE external_id IS NOT NULL
            AND external_id != ''
        """
    )


def _migrate_document_storage_folders():
    from modules.config import get_private_upload_root

    root = get_private_upload_root()
    organizations = root / "organizations"

    if not organizations.is_dir():
        return

    for legacy_dir in organizations.glob(
        "*/operations/*/vat-docs"
    ):
        target = legacy_dir.parent / "documents"

        if target.exists():
            for child in legacy_dir.iterdir():
                destination = target / child.name

                if not destination.exists():
                    child.rename(destination)

            try:
                legacy_dir.rmdir()
            except OSError:
                pass
        else:
            legacy_dir.rename(target)


def _migrate_property_and_operation_requirements(cursor):
    if _table_exists(cursor, "properties"):
        if not _column_exists(
            cursor,
            "properties",
            "property_type"
        ):
            cursor.execute(
                """
                ALTER TABLE properties
                ADD COLUMN property_type TEXT
                """
            )

    if _table_exists(cursor, "operations"):
        if not _column_exists(
            cursor,
            "operations",
            "invoice_full_commission"
        ):
            cursor.execute(
                """
                ALTER TABLE operations
                ADD COLUMN invoice_full_commission TEXT
                NOT NULL DEFAULT 'no'
                """
            )

        cursor.execute(
            """
            UPDATE operations
            SET invoice_full_commission = 'no'
            WHERE invoice_full_commission IS NULL
                OR invoice_full_commission = ''
            """
        )

    if _table_exists(cursor, "property_change_requests"):
        if not _column_exists(
            cursor,
            "property_change_requests",
            "proposed_property_type"
        ):
            cursor.execute(
                """
                ALTER TABLE property_change_requests
                ADD COLUMN proposed_property_type TEXT
                """
            )

        if not _column_exists(
            cursor,
            "property_change_requests",
            "proposed_listing_price"
        ):
            cursor.execute(
                """
                ALTER TABLE property_change_requests
                ADD COLUMN proposed_listing_price REAL
                """
            )

        if not _column_exists(
            cursor,
            "property_change_requests",
            "proposed_listing_purpose"
        ):
            cursor.execute(
                """
                ALTER TABLE property_change_requests
                ADD COLUMN proposed_listing_purpose TEXT
                """
            )

    if _table_exists(cursor, "properties"):
        if not _column_exists(
            cursor,
            "properties",
            "listing_price"
        ):
            cursor.execute(
                """
                ALTER TABLE properties
                ADD COLUMN listing_price REAL
                """
            )

        if not _column_exists(
            cursor,
            "properties",
            "listing_purpose"
        ):
            cursor.execute(
                """
                ALTER TABLE properties
                ADD COLUMN listing_purpose TEXT
                """
            )


def _organization_integrations_sql_has_csv_upload(cursor):
    cursor.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
            AND name = 'organization_integrations'
        """
    )
    row = cursor.fetchone()

    if row is None or row[0] is None:
        return False

    return "csv_upload" in row[0]


def _rebuild_organization_integrations_for_csv_upload(cursor):
    # FK from integration_sync_runs blocks DROP while foreign_keys=ON.
    cursor.execute("PRAGMA foreign_keys = OFF")

    cursor.execute(
        """
        CREATE TABLE organization_integrations_migrated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            agent_id INTEGER,
            status TEXT NOT NULL DEFAULT 'disconnected',
            external_office_id TEXT,
            config_json TEXT,
            last_synced_at TEXT,
            last_sync_status TEXT,
            last_sync_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (agent_id)
                REFERENCES agents(id)
                ON DELETE RESTRICT,

            CHECK (
                provider IN (
                    'remax',
                    'organization_website',
                    'stub_fixture',
                    'csv_upload'
                )
            ),
            CHECK (
                scope_type IN (
                    'organization',
                    'agent'
                )
            ),
            CHECK (
                status IN (
                    'disconnected',
                    'connected',
                    'error',
                    'disabled'
                )
            ),
            CHECK (
                last_sync_status IS NULL
                OR last_sync_status IN (
                    'ok',
                    'partial',
                    'failed'
                )
            ),
            CHECK (
                (
                    scope_type = 'organization'
                    AND agent_id IS NULL
                )
                OR (
                    scope_type = 'agent'
                    AND agent_id IS NOT NULL
                )
            )
        )
        """
    )

    cursor.execute(
        """
        INSERT INTO organization_integrations_migrated (
            id,
            organization_id,
            provider,
            scope_type,
            agent_id,
            status,
            external_office_id,
            config_json,
            last_synced_at,
            last_sync_status,
            last_sync_error,
            created_at,
            updated_at
        )
        SELECT
            id,
            organization_id,
            provider,
            scope_type,
            agent_id,
            status,
            external_office_id,
            config_json,
            last_synced_at,
            last_sync_status,
            last_sync_error,
            created_at,
            updated_at
        FROM organization_integrations
        """
    )

    cursor.execute("DROP TABLE organization_integrations")
    cursor.execute(
        """
        ALTER TABLE organization_integrations_migrated
        RENAME TO organization_integrations
        """
    )
    cursor.execute("PRAGMA foreign_keys = ON")


def _migrate_agent_teams_and_wallet(cursor):
    if _table_exists(cursor, "agents"):
        if not _column_exists(
            cursor,
            "agents",
            "team_leader_agent_id",
        ):
            cursor.execute(
                """
                ALTER TABLE agents
                ADD COLUMN team_leader_agent_id INTEGER
                REFERENCES agents(id)
                ON DELETE SET NULL
                """
            )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_agents_team_leader
            ON agents (
                organization_id,
                team_leader_agent_id
            )
            WHERE team_leader_agent_id IS NOT NULL
            """
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_wallet_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            agent_id INTEGER NOT NULL,
            operation_id INTEGER,
            movement_type TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            source_agent_id INTEGER,
            related_movement_id INTEGER,
            description TEXT,
            reference TEXT,
            idempotency_key TEXT,
            created_at TEXT NOT NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (agent_id)
                REFERENCES agents(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (operation_id)
                REFERENCES operations(id)
                ON DELETE SET NULL,
            FOREIGN KEY (source_agent_id)
                REFERENCES agents(id)
                ON DELETE SET NULL,
            FOREIGN KEY (related_movement_id)
                REFERENCES agent_wallet_movements(id)
                ON DELETE SET NULL,

            CHECK (
                movement_type IN (
                    'own_commission',
                    'team_leader_income',
                    'adjustment',
                    'reversal'
                )
            ),
            CHECK (
                currency IN ('USD', 'ARS')
            )
        )
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_wallet_idempotency
        ON agent_wallet_movements (
            organization_id,
            idempotency_key
        )
        WHERE idempotency_key IS NOT NULL
            AND idempotency_key != ''
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_wallet_agent
        ON agent_wallet_movements (
            organization_id,
            agent_id,
            created_at
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_wallet_operation
        ON agent_wallet_movements (
            organization_id,
            operation_id
        )
        """
    )


def _migrate_organization_integrations(cursor):
    if _table_exists(cursor, "agents"):
        if not _column_exists(
            cursor,
            "agents",
            "external_provider"
        ):
            cursor.execute(
                """
                ALTER TABLE agents
                ADD COLUMN external_provider TEXT
                """
            )

        if not _column_exists(
            cursor,
            "agents",
            "external_id"
        ):
            cursor.execute(
                """
                ALTER TABLE agents
                ADD COLUMN external_id TEXT
                """
            )

        if not _column_exists(
            cursor,
            "agents",
            "last_synced_at"
        ):
            cursor.execute(
                """
                ALTER TABLE agents
                ADD COLUMN last_synced_at TEXT
                """
            )

        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_agents_org_external_identity
            ON agents (
                organization_id,
                external_provider,
                external_id
            )
            WHERE external_provider IS NOT NULL
                AND external_id IS NOT NULL
                AND external_id != ''
            """
        )

    if _table_exists(cursor, "properties"):
        if not _column_exists(
            cursor,
            "properties",
            "last_synced_at"
        ):
            cursor.execute(
                """
                ALTER TABLE properties
                ADD COLUMN last_synced_at TEXT
                """
            )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS organization_integrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            agent_id INTEGER,
            status TEXT NOT NULL DEFAULT 'disconnected',
            external_office_id TEXT,
            config_json TEXT,
            last_synced_at TEXT,
            last_sync_status TEXT,
            last_sync_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (agent_id)
                REFERENCES agents(id)
                ON DELETE RESTRICT,

            CHECK (
                provider IN (
                    'remax',
                    'organization_website',
                    'stub_fixture',
                    'csv_upload'
                )
            ),
            CHECK (
                scope_type IN (
                    'organization',
                    'agent'
                )
            ),
            CHECK (
                status IN (
                    'disconnected',
                    'connected',
                    'error',
                    'disabled'
                )
            ),
            CHECK (
                last_sync_status IS NULL
                OR last_sync_status IN (
                    'ok',
                    'partial',
                    'failed'
                )
            ),
            CHECK (
                (
                    scope_type = 'organization'
                    AND agent_id IS NULL
                )
                OR (
                    scope_type = 'agent'
                    AND agent_id IS NOT NULL
                )
            )
        )
        """
    )

    if (
        _table_exists(cursor, "organization_integrations")
        and not _organization_integrations_sql_has_csv_upload(
            cursor
        )
    ):
        _rebuild_organization_integrations_for_csv_upload(
            cursor
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS csv_import_batches (
            id TEXT PRIMARY KEY,
            organization_id INTEGER NOT NULL,
            filename TEXT,
            payload_json TEXT NOT NULL,
            preview_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_csv_import_batches_organization
        ON csv_import_batches (organization_id)
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_org_integrations_org_provider_org_scope
        ON organization_integrations (
            organization_id,
            provider
        )
        WHERE scope_type = 'organization'
        """
    )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_org_integrations_org_provider_agent_scope
        ON organization_integrations (
            organization_id,
            provider,
            agent_id
        )
        WHERE scope_type = 'agent'
            AND agent_id IS NOT NULL
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_org_integrations_organization
        ON organization_integrations (organization_id)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS integration_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            integration_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            agents_created INTEGER NOT NULL DEFAULT 0,
            agents_updated INTEGER NOT NULL DEFAULT 0,
            properties_created INTEGER NOT NULL DEFAULT 0,
            properties_updated INTEGER NOT NULL DEFAULT 0,
            listings_created INTEGER NOT NULL DEFAULT 0,
            listings_updated INTEGER NOT NULL DEFAULT 0,
            listings_deactivated INTEGER NOT NULL DEFAULT 0,
            error_summary TEXT,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (integration_id)
                REFERENCES organization_integrations(id)
                ON DELETE CASCADE,

            CHECK (
                status IN (
                    'running',
                    'ok',
                    'partial',
                    'failed'
                )
            )
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_integration_sync_runs_integration
        ON integration_sync_runs (
            integration_id,
            started_at
        )
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
        _migrate_operation_documents(cursor)
        _migrate_property_external_listings(cursor)
        _migrate_property_and_operation_requirements(cursor)
        _migrate_organization_integrations(cursor)
        _migrate_agent_teams_and_wallet(cursor)

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
    _migrate_document_storage_folders()

    return backup_path


def create_tables():
    from modules.config import (
        BACKEND_POSTGRES,
        get_database_backend,
    )

    if get_database_backend() == BACKEND_POSTGRES:
        from .schema_postgres import (
            create_postgres_schema,
        )

        create_postgres_schema()
        return

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
            external_provider TEXT,
            external_id TEXT,
            last_synced_at TEXT,
            team_leader_agent_id INTEGER,

            FOREIGN KEY (organization_id)
                REFERENCES organizations(id)
                ON DELETE RESTRICT,

            FOREIGN KEY (team_leader_agent_id)
                REFERENCES agents(id)
                ON DELETE SET NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            organization_id INTEGER NOT NULL,
            agent_id INTEGER,
            property_type TEXT,
            listing_price REAL,
            listing_purpose TEXT,
            last_synced_at TEXT,

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
            invoice_full_commission TEXT NOT NULL DEFAULT 'no',

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
