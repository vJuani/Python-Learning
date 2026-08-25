#!/usr/bin/env python3
"""
One-shot SQLite → PostgreSQL data migration (Phase 3).

Does NOT cut over the web app. Does NOT modify the SQLite source.

Examples (from remax_commission_calculator/):

  # Dry-run (loads + validates, then rolls back PG)
  python scripts/migrate_sqlite_to_postgres.py \\
    --sqlite /data/commission.db \\
    --postgres-url \"$TEST_DATABASE_URL\" \\
    --dry-run

  # Real load into an empty disposable Postgres
  python scripts/migrate_sqlite_to_postgres.py \\
    --sqlite /data/commission.db \\
    --postgres-url \"$TEST_DATABASE_URL\"

  # Rebuild destination schema then load (destructive to PG only)
  python scripts/migrate_sqlite_to_postgres.py \\
    --sqlite /data/commission.db \\
    --postgres-url \"$TEST_DATABASE_URL\" \\
    --reset-schema --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from modules.database.etl_sqlite_to_postgres import (  # noqa: E402
    EtlError,
    run_migration,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy SQLite application data into PostgreSQL, "
            "preserving IDs. Source SQLite is never modified."
        )
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        help="Path to source SQLite file (e.g. /data/commission.db)",
    )
    parser.add_argument(
        "--postgres-url",
        required=True,
        help=(
            "Destination PostgreSQL DSN. Use a disposable DB — "
            "not the Railway web service DATABASE_URL."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run full copy + validation inside a transaction, "
            "then roll back PostgreSQL."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow nonempty destination: TRUNCATE data tables "
            "before load (PostgreSQL only)."
        ),
    )
    parser.add_argument(
        "--reset-schema",
        action="store_true",
        help=(
            "DROP SCHEMA public CASCADE and recreate clean PG "
            "schema before load. Requires --force."
        ),
    )
    parser.add_argument(
        "--skip-ensure-schema",
        action="store_true",
        help="Do not call create_postgres_schema() before load.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sqlite_path = Path(args.sqlite).expanduser()

    if not sqlite_path.is_file():
        print(f"ERROR: SQLite file not found: {sqlite_path}")
        return 2

    print(f"Source SQLite: {sqlite_path}")
    print(
        "Destination Postgres: "
        f"{_mask_dsn(args.postgres_url)}"
    )
    print(f"Dry-run: {args.dry_run}")
    print(f"Force: {args.force}")
    print(f"Reset schema: {args.reset_schema}")
    print("---")

    try:
        result = run_migration(
            str(sqlite_path),
            args.postgres_url,
            dry_run=args.dry_run,
            force=args.force,
            reset_schema=args.reset_schema,
            ensure_schema=not args.skip_ensure_schema,
            progress=print,
        )
    except EtlError as error:
        print("---")
        print(f"ERROR: {error}")
        print("VALIDATION FAILED")
        print(
            "PostgreSQL transaction rolled back "
            "(SQLite source untouched)."
        )
        return 1
    except Exception as error:
        print("---")
        print(f"ERROR: {error}")
        print("VALIDATION FAILED")
        print(
            "PostgreSQL changes rolled back if still in "
            "transaction; SQLite source untouched."
        )
        return 1

    print("---")
    for table_result in result.table_results:
        mark = "OK" if table_result.ok else "FAIL"
        print(
            f"{table_result.table}: "
            f"{table_result.source_count} -> "
            f"{table_result.dest_count} {mark}"
        )

    if result.skipped_sqlite_only_columns:
        print("Skipped SQLite-only columns:")
        for table, cols in result.skipped_sqlite_only_columns.items():
            print(f"  {table}: {cols}")

    print("---")
    if result.validation.passed:
        print("VALIDATION PASSED")
        if result.dry_run:
            print(
                "(dry-run: destination left unchanged)"
            )
        return 0

    print("VALIDATION FAILED")
    return 1


def _mask_dsn(url: str) -> str:
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        creds = f"{user}:***"
    return f"{scheme}://{creds}@{host}"


if __name__ == "__main__":
    raise SystemExit(main())
