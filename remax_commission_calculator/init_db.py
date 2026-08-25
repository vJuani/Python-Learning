"""
One-shot database/upload bootstrap for deploy release.

Intended to run once before Gunicorn starts (Railway releaseCommand),
not on every WSGI worker import.

Usage:
  python init_db.py
"""

from pathlib import Path

from modules.config import (
    get_database_path,
    get_private_upload_root,
    get_upload_root,
    load_dotenv_file,
)
from modules.database import create_tables


def ensure_storage_dirs():
    database_path = Path(get_database_path()).expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    get_upload_root().mkdir(parents=True, exist_ok=True)
    get_private_upload_root().mkdir(parents=True, exist_ok=True)

    return database_path


def main():
    load_dotenv_file()
    database_path = ensure_storage_dirs()
    create_tables()
    print(
        f"Database ready at {database_path} "
        "(tables ensured)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
