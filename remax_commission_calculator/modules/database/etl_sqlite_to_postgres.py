"""
SQLite → PostgreSQL one-shot ETL (Phase 3).

Reads SQLite with the stdlib sqlite3 driver and writes PostgreSQL with
psycopg. Does not modify or delete the SQLite source file.

Team Leader / Junior is a column on agents (team_leader_agent_id),
migrated in a second pass after all agent rows exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Optional, Sequence
import sqlite3

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


# Data tables only (skip schema_migrations — owned by PG schema init).
MIGRATION_TABLE_ORDER: tuple[str, ...] = (
    "organizations",
    "organization_settings",
    "agents",
    "users",
    "properties",
    "operations",
    "registration_requests",
    "email_verification_tokens",
    "organization_guest_access",
    "notifications",
    "operation_documents",
    "property_change_requests",
    "property_external_listings",
    "organization_integrations",
    "csv_import_batches",
    "integration_sync_runs",
    "agent_wallet_movements",
)

# Columns with explicit identity that need setval after load.
IDENTITY_TABLES: tuple[tuple[str, str], ...] = (
    ("organizations", "id"),
    ("agents", "id"),
    ("users", "id"),
    ("properties", "id"),
    ("operations", "id"),
    ("registration_requests", "id"),
    ("email_verification_tokens", "id"),
    ("organization_guest_access", "id"),
    ("notifications", "id"),
    ("operation_documents", "id"),
    ("property_change_requests", "id"),
    ("property_external_listings", "id"),
    ("organization_integrations", "id"),
    ("integration_sync_runs", "id"),
    ("agent_wallet_movements", "id"),
)

# Insert first with these self-FK columns forced to NULL, then UPDATE.
DEFERRED_SELF_FK: dict[str, tuple[str, ...]] = {
    "agents": ("team_leader_agent_id",),
    "agent_wallet_movements": ("related_movement_id",),
}

MONEY_COLUMNS: dict[str, frozenset[str]] = {
    "properties": frozenset({"listing_price"}),
    "operations": frozenset({
        "vat_amount",
        "sale_price",
        "commission_rate",
        "total_commission",
        "commission_after_abao",
        "abao",
        "martillero",
        "agent_payment",
        "office_payment",
        "office_total",
        "original_amount",
        "exchange_rate",
    }),
    "property_change_requests": frozenset({
        "proposed_listing_price",
    }),
    "property_external_listings": frozenset({
        "buyer_side_commission_percent",
        "seller_side_commission_percent",
    }),
    "agent_wallet_movements": frozenset({"amount"}),
}

FLAG_COLUMNS: dict[str, frozenset[str]] = {
    "organizations": frozenset({"is_active"}),
    "organization_settings": frozenset({"registration_enabled"}),
    "users": frozenset({"is_active"}),
    "notifications": frozenset({"is_read"}),
}

NULL_CHECK_COLUMNS: dict[str, tuple[str, ...]] = {
    "agents": ("team_leader_agent_id", "external_id"),
    "properties": ("agent_id", "listing_price"),
    "operations": ("original_amount", "rejection_reason"),
    "users": ("agent_id", "email"),
    "agent_wallet_movements": (
        "operation_id",
        "idempotency_key",
        "related_movement_id",
    ),
    "property_external_listings": ("external_id", "url"),
}


class EtlError(Exception):
    """Fatal ETL / validation error."""


@dataclass
class TableCopyResult:
    table: str
    source_count: int
    dest_count: int
    ok: bool
    detail: str = ""


@dataclass
class ValidationReport:
    lines: list[str] = field(default_factory=list)
    passed: bool = True

    def add(self, line: str, ok: bool = True) -> None:
        status = "OK" if ok else "FAIL"
        self.lines.append(f"{line} {status}")
        if not ok:
            self.passed = False


# Destination money scale for NUMERIC(18,4) — validation only.
MONEY_QUANT = Decimal("0.0001")


def to_decimal(value: Any) -> Optional[Decimal]:
    """Convert SQLite REAL / numeric values without 2-decimal truncation."""
    if value is None:
        return None

    if isinstance(value, Decimal):
        return value

    if isinstance(value, bool):
        raise EtlError(f"Unexpected bool for money column: {value!r}")

    if isinstance(value, int):
        return Decimal(value)

    if isinstance(value, float):
        # str() avoids binary float artifacts better than Decimal(float).
        return Decimal(str(value))

    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")

    text = str(value).strip()
    if text == "":
        return None

    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise EtlError(
            f"Cannot convert money value {value!r} to Decimal"
        ) from error


def quantize_money(value: Any) -> Decimal:
    """Normalize a monetary value to NUMERIC(18,4) scale (comparison only)."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        dec = value
    else:
        converted = to_decimal(value)
        if converted is None:
            return Decimal("0")
        dec = converted
    return dec.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def money_equal(left: Any, right: Any) -> bool:
    """True when both sides match at NUMERIC(18,4) storage scale."""
    return quantize_money(left) == quantize_money(right)


def to_flag(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    return int(value)


def open_sqlite_readonly(path: str) -> sqlite3.Connection:
    from pathlib import Path

    resolved = Path(path).expanduser().resolve()
    posix = resolved.as_posix()

    if len(posix) >= 2 and posix[1] == ":":
        uri = f"file:///{posix}?mode=ro"
    else:
        uri = f"file:{posix}?mode=ro"

    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        # Fallback if URI/ro is unavailable; ETL never writes to SQLite.
        connection = sqlite3.connect(str(resolved))

    connection.row_factory = sqlite3.Row
    return connection


def open_postgres(dsn: str):
    if psycopg is None:
        raise EtlError(
            "psycopg is not installed. "
            "Install with: pip install 'psycopg[binary]'"
        )
    return psycopg.connect(dsn)


def sqlite_table_exists(
    sqlite_conn: sqlite3.Connection,
    table: str,
) -> bool:
    row = sqlite_conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
            AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def sqlite_columns(
    sqlite_conn: sqlite3.Connection,
    table: str,
) -> list[str]:
    rows = sqlite_conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()
    return [row["name"] for row in rows]


def postgres_columns(
    pg_conn,
    table: str,
) -> list[str]:
    rows = pg_conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
            AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    return [row[0] for row in rows]


def intersect_columns(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
) -> list[str]:
    src = sqlite_columns(sqlite_conn, table)
    dst = set(postgres_columns(pg_conn, table))
    missing_in_pg = [c for c in src if c not in dst]
    if missing_in_pg:
        # SQLite-only legacy columns are skipped with a warning via caller.
        pass
    return [c for c in src if c in dst]


def count_rows(conn, table: str, *, placeholder: str = "?") -> int:
    # table names are controlled allow-list only
    if table not in MIGRATION_TABLE_ORDER and table != "schema_migrations":
        raise EtlError(f"Refusing to count unknown table {table!r}")
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def postgres_has_data(pg_conn) -> list[str]:
    nonempty: list[str] = []
    for table in MIGRATION_TABLE_ORDER:
        exists = pg_conn.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
                AND table_name = %s
            """,
            (table,),
        ).fetchone()
        if exists is None:
            continue
        if count_rows(pg_conn, table) > 0:
            nonempty.append(table)
    return nonempty


def ensure_postgres_schema(pg_dsn: str) -> None:
    """Apply clean PG schema using app helpers (sets DATABASE_URL briefly)."""
    import os

    from modules.database.schema_postgres import (
        create_postgres_schema,
    )

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_dsn
    try:
        create_postgres_schema()
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def truncate_data_tables(pg_conn) -> None:
    tables = ", ".join(reversed(MIGRATION_TABLE_ORDER))
    pg_conn.execute(
        f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"
    )


def transform_row(
    table: str,
    columns: Sequence[str],
    row: sqlite3.Row,
    *,
    null_deferred: Sequence[str] = (),
) -> tuple[Any, ...]:
    money = MONEY_COLUMNS.get(table, frozenset())
    flags = FLAG_COLUMNS.get(table, frozenset())
    values: list[Any] = []

    for column in columns:
        value = row[column]

        if column in null_deferred:
            values.append(None)
            continue

        if column in money:
            values.append(to_decimal(value))
            continue

        if column in flags:
            values.append(to_flag(value))
            continue

        values.append(value)

    return tuple(values)


def copy_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
    *,
    columns: Sequence[str],
    null_deferred: Sequence[str] = (),
) -> int:
    if not sqlite_table_exists(sqlite_conn, table):
        return 0

    rows = sqlite_conn.execute(
        f"SELECT * FROM {table} ORDER BY rowid"
    ).fetchall()

    if not rows:
        return 0

    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    # OVERRIDING SYSTEM VALUE keeps explicit ids with IDENTITY columns.
    has_identity = any(
        table == t and "id" in columns
        for t, _ in IDENTITY_TABLES
    ) or (
        table == "organization_settings"
        and "organization_id" in columns
    )

    if table == "organization_settings" or table == "csv_import_batches":
        sql = (
            f"INSERT INTO {table} ({col_list}) "
            f"VALUES ({placeholders})"
        )
    elif has_identity and "id" in columns:
        sql = (
            f"INSERT INTO {table} ({col_list}) "
            f"OVERRIDING SYSTEM VALUE "
            f"VALUES ({placeholders})"
        )
    else:
        sql = (
            f"INSERT INTO {table} ({col_list}) "
            f"VALUES ({placeholders})"
        )

    payload = [
        transform_row(
            table,
            columns,
            row,
            null_deferred=null_deferred,
        )
        for row in rows
    ]

    try:
        with pg_conn.cursor() as cursor:
            cursor.executemany(sql, payload)
    except Exception as error:
        raise EtlError(
            f"Failed inserting into {table} "
            f"({len(payload)} rows): {error}"
        ) from error

    return len(payload)


def apply_deferred_self_fks(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
    columns: Sequence[str],
) -> int:
    deferred = DEFERRED_SELF_FK.get(table)
    if not deferred:
        return 0

    pk = "id"
    if pk not in columns:
        return 0

    updated = 0
    rows = sqlite_conn.execute(
        f"SELECT * FROM {table} ORDER BY rowid"
    ).fetchall()

    for row in rows:
        sets = []
        params: list[Any] = []
        for column in deferred:
            if column not in columns:
                continue
            value = row[column]
            sets.append(f"{column} = %s")
            params.append(value)

        if not sets:
            continue

        params.append(row[pk])
        sql = (
            f"UPDATE {table} SET {', '.join(sets)} "
            f"WHERE {pk} = %s"
        )
        try:
            pg_conn.execute(sql, params)
            updated += 1
        except Exception as error:
            raise EtlError(
                f"Failed deferred FK update on {table} "
                f"id={row[pk]}: {error}"
            ) from error

    return updated


def reset_identity_sequences(pg_conn) -> list[str]:
    messages: list[str] = []

    for table, column in IDENTITY_TABLES:
        exists = pg_conn.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
                AND table_name = %s
            """,
            (table,),
        ).fetchone()
        if exists is None:
            continue

        seq_row = pg_conn.execute(
            "SELECT pg_get_serial_sequence(%s, %s)",
            (table, column),
        ).fetchone()
        sequence = seq_row[0] if seq_row else None
        if not sequence:
            messages.append(
                f"{table}.{column}: no sequence (skipped)"
            )
            continue

        max_row = pg_conn.execute(
            f"SELECT MAX({column}) FROM {table}"
        ).fetchone()
        max_id = max_row[0]

        if max_id is None:
            # Next nextval should yield 1.
            pg_conn.execute(
                "SELECT setval(%s, %s, %s)",
                (sequence, 1, False),
            )
            messages.append(
                f"{table}.{column}: setval empty → next=1"
            )
        else:
            pg_conn.execute(
                "SELECT setval(%s, %s, %s)",
                (sequence, int(max_id), True),
            )
            messages.append(
                f"{table}.{column}: setval → next={int(max_id) + 1}"
            )

    return messages


def _scalar(conn, sql: str, params: Sequence[Any] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _decimal_sum(conn, sql: str, params: Sequence[Any] = ()) -> Decimal:
    value = _scalar(conn, sql, params)
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _sqlite_money_sum(
    sqlite_conn: sqlite3.Connection,
    table: str,
    column: str,
) -> Decimal:
    """
    Sum money as stored in PG NUMERIC(18,4): per-row to_decimal + quantize.

    Do not use SQL SUM(REAL) — binary float aggregates diverge from
    destination scale even after quantizing the aggregate once.
    """
    total = Decimal("0")
    for (raw,) in sqlite_conn.execute(
        f"SELECT {column} FROM {table}"
    ):
        if raw is None:
            continue
        converted = to_decimal(raw)
        if converted is None:
            continue
        total += quantize_money(converted)
    return total


def validate_migration(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
) -> ValidationReport:
    report = ValidationReport()

    for table in MIGRATION_TABLE_ORDER:
        if not sqlite_table_exists(sqlite_conn, table):
            src = 0
        else:
            src = count_rows(sqlite_conn, table)

        dst = count_rows(pg_conn, table)
        ok = src == dst
        report.add(f"{table}: {src} -> {dst}", ok)

        if not ok:
            continue

        # max(id) when both have identity id
        if any(t == table for t, _ in IDENTITY_TABLES):
            if src == 0:
                continue
            src_max = _scalar(
                sqlite_conn,
                f"SELECT MAX(id) FROM {table}",
            )
            dst_max = _scalar(
                pg_conn,
                f"SELECT MAX(id) FROM {table}",
            )
            ok_max = src_max == dst_max
            report.add(
                f"{table} max(id): {src_max} -> {dst_max}",
                ok_max,
            )

        for column in NULL_CHECK_COLUMNS.get(table, ()):
            if not sqlite_table_exists(sqlite_conn, table):
                continue
            src_cols = set(sqlite_columns(sqlite_conn, table))
            if column not in src_cols:
                continue
            src_nulls = _scalar(
                sqlite_conn,
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {column} IS NULL",
            )
            dst_nulls = _scalar(
                pg_conn,
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {column} IS NULL",
            )
            report.add(
                f"{table} null({column}): "
                f"{src_nulls} -> {dst_nulls}",
                src_nulls == dst_nulls,
            )

    # Money sums at NUMERIC(18,4) destination scale
    if sqlite_table_exists(sqlite_conn, "operations"):
        money_cols = (
            "sale_price",
            "total_commission",
            "commission_after_abao",
            "abao",
            "agent_payment",
            "office_total",
        )
        for column in money_cols:
            src_sum = _sqlite_money_sum(
                sqlite_conn, "operations", column
            )
            dst_sum = quantize_money(
                _decimal_sum(
                    pg_conn,
                    f"SELECT COALESCE(SUM({column}), 0) "
                    f"FROM operations",
                )
            )
            report.add(
                f"operations SUM({column}): "
                f"{src_sum} -> {dst_sum}",
                money_equal(src_sum, dst_sum),
            )

    if sqlite_table_exists(sqlite_conn, "agent_wallet_movements"):
        src_sum = _sqlite_money_sum(
            sqlite_conn, "agent_wallet_movements", "amount"
        )
        dst_sum = quantize_money(
            _decimal_sum(
                pg_conn,
                "SELECT COALESCE(SUM(amount), 0) "
                "FROM agent_wallet_movements",
            )
        )
        report.add(
            f"wallet sum: {src_sum} -> {dst_sum}",
            money_equal(src_sum, dst_sum),
        )

    # Domain counts
    for label, table in (
        ("agents", "agents"),
        ("properties", "properties"),
        ("operations", "operations"),
        ("property_external_listings", "property_external_listings"),
    ):
        if not sqlite_table_exists(sqlite_conn, table):
            src = 0
        else:
            src = count_rows(sqlite_conn, table)
        dst = count_rows(pg_conn, table)
        report.add(f"count {label}: {src} -> {dst}", src == dst)

    # Team Leader / Junior edges
    if sqlite_table_exists(sqlite_conn, "agents"):
        src_tl = _scalar(
            sqlite_conn,
            """
            SELECT COUNT(*)
            FROM agents
            WHERE team_leader_agent_id IS NOT NULL
            """,
        )
        dst_tl = _scalar(
            pg_conn,
            """
            SELECT COUNT(*)
            FROM agents
            WHERE team_leader_agent_id IS NOT NULL
            """,
        )
        report.add(
            f"team_leader links: {src_tl} -> {dst_tl}",
            src_tl == dst_tl,
        )

    # Users per organization
    if sqlite_table_exists(sqlite_conn, "users"):
        src_rows = sqlite_conn.execute(
            """
            SELECT organization_id, COUNT(*)
            FROM users
            GROUP BY organization_id
            ORDER BY organization_id
            """
        ).fetchall()
        dst_rows = pg_conn.execute(
            """
            SELECT organization_id, COUNT(*)
            FROM users
            GROUP BY organization_id
            ORDER BY organization_id
            """
        ).fetchall()
        src_map = {int(r[0]): int(r[1]) for r in src_rows}
        dst_map = {int(r[0]): int(r[1]) for r in dst_rows}
        report.add(
            f"users by organization: {src_map} -> {dst_map}",
            src_map == dst_map,
        )

    # Listings by provider/status
    if sqlite_table_exists(sqlite_conn, "property_external_listings"):
        src_rows = sqlite_conn.execute(
            """
            SELECT provider, status, COUNT(*)
            FROM property_external_listings
            GROUP BY provider, status
            ORDER BY provider, status
            """
        ).fetchall()
        dst_rows = pg_conn.execute(
            """
            SELECT provider, status, COUNT(*)
            FROM property_external_listings
            GROUP BY provider, status
            ORDER BY provider, status
            """
        ).fetchall()
        src_map = {
            (r[0], r[1]): int(r[2]) for r in src_rows
        }
        dst_map = {
            (r[0], r[1]): int(r[2]) for r in dst_rows
        }
        report.add(
            f"listings by provider/status: "
            f"{src_map} -> {dst_map}",
            src_map == dst_map,
        )

    return report


def verify_next_identity_insert(pg_conn) -> None:
    """Ensure the next organizations id does not collide."""
    max_id = _scalar(
        pg_conn,
        "SELECT COALESCE(MAX(id), 0) FROM organizations",
    )
    row = pg_conn.execute(
        """
        INSERT INTO organizations (name, is_active)
        VALUES ('__etl_sequence_probe__', 0)
        RETURNING id
        """
    ).fetchone()
    new_id = int(row[0])
    pg_conn.execute(
        "DELETE FROM organizations WHERE id = %s",
        (new_id,),
    )

    expected_min = int(max_id) + 1
    if new_id < expected_min:
        raise EtlError(
            f"Identity probe collided: got id={new_id}, "
            f"expected >= {expected_min}"
        )


@dataclass
class MigrateResult:
    dry_run: bool
    table_results: list[TableCopyResult]
    sequence_messages: list[str]
    validation: ValidationReport
    skipped_sqlite_only_columns: dict[str, list[str]] = field(
        default_factory=dict
    )


def run_migration(
    sqlite_path: str,
    postgres_dsn: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    reset_schema: bool = False,
    ensure_schema: bool = True,
    progress: Optional[Callable[[str], None]] = None,
) -> MigrateResult:
    """
    Copy all application data from SQLite to PostgreSQL.

    dry_run: perform inserts + validation inside a transaction, then ROLLBACK.
    force: truncate destination data tables if nonempty.
    reset_schema: DROP SCHEMA public CASCADE then recreate
        (requires force; destructive to destination only).
    """
    log = progress or (lambda _msg: None)

    sqlite_conn = open_sqlite_readonly(sqlite_path)
    try:
        if ensure_schema or reset_schema:
            if reset_schema:
                if not force:
                    raise EtlError(
                        "--reset-schema requires --force"
                    )
                log("Resetting PostgreSQL public schema...")
                with open_postgres(postgres_dsn) as boot:
                    boot.autocommit = True
                    boot.execute(
                        "DROP SCHEMA IF EXISTS public CASCADE"
                    )
                    boot.execute("CREATE SCHEMA public")
                    boot.execute(
                        "GRANT ALL ON SCHEMA public TO PUBLIC"
                    )
            log("Ensuring PostgreSQL schema...")
            ensure_postgres_schema(postgres_dsn)

        pg_conn = open_postgres(postgres_dsn)
        table_results: list[TableCopyResult] = []
        sequence_messages: list[str] = []
        validation = ValidationReport()
        skipped_cols: dict[str, list[str]] = {}

        try:
            # IMPORTANT: pre-checks must not leave an implicit
            # transaction open. With autocommit=False (psycopg
            # default), a SELECT before `with conn.transaction()`
            # starts txn T1; the context manager then only opens a
            # SAVEPOINT. Exiting the CM releases the savepoint but
            # does not COMMIT T1; conn.close() rolls T1 back and
            # all inserted rows disappear after a "successful" run.
            pg_conn.autocommit = True
            nonempty = postgres_has_data(pg_conn)
            if nonempty and not force and not dry_run:
                raise EtlError(
                    "PostgreSQL destination is not empty: "
                    + ", ".join(nonempty)
                    + ". Use --force to TRUNCATE data tables, "
                    "or --reset-schema --force to rebuild schema."
                )

            agents_before = count_rows(pg_conn, "agents")
            log(
                f"Destination agents count before load: "
                f"{agents_before}"
            )

            pg_conn.autocommit = False

            try:
                with pg_conn.transaction():
                    if nonempty and (force or dry_run):
                        log(
                            "Truncating destination data tables"
                            + (
                                " (dry-run; will roll back)..."
                                if dry_run and not force
                                else "..."
                            )
                        )
                        truncate_data_tables(pg_conn)

                    for table in MIGRATION_TABLE_ORDER:
                        if not sqlite_table_exists(
                            sqlite_conn,
                            table,
                        ):
                            table_results.append(
                                TableCopyResult(
                                    table,
                                    0,
                                    0,
                                    True,
                                    "missing in SQLite",
                                )
                            )
                            log(
                                f"{table}: 0 -> 0 "
                                "(absent in SQLite)"
                            )
                            continue

                        src_cols = sqlite_columns(
                            sqlite_conn,
                            table,
                        )
                        dst_cols = set(
                            postgres_columns(pg_conn, table)
                        )
                        columns = [
                            c for c in src_cols if c in dst_cols
                        ]
                        skipped = [
                            c
                            for c in src_cols
                            if c not in dst_cols
                        ]
                        if skipped:
                            skipped_cols[table] = skipped
                            log(
                                f"{table}: skipping SQLite-only "
                                f"columns {skipped}"
                            )

                        if not columns:
                            raise EtlError(
                                f"No overlapping columns "
                                f"for {table}"
                            )

                        deferred = DEFERRED_SELF_FK.get(
                            table,
                            (),
                        )
                        deferred = tuple(
                            c
                            for c in deferred
                            if c in columns
                        )

                        src_count = count_rows(
                            sqlite_conn,
                            table,
                        )
                        log(
                            f"Copying {table} "
                            f"({src_count} rows)..."
                        )
                        inserted = copy_table(
                            sqlite_conn,
                            pg_conn,
                            table,
                            columns=columns,
                            null_deferred=deferred,
                        )
                        if deferred:
                            apply_deferred_self_fks(
                                sqlite_conn,
                                pg_conn,
                                table,
                                columns,
                            )

                        dst_count = count_rows(
                            pg_conn,
                            table,
                        )
                        ok = src_count == dst_count
                        table_results.append(
                            TableCopyResult(
                                table,
                                src_count,
                                dst_count,
                                ok,
                            )
                        )
                        log(
                            f"{table}: sqlite_read={src_count} "
                            f"rows_attempted={inserted} "
                            f"pg_in_txn={dst_count} "
                            f"{'OK' if ok else 'FAIL'}"
                        )
                        if not ok:
                            raise EtlError(
                                f"Row count mismatch on {table}: "
                                f"sqlite={src_count} "
                                f"postgres={dst_count}"
                            )

                    log("Resetting identity sequences...")
                    sequence_messages = (
                        reset_identity_sequences(pg_conn)
                    )
                    for message in sequence_messages:
                        log(message)

                    log("Validating (in transaction)...")
                    validation = validate_migration(
                        sqlite_conn,
                        pg_conn,
                    )
                    for line in validation.lines:
                        log(line)

                    if not validation.passed:
                        raise EtlError("VALIDATION FAILED")

                    if not dry_run:
                        log(
                            "Probing next identity insert..."
                        )
                        verify_next_identity_insert(pg_conn)

                    if dry_run:
                        log(
                            "Dry-run complete — rolling back "
                            "PostgreSQL transaction."
                        )
                        raise _DryRunRollback()
            except _DryRunRollback:
                log(
                    "Dry-run rolled back. Destination data "
                    "unchanged (empty if schema was reset)."
                )
                return MigrateResult(
                    dry_run=True,
                    table_results=table_results,
                    sequence_messages=sequence_messages,
                    validation=validation,
                    skipped_sqlite_only_columns=skipped_cols,
                )

            # Top-level transaction committed on clean exit above.
            # Verify with a fresh autocommit connection so we never
            # mistake uncommitted savepoint data for success.
            log("Post-commit verification with fresh connection...")
            with open_postgres(postgres_dsn) as verify_conn:
                verify_conn.autocommit = True
                post = validate_migration(
                    sqlite_conn,
                    verify_conn,
                )
                for line in post.lines:
                    log(f"post-commit {line}")
                if not post.passed:
                    raise EtlError(
                        "VALIDATION FAILED after COMMIT "
                        "(data not visible on a new connection)"
                    )
                agents_after = count_rows(
                    verify_conn,
                    "agents",
                )
                log(
                    f"Destination agents count after commit: "
                    f"{agents_after}"
                )

            return MigrateResult(
                dry_run=False,
                table_results=table_results,
                sequence_messages=sequence_messages,
                validation=post,
                skipped_sqlite_only_columns=skipped_cols,
            )
        finally:
            pg_conn.close()
    finally:
        sqlite_conn.close()


class _DryRunRollback(Exception):
    """Internal: unwind the destination transaction after a successful dry-run."""