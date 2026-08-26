"""WSGI entry point for production servers such as Gunicorn."""

from modules.config import load_dotenv_file

load_dotenv_file()

from modules.database import create_tables

# Ensure schema on the same SQLite/Postgres target the app will use.
# Critical for existing Railway SQLite volumes that predate
# properties.external_id (CREATE TABLE IF NOT EXISTS does not
# add columns). Skip backup here; releaseCommand/init_db still
# backs up when desired.
create_tables(create_backup=False)

from web_app import app  # noqa: E402
