"""
Database connection layer with SQLite / PostgreSQL backends.

Phase 1: dual connection + placeholder adaptation + insert helpers.
Repositories keep `?` placeholders; adaptation happens here when needed.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Optional, Sequence

from modules.config import (
    BACKEND_POSTGRES,
    BACKEND_SQLITE,
    get_database_backend,
    get_database_path,
    get_database_url,
)

try:
    import psycopg
except ImportError:  # pragma: no cover - optional until Postgres cutover
    psycopg = None


# Unified integrity error for `except IntegrityError`.
# Tuple form works with `except IntegrityError` in Python.
_INTEGRITY_TYPES = [sqlite3.IntegrityError]

if psycopg is not None:
    _INTEGRITY_TYPES.append(psycopg.IntegrityError)

IntegrityError = tuple(_INTEGRITY_TYPES)


_RETURNING_RE = re.compile(
    r"\breturning\b",
    re.IGNORECASE,
)


def adapt_sql(
    sql: str,
    backend: Optional[str] = None,
) -> str:
    """
    Adapt SQL placeholders for the active backend.

    Repositories author queries with ``?`` (SQLite style).
    On PostgreSQL those become ``%s``. Placeholders inside
    single- or double-quoted string literals are left alone.
    Outside literals, lone ``%`` become ``%%`` so psycopg
    does not treat them as format markers.
    """
    if backend is None:
        backend = get_database_backend()

    if backend != BACKEND_POSTGRES:
        return sql

    return _adapt_sql_for_postgres(sql)


def _adapt_sql_for_postgres(sql: str) -> str:
    result: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False

    while i < n:
        char = sql[i]

        if char == "'" and not in_double:
            if (
                in_single
                and i + 1 < n
                and sql[i + 1] == "'"
            ):
                result.append("''")
                i += 2
                continue

            in_single = not in_single
            result.append(char)
            i += 1
            continue

        if char == '"' and not in_single:
            in_double = not in_double
            result.append(char)
            i += 1
            continue

        if not in_single and not in_double:
            if char == "?":
                result.append("%s")
                i += 1
                continue

            if char == "%":
                result.append("%%")
                i += 1
                continue

        result.append(char)
        i += 1

    return "".join(result)


def execute(
    cursor,
    sql: str,
    params: Optional[Sequence[Any]] = None,
):
    """Execute SQL with backend-aware placeholder adaptation."""
    return cursor.execute(
        adapt_sql(sql),
        params if params is not None else (),
    )


def execute_insert(
    cursor,
    sql: str,
    params: Optional[Sequence[Any]] = None,
):
    """
    Execute an INSERT and return the generated primary key.

    SQLite: uses ``cursor.lastrowid`` after insert.
    PostgreSQL: appends ``RETURNING id`` when missing and
    returns that value.
    """
    params = params if params is not None else ()
    backend = get_database_backend()

    if backend == BACKEND_POSTGRES:
        sql_out = sql.rstrip().rstrip(";")

        if not _RETURNING_RE.search(sql_out):
            sql_out = f"{sql_out} RETURNING id"

        cursor.execute(sql_out, params)
        row = cursor.fetchone()

        if row is None:
            raise RuntimeError(
                "INSERT RETURNING id returned no row"
            )

        return row[0]

    cursor.execute(sql, params)
    return cursor.lastrowid


class AdaptingCursor:
    """Cursor proxy that adapts placeholders on execute."""

    def __init__(self, cursor, backend: str):
        self._cursor = cursor
        self._backend = backend

    def execute(
        self,
        sql: str,
        parameters: Optional[Sequence[Any]] = None,
    ):
        adapted = adapt_sql(sql, self._backend)

        if parameters is None:
            return self._cursor.execute(adapted)

        return self._cursor.execute(adapted, parameters)

    def executemany(
        self,
        sql: str,
        parameters_seq,
    ):
        adapted = adapt_sql(sql, self._backend)
        return self._cursor.executemany(
            adapted,
            parameters_seq,
        )

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class AdaptingConnection:
    """Connection proxy with adapting cursors."""

    def __init__(self, connection, backend: str):
        self._connection = connection
        self._backend = backend

    def cursor(self, *args, **kwargs):
        return AdaptingCursor(
            self._connection.cursor(*args, **kwargs),
            self._backend,
        )

    def execute(
        self,
        sql: str,
        parameters: Optional[Sequence[Any]] = None,
    ):
        adapted = adapt_sql(sql, self._backend)

        if parameters is None:
            return self._connection.execute(adapted)

        return self._connection.execute(
            adapted,
            parameters,
        )

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._connection.__exit__(
            exc_type,
            exc,
            tb,
        )


def get_connection():
    """
    Open a DB connection for the configured backend.

    - ``DATABASE_URL`` set → PostgreSQL (psycopg3)
    - otherwise → SQLite via ``DATABASE_PATH`` / default file
    """
    backend = get_database_backend()

    if backend == BACKEND_POSTGRES:
        if psycopg is None:
            raise RuntimeError(
                "DATABASE_URL is set but psycopg is not "
                "installed. Install psycopg[binary]."
            )

        database_url = get_database_url()
        raw = psycopg.connect(database_url)
        return AdaptingConnection(raw, backend)

    raw = sqlite3.connect(get_database_path())
    raw.execute("PRAGMA foreign_keys = ON")
    return AdaptingConnection(raw, backend)


__all__ = [
    "AdaptingConnection",
    "AdaptingCursor",
    "IntegrityError",
    "adapt_sql",
    "execute",
    "execute_insert",
    "get_connection",
    "get_database_backend",
    "get_database_path",
    "get_database_url",
]
