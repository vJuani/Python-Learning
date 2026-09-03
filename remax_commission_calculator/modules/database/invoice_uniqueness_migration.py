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
TEMP_ISSUER_PREFIX = "legacy_tmp:"
KNOWN_UNIQUENESS_INDEX_NAMES = (
    LEGACY_INDEX_NAME,
    NEW_INDEX_NAME,
)

ACTIVE_STATUS_SQL = (
    "'draft', 'ready_to_issue', 'issued', 'error'"
)
ACTIVE_STATUS_PLACEHOLDERS_SQLITE = ", ".join(
    "?" for _ in ACTIVE_INVOICE_STATUSES
)


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


def _sqlite_invoice_uniqueness_index_names(cursor) -> list[str]:
    """Return UNIQUE index names on invoices touching operation identity columns."""
    cursor.execute("PRAGMA index_list(invoices)")
    index_rows = cursor.fetchall()
    targets = {
        "organization_id",
        "operation_id",
        "side",
        "issuer_key",
    }
    legacy_pair = {"organization_id", "operation_id"}

    names: list[str] = []
    for _seq, name, is_unique, _origin, _partial in index_rows:
        if not is_unique or not name:
            continue
        if name.startswith("sqlite_autoindex"):
            continue

        cursor.execute(f"PRAGMA index_info({name})")
        columns = {row[2] for row in cursor.fetchall()}
        if not columns:
            continue

        if columns & targets and (
            columns <= targets
            or columns == legacy_pair
        ):
            names.append(name)

    return names


def _drop_sqlite_invoice_uniqueness_indexes(cursor) -> None:
    for name in KNOWN_UNIQUENESS_INDEX_NAMES:
        cursor.execute(f"DROP INDEX IF EXISTS {name}")

    for name in _sqlite_invoice_uniqueness_index_names(cursor):
        cursor.execute(f"DROP INDEX IF EXISTS {name}")


def _drop_postgres_invoice_uniqueness_indexes(cursor) -> None:
    for name in KNOWN_UNIQUENESS_INDEX_NAMES:
        cursor.execute(f"DROP INDEX IF EXISTS {name}")

    cursor.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE tablename = 'invoices'
            AND indexdef ILIKE '%UNIQUE%'
            AND (
                indexdef ILIKE '%organization_id%'
                OR indexdef ILIKE '%operation_id%'
            )
            AND indexname NOT LIKE '%invoice_seq%'
            AND indexname NOT LIKE '%invoice_number_internal%'
            AND indexname NOT LIKE '%fiscal_voucher%'
        """
    )
    for (name,) in cursor.fetchall():
        cursor.execute(f"DROP INDEX IF EXISTS {name}")


def _origin_filter(cursor, *, postgres: bool = False) -> str:
    column_exists = (
        _column_exists_postgres
        if postgres
        else _column_exists_sqlite
    )
    if column_exists(cursor, "invoices", "origin_type"):
        return " AND origin_type = 'operation'"
    return ""


def _assign_temporary_issuer_keys(cursor, *, postgres: bool = False) -> None:
    """Give every active row a unique issuer_key before normalization."""
    placeholders = ", ".join(
        "%s" if postgres else "?"
        for _ in ACTIVE_INVOICE_STATUSES
    )
    origin_filter = _origin_filter(cursor, postgres=postgres)
    if postgres:
        cursor.execute(
            f"""
            UPDATE invoices
            SET issuer_key = '{TEMP_ISSUER_PREFIX}' || id::text
            WHERE status IN ({placeholders})
                {origin_filter}
            """,
            ACTIVE_INVOICE_STATUSES,
        )
    else:
        cursor.execute(
            f"""
            UPDATE invoices
            SET issuer_key = '{TEMP_ISSUER_PREFIX}' || id
            WHERE status IN ({placeholders})
                {origin_filter}
            """,
            ACTIVE_INVOICE_STATUSES,
        )


def _backfill_invoice_identity_columns(
    cursor,
    *,
    postgres: bool = False,
    column_exists,
) -> None:
    if not column_exists(cursor, "invoices", "side"):
        return

    origin_filter = _origin_filter(cursor, postgres=postgres)
    cursor.execute(
        f"""
        UPDATE invoices
        SET side = 'buyer'
        WHERE side IS NULL OR side = ''
            {origin_filter}
        """
    )


def _active_invoice_rows(cursor, *, postgres: bool = False):
    placeholders = ", ".join(
        "%s" if postgres else "?"
        for _ in ACTIVE_INVOICE_STATUSES
    )
    origin_filter = _origin_filter(cursor, postgres=postgres)
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
            {origin_filter}
        ORDER BY organization_id, operation_id, id
        """,
        ACTIVE_INVOICE_STATUSES,
    )
    return cursor.fetchall()


def _final_issuer_key(agent_id, invoice_id: int) -> str:
    if agent_id is not None:
        return f"agent:{agent_id}:inv:{invoice_id}"
    return f"legacy:{invoice_id}"


def _normalize_active_invoice_rows(cursor, *, postgres: bool = False) -> None:
    rows = _active_invoice_rows(cursor, postgres=postgres)
    if not rows:
        return

    param = "%s" if postgres else "?"

    by_operation: dict[tuple[int, int], list] = {}
    for row in rows:
        _inv_id, org_id, operation_id, _side, _issuer_key, _agent_id = row
        by_operation.setdefault((org_id, operation_id), []).append(row)

    for (_org_id, _operation_id), group in by_operation.items():
        if len(group) == 1:
            inv_id, _, _, side, _, agent_id = group[0]
            final_side = side if side in ("buyer", "seller") else "buyer"
            final_issuer = _final_issuer_key(agent_id, inv_id)
            cursor.execute(
                f"""
                UPDATE invoices
                SET side = {param},
                    issuer_key = {param}
                WHERE id = {param}
                """,
                (final_side, final_issuer, inv_id),
            )
            continue

        distinct_sides = {
            (row[3] or "").strip().lower()
            for row in group
            if (row[3] or "").strip().lower() in ("buyer", "seller")
        }

        if len(group) == 2 and len(distinct_sides) < 2:
            for index, row in enumerate(group):
                inv_id, _, _, _side, _, agent_id = row
                new_side = "buyer" if index == 0 else "seller"
                final_issuer = _final_issuer_key(agent_id, inv_id)
                cursor.execute(
                    f"""
                    UPDATE invoices
                    SET side = {param},
                        issuer_key = {param}
                    WHERE id = {param}
                    """,
                    (new_side, final_issuer, inv_id),
                )
            continue

        for row in group:
            inv_id, _, _, side, _, agent_id = row
            final_side = side if side in ("buyer", "seller") else "buyer"
            final_issuer = _final_issuer_key(agent_id, inv_id)
            cursor.execute(
                f"""
                UPDATE invoices
                SET side = {param},
                    issuer_key = {param}
                WHERE id = {param}
                """,
                (final_side, final_issuer, inv_id),
            )


def _validate_active_invoice_uniqueness(cursor, *, postgres: bool = False) -> None:
    placeholders = ", ".join(
        "%s" if postgres else "?"
        for _ in ACTIVE_INVOICE_STATUSES
    )
    origin_filter = _origin_filter(cursor, postgres=postgres)
    cursor.execute(
        f"""
        SELECT
            organization_id,
            operation_id,
            side,
            issuer_key,
            COUNT(*) AS row_count
        FROM invoices
        WHERE status IN ({placeholders})
            {origin_filter}
        GROUP BY organization_id, operation_id, side, issuer_key
        HAVING COUNT(*) > 1
        """,
        ACTIVE_INVOICE_STATUSES,
    )
    duplicates = cursor.fetchall()
    if duplicates:
        raise sqlite3.IntegrityError(
            "invoice active uniqueness validation failed: "
            f"{duplicates!r}"
        )


def _create_new_unique_index(cursor, *, postgres: bool = False) -> None:
    if _postgres_index_exists(cursor, NEW_INDEX_NAME) if postgres else (
        _sqlite_index_exists(cursor, NEW_INDEX_NAME)
    ):
        return

    origin_filter = ""
    column_exists = (
        _column_exists_postgres
        if postgres
        else _column_exists_sqlite
    )
    if column_exists(cursor, "invoices", "origin_type"):
        origin_filter = " AND origin_type = 'operation'"

    index_sql = f"""
        CREATE UNIQUE INDEX {NEW_INDEX_NAME}
        ON invoices (
            organization_id,
            operation_id,
            side,
            issuer_key
        )
        WHERE status IN ({ACTIVE_STATUS_SQL})
            {origin_filter}
    """
    cursor.execute(index_sql)


def migrate_invoices_active_uniqueness_sqlite(cursor) -> None:
    if not _sqlite_table_exists(cursor, "invoices"):
        return

    if not _column_exists_sqlite(cursor, "invoices", "side"):
        return

    # 1-3. Drop legacy/new/equivalent UNIQUE indexes before touching rows.
    _drop_sqlite_invoice_uniqueness_indexes(cursor)

    # 4. Unique temporary issuer keys so row updates cannot collide.
    _assign_temporary_issuer_keys(cursor, postgres=False)

    # 5. Backfill + normalize to final side/issuer_key values.
    _backfill_invoice_identity_columns(
        cursor,
        postgres=False,
        column_exists=_column_exists_sqlite,
    )
    _normalize_active_invoice_rows(cursor, postgres=False)

    # 6. Validation must pass before index creation.
    _validate_active_invoice_uniqueness(cursor, postgres=False)

    # 7. Create target partial unique index (idempotent).
    _create_new_unique_index(cursor, postgres=False)


def migrate_invoices_active_uniqueness_postgres(cursor) -> None:
    if not _column_exists_postgres(cursor, "invoices", "side"):
        return

    _drop_postgres_invoice_uniqueness_indexes(cursor)
    _assign_temporary_issuer_keys(cursor, postgres=True)
    _backfill_invoice_identity_columns(
        cursor,
        postgres=True,
        column_exists=_column_exists_postgres,
    )
    _normalize_active_invoice_rows(cursor, postgres=True)
    _validate_active_invoice_uniqueness(cursor, postgres=True)
    _create_new_unique_index(cursor, postgres=True)


# Backward-compatible alias used in tests/diagnostics.
def drop_legacy_invoice_operation_index(cursor, *, postgres: bool = False) -> None:
    if postgres:
        _drop_postgres_invoice_uniqueness_indexes(cursor)
    else:
        _drop_sqlite_invoice_uniqueness_indexes(cursor)
