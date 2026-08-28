"""
ARCA Ticket de Acceso cache (multi-worker safe).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .connection import get_connection
from .tenant import require_organization_id


def _now_iso():
    return datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()


def get_cached_ta(cache_key: str):
    from modules.arca.wsaa import TicketAcceso

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT token, sign, expires_at, service, cuit, environment
            FROM arca_ta_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return TicketAcceso(
            token=row[0],
            sign=row[1],
            expires_at=datetime.fromisoformat(
                row[2].replace("Z", "+00:00")
            ),
            service=row[3],
            cuit=row[4],
            environment=row[5],
        )
    finally:
        connection.close()


def store_cached_ta(cache_key: str, ticket) -> None:
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT OR REPLACE INTO arca_ta_cache (
                cache_key,
                token,
                sign,
                expires_at,
                service,
                cuit,
                environment,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                ticket.token,
                ticket.sign,
                ticket.expires_at.isoformat(),
                ticket.service,
                ticket.cuit,
                ticket.environment,
                _now_iso(),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def log_fiscal_event(
    organization_id,
    *,
    invoice_id,
    issuer_key,
    environment,
    event_type,
    result,
    cae=None,
    error_message=None,
    actor_user_id=None,
    metadata=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO invoice_fiscal_events (
                organization_id,
                invoice_id,
                issuer_key,
                environment,
                event_type,
                result,
                cae,
                error_message,
                actor_user_id,
                metadata,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                invoice_id,
                issuer_key,
                environment,
                event_type,
                result,
                cae,
                (error_message or "")[:500] or None,
                actor_user_id,
                metadata,
                _now_iso(),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
