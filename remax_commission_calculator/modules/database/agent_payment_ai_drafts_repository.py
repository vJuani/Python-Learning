"""
Repository for AI agent payment drafts (Phase 3A.2).

Drafts hold the extracted receipt data plus the backend
resolution. They never touch balances: confirmation goes
through the manual agent payment service.
"""

from __future__ import annotations

import json
from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


STATUS_PROCESSING = "processing"
STATUS_REVIEW = "review"
STATUS_CONFIRMED = "confirmed"
STATUS_FAILED = "failed"
STATUS_DISCARDED = "discarded"

OPEN_STATUSES = (
    STATUS_PROCESSING,
    STATUS_REVIEW,
    STATUS_FAILED,
)


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _load_json(raw, fallback):
    if not raw:
        return fallback

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


DRAFT_SELECT = """
    SELECT
        id,
        organization_id,
        created_by_user_id,
        status,
        user_context_text,
        attachment_path,
        attachment_hash,
        attachment_content_type,
        attachment_original_name,
        confirm_token,
        idempotency_key,
        agent_id,
        treasury_account_id,
        charge_movement_id,
        confirmed_movement_id,
        confirmed_cash_movement_id,
        error_message_key,
        confidence,
        provider,
        draft_json,
        resolution_json,
        fields_needing_review_json,
        created_at,
        updated_at
    FROM agent_payment_ai_drafts
"""


def _build_draft_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "created_by_user_id": row[2],
        "status": row[3],
        "user_context_text": row[4] or "",
        "attachment_path": row[5],
        "attachment_hash": row[6],
        "attachment_content_type": row[7],
        "attachment_original_name": row[8],
        "confirm_token": row[9],
        "idempotency_key": row[10],
        "agent_id": row[11],
        "treasury_account_id": row[12],
        "charge_movement_id": row[13],
        "confirmed_movement_id": row[14],
        "confirmed_cash_movement_id": row[15],
        "error_message_key": row[16],
        "confidence": row[17],
        "provider": row[18],
        "draft_payload": _load_json(row[19], {}),
        "resolution": _load_json(row[20], {}),
        "fields_needing_review": _load_json(row[21], []),
        "created_at": row[22],
        "updated_at": row[23] or row[22],
    }


def create_agent_payment_ai_draft(
    organization_id,
    *,
    created_by_user_id,
    confirm_token,
    idempotency_key,
    user_context_text="",
    agent_id=None,
    status=STATUS_PROCESSING,
    provider=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        draft_id = execute_insert(
            cursor,
            """
            INSERT INTO agent_payment_ai_drafts (
                organization_id,
                created_by_user_id,
                status,
                user_context_text,
                confirm_token,
                idempotency_key,
                agent_id,
                provider,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                created_by_user_id,
                status,
                user_context_text or "",
                confirm_token,
                idempotency_key,
                agent_id,
                provider,
                now,
                now,
            ),
        )
        connection.commit()
        return draft_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_agent_payment_ai_draft(draft_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            DRAFT_SELECT
            + """
            WHERE id = ?
                AND organization_id = ?
            """,
            (draft_id, organization_id),
        )
        return _build_draft_dict(cursor.fetchone())
    finally:
        connection.close()


def list_agent_payment_ai_drafts(
    organization_id,
    *,
    agent_id=None,
    statuses=None,
    limit=20,
):
    organization_id = require_organization_id(
        organization_id
    )
    clauses = ["organization_id = ?"]
    params = [organization_id]

    if agent_id is not None:
        clauses.append("agent_id = ?")
        params.append(agent_id)

    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)

    limit_value = int(limit) if limit is not None else 20
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            DRAFT_SELECT
            + f"""
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit_value),
        )
        return [
            _build_draft_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


_UNSET = object()


def update_agent_payment_ai_draft(
    draft_id,
    organization_id,
    *,
    status=None,
    draft_payload=None,
    resolution=None,
    fields_needing_review=None,
    confidence=None,
    error_message_key=None,
    agent_id=_UNSET,
    treasury_account_id=_UNSET,
    charge_movement_id=_UNSET,
    confirmed_movement_id=None,
    confirmed_cash_movement_id=None,
    attachment_path=None,
    attachment_hash=None,
    attachment_content_type=None,
    attachment_original_name=None,
    provider=None,
):
    """
    Partial update. Selection columns accept an explicit
    ``None`` to clear them, so they use a sentinel default.
    """
    organization_id = require_organization_id(
        organization_id
    )
    clauses = ["updated_at = ?"]
    params = [_now_iso()]

    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    if draft_payload is not None:
        clauses.append("draft_json = ?")
        params.append(json.dumps(draft_payload))

    if resolution is not None:
        clauses.append("resolution_json = ?")
        params.append(json.dumps(resolution))

    if fields_needing_review is not None:
        clauses.append("fields_needing_review_json = ?")
        params.append(json.dumps(fields_needing_review))

    if confidence is not None:
        clauses.append("confidence = ?")
        params.append(confidence)

    if error_message_key is not None:
        clauses.append("error_message_key = ?")
        params.append(error_message_key)

    if agent_id is not _UNSET:
        clauses.append("agent_id = ?")
        params.append(agent_id)

    if treasury_account_id is not _UNSET:
        clauses.append("treasury_account_id = ?")
        params.append(treasury_account_id)

    if charge_movement_id is not _UNSET:
        clauses.append("charge_movement_id = ?")
        params.append(charge_movement_id)

    if confirmed_movement_id is not None:
        clauses.append("confirmed_movement_id = ?")
        params.append(confirmed_movement_id)

    if confirmed_cash_movement_id is not None:
        clauses.append("confirmed_cash_movement_id = ?")
        params.append(confirmed_cash_movement_id)

    if attachment_path is not None:
        clauses.append("attachment_path = ?")
        params.append(attachment_path)

    if attachment_hash is not None:
        clauses.append("attachment_hash = ?")
        params.append(attachment_hash)

    if attachment_content_type is not None:
        clauses.append("attachment_content_type = ?")
        params.append(attachment_content_type)

    if attachment_original_name is not None:
        clauses.append("attachment_original_name = ?")
        params.append(attachment_original_name)

    if provider is not None:
        clauses.append("provider = ?")
        params.append(provider)

    params.extend([draft_id, organization_id])
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            UPDATE agent_payment_ai_drafts
            SET {", ".join(clauses)}
            WHERE id = ?
                AND organization_id = ?
            """,
            params,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_agent_payment_ai_draft_for_payment(
    organization_id,
    payment_movement_id,
):
    """Reverse link used to show the receipt from a payment."""
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            DRAFT_SELECT
            + """
            WHERE organization_id = ?
                AND confirmed_movement_id = ?
            ORDER BY id DESC
            """,
            (organization_id, payment_movement_id),
        )
        return _build_draft_dict(cursor.fetchone())
    finally:
        connection.close()
