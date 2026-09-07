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


def _parse_optional_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_ta_secret(stored):
    from modules.arca.connections import decrypt_secret

    if not stored:
        return None
    plain = decrypt_secret(stored)
    if plain:
        return plain
    text = str(stored)
    if text.startswith("gAAAA"):
        return None
    return text


def get_cached_ta(cache_key: str):
    from modules.arca.wsaa import TicketAcceso

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT token, sign, expires_at, service, cuit, environment,
                   generation_time
            FROM arca_ta_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        token = _read_ta_secret(row[0])
        sign = _read_ta_secret(row[1])
        if not token or not sign:
            return None
        ticket = TicketAcceso(
            token=token,
            sign=sign,
            expires_at=datetime.fromisoformat(
                row[2].replace("Z", "+00:00")
            ),
            service=row[3],
            cuit=row[4],
            environment=row[5],
            generation_time=_parse_optional_datetime(
                row[6] if len(row) > 6 else None
            ),
        )
        if row[0] == token or row[1] == sign:
            from modules.arca.connections import encrypt_secret

            cursor.execute(
                """
                UPDATE arca_ta_cache
                SET token = ?, sign = ?, updated_at = ?
                WHERE cache_key = ?
                """,
                (
                    encrypt_secret(token),
                    encrypt_secret(sign),
                    _now_iso(),
                    cache_key,
                ),
            )
            connection.commit()
        return ticket
    finally:
        connection.close()


def store_cached_ta(cache_key: str, ticket) -> None:
    from modules.arca.connections import encrypt_secret

    connection = get_connection()
    cursor = connection.cursor()
    now = _now_iso()
    generation = None
    if getattr(ticket, "generation_time", None) is not None:
        generation = ticket.generation_time.isoformat()
    try:
        created_at = now
        cursor.execute(
            """
            SELECT created_at
            FROM arca_ta_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        )
        existing = cursor.fetchone()
        if existing and existing[0]:
            created_at = existing[0]
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
                updated_at,
                generation_time,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                encrypt_secret(ticket.token),
                encrypt_secret(ticket.sign),
                ticket.expires_at.isoformat(),
                ticket.service,
                ticket.cuit,
                ticket.environment,
                now,
                generation,
                created_at,
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
