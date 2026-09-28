"""Shared public-inquiry rate hits. One table, SQLite and Postgres."""

from __future__ import annotations

from modules.config import BACKEND_POSTGRES, get_database_backend

from .connection import get_connection


def migrate_inquiry_rate_limit():
    postgres = get_database_backend() == BACKEND_POSTGRES
    connection = get_connection()
    cursor = connection.cursor()
    if postgres:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS public_inquiry_rate_hits (
                id BIGSERIAL PRIMARY KEY,
                token TEXT NOT NULL,
                hit_at TEXT NOT NULL
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS public_inquiry_rate_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                hit_at TEXT NOT NULL
            )
            """
        )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_inquiry_rate_token
        ON public_inquiry_rate_hits (token)
        """
    )
    connection.commit()
    connection.close()
