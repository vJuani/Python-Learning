"""
One-shot database/upload bootstrap for deploy release.

Intended to run once before Gunicorn starts (Railway releaseCommand).
Gunicorn/`wsgi.py` also calls `create_tables(create_backup=False)` so an
existing SQLite volume is migrated in the same process that serves traffic.

Usage:
  python init_db.py

Backend selection:
  - DATABASE_URL set → PostgreSQL clean schema (schema_postgres)
  - otherwise → SQLite create_tables + migrate_schema
"""

from pathlib import Path

from modules.config import (
    BACKEND_POSTGRES,
    get_database_backend,
    get_database_path,
    get_database_url,
    get_private_upload_root,
    get_upload_root,
    load_dotenv_file,
)
from modules.database import create_tables


def ensure_storage_dirs():
    """Ensure upload dirs; SQLite also ensures DB parent directory."""
    backend = get_database_backend()

    if backend != BACKEND_POSTGRES:
        database_path = Path(
            get_database_path()
        ).expanduser()
        database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
    else:
        database_path = None

    get_upload_root().mkdir(parents=True, exist_ok=True)
    get_private_upload_root().mkdir(
        parents=True,
        exist_ok=True,
    )

    return database_path


def _mask_database_url(url):
    if not url:
        return None

    # postgresql://user:pass@host/db → postgresql://user:***@host/db
    if "://" not in url:
        return url

    scheme, rest = url.split("://", 1)

    if "@" not in rest:
        return url

    creds, hostpart = rest.rsplit("@", 1)

    if ":" in creds:
        user = creds.split(":", 1)[0]
        creds = f"{user}:***"

    return f"{scheme}://{creds}@{hostpart}"


def main():
    load_dotenv_file()
    database_path = ensure_storage_dirs()
    backend = get_database_backend()
    create_tables()

    if backend == BACKEND_POSTGRES:
        masked = _mask_database_url(get_database_url())
        print(
            f"PostgreSQL schema ready ({masked}). "
            f"Backend={backend}."
        )
    else:
        print(
            f"Database ready at {database_path} "
            f"(tables ensured). Backend={backend}."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
