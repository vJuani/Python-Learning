"""
Safe migration for invoice active-row uniqueness.

Legacy: UNIQUE (organization_id, operation_id) for active statuses.
Current: UNIQUE (organization_id, operation_id, side, issuer_key).
"""

from __future__ import annotations

import sqlite3

ACTIVE_INVOICE_STATUSES = (
    "draft",
    "ready_to_issue",
    "issued",
    "error",
)

LEGACY_INDEX_NAME = "idx_invoices_one_active_per_operation"
NEW_INDEX_NAME = "idx_invoices_one_active_per_issuer_side"


def _sqlite_index_exists(cursor, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'index'
            AND name = ?
        """,
        (index_name,),
    )
    return cursor.fetchone() is not None


def _postgres_index_exists(cursor, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM pg_indexes
        WHERE indexname = %s
        """,
        (index_name,),
    )
    return cursor.fetchone() is not None


def _column_exists_sqlite(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return column_name in [row[1] for row in cursor.fetchall()]


def _column_exists_postgres(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
            AND table_name = %s
            AND column_name = %s
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def drop_legacy_invoice_operation_index(cursor, *, postgres: bool = False) -> None:
    cursor.execute(
        f"DROP INDEX IF EXISTS {LEGACY_INDEX_NAME}"
    )


def _backfill_invoice_identity_columns(
    cursor,
    *,
    postgres: bool = False,
    column_exists,
) -> None:
    if not column_exists(cursor, "invoices", "side"):
        return

    cursor.execute(
        """
        UPDATE invoices
        SET side = 'buyer'
        WHERE side IS NULL OR side = ''
        """
    )

    if column_exists(cursor, "invoices", "issuer_key"):
        if postgres:
            cursor.execute(
                """
                UPDATE invoices
                SET issuer_key = 'agent:' || agent_id::text
                WHERE (issuer_key IS NULL OR issuer_key = '')
                    AND agent_id IS NOT NULL
                """
            )
            cursor.execute(
                """
                UPDATE invoices
                SET issuer_key = 'legacy:' || id::text
                WHERE issuer_key IS NULL OR issuer_key = ''
                """
            )
        else:
            cursor.execute(
                """
                UPDATE invoices
                SET issuer_key = 'agent:' || agent_id
                WHERE (issuer_key IS NULL OR issuer_key = '')
                    AND agent_id IS NOT NULL
                """
            )
            cursor.execute(
                """
                UPDATE invoices
                SET issuer_key = 'legacy:' || id
                WHERE issuer_key IS NULL OR issuer_key = ''
                """
            )


def _active_invoice_rows(cursor, *, postgres: bool = False):
    placeholders = ", ".join(
        "%s" if postgres else "?"
        for _ in ACTIVE_INVOICE_STATUSES
    )
    cursor.execute(
        f"""
        SELECT
            id,
            organization_id,
            operation_id,
            side,
            issuer_key,
            agent_id
        FROM invoices
        WHERE status IN ({placeholders})
        ORDER BY organization_id, operation_id, id
        """,
        ACTIVE_INVOICE_STATUSES,
    )
    return cursor.fetchall()


def _disambiguate_active_invoice_rows(cursor, *, postgres: bool = False) -> None:
    rows = _active_invoice_rows(cursor, postgres=postgres)
    if not rows:
        return

    param = "%s" if postgres else "?"

    by_operation: dict[tuple[int, int], list] = {}
    for row in rows:
        inv_id, org_id, operation_id, side, issuer_key, agent_id = row
        by_operation.setdefault(
            (org_id, operation_id),
            [],
        ).append(row)

    for (_org_id, _operation_id), group in by_operation.items():
        if len(group) <= 1:
            continue
        for index, row in enumerate(group):
            inv_id = row[0]
            new_side = "buyer" if index % 2 == 0 else "seller"
            cursor.execute(
                f"""
                UPDATE invoices
                SET side = {param}
                WHERE id = {param}
                """,
                (new_side, inv_id),
            )

    rows = _active_invoice_rows(cursor, postgres=postgres)
    by_identity: dict[tuple, list[int]] = {}
    for row in rows:
        inv_id, org_id, operation_id, side, issuer_key, _agent_id = row
        key = (org_id, operation_id, side or "", issuer_key or "")
        by_identity.setdefault(key, []).append(inv_id)

    for ids in by_identity.values():
        if len(ids) <= 1:
            continue
        for inv_id in ids[1:]:
            if postgres:
                cursor.execute(
                    """
                    UPDATE invoices
                    SET issuer_key = issuer_key || ':inv:' || id::text
                    WHERE id = %s
                    """,
                    (inv_id,),
                )
            else:
                cursor.execute(
                    """
                    UPDATE invoices
                    SET issuer_key = issuer_key || ':inv:' || id
                    WHERE id = ?
                    """,
                    (inv_id,),
                )


def _create_new_unique_index(cursor, *, postgres: bool = False) -> None:
    if postgres:
        if _postgres_index_exists(cursor, NEW_INDEX_NAME):
            return
        index_sql = f"""
            CREATE UNIQUE INDEX {NEW_INDEX_NAME}
            ON invoices (
                organization_id,
                operation_id,
                side,
                issuer_key
            )
            WHERE status IN (
                'draft',
                'ready_to_issue',
                'issued',
                'error'
            )
                AND issuer_key IS NOT NULL
        """
        try:
            cursor.execute(index_sql)
        except Exception:
            _disambiguate_active_invoice_rows(cursor, postgres=True)
            if not _postgres_index_exists(cursor, NEW_INDEX_NAME):
                cursor.execute(index_sql)
        return

    if _sqlite_index_exists(cursor, NEW_INDEX_NAME):
        return

    index_sql = f"""
        CREATE UNIQUE INDEX {NEW_INDEX_NAME}
        ON invoices (
            organization_id,
            operation_id,
            side,
            issuer_key
        )
        WHERE status IN (
            'draft',
            'ready_to_issue',
            'issued',
            'error'
        )
            AND issuer_key IS NOT NULL
    """
    try:
        cursor.execute(index_sql)
    except sqlite3.IntegrityError:
        _disambiguate_active_invoice_rows(cursor, postgres=False)
        if not _sqlite_index_exists(cursor, NEW_INDEX_NAME):
            cursor.execute(index_sql)


def _sqlite_table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
            AND name = ?
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def migrate_invoices_active_uniqueness_sqlite(cursor) -> None:
    if not _sqlite_table_exists(cursor, "invoices"):
        return

    drop_legacy_invoice_operation_index(cursor, postgres=False)
    _backfill_invoice_identity_columns(
        cursor,
        postgres=False,
        column_exists=_column_exists_sqlite,
    )
    _disambiguate_active_invoice_rows(cursor, postgres=False)
    _create_new_unique_index(cursor, postgres=False)


def migrate_invoices_active_uniqueness_postgres(cursor) -> None:
    drop_legacy_invoice_operation_index(cursor, postgres=True)
    _backfill_invoice_identity_columns(
        cursor,
        postgres=True,
        column_exists=_column_exists_postgres,
    )
    _disambiguate_active_invoice_rows(cursor, postgres=True)
    _create_new_unique_index(cursor, postgres=True)
