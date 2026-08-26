"""
Repository for AI cash movement drafts.
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


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _build_draft_dict(row):
    if row is None:
        return None

    fields_needing_review = []
    raw_fields = row[16]

    if raw_fields:
        try:
            fields_needing_review = json.loads(raw_fields)
        except (TypeError, ValueError):
            fields_needing_review = []

    draft_payload = {}
    raw_draft = row[14]

    if raw_draft:
        try:
            draft_payload = json.loads(raw_draft)
        except (TypeError, ValueError):
            draft_payload = {}

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
        "confirmed_movement_id": row[10],
        "error_message_key": row[11],
        "confidence": row[12],
        "provider": row[13],
        "draft_payload": draft_payload,
        "fields_needing_review": fields_needing_review,
        "created_at": row[15],
        "updated_at": row[17] if len(row) > 17 else row[15],
    }


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
        confirmed_movement_id,
        error_message_key,
        confidence,
        provider,
        draft_json,
        created_at,
        fields_needing_review_json,
        updated_at
    FROM cash_ai_drafts
"""


def create_cash_ai_draft(
    organization_id,
    *,
    created_by_user_id,
    confirm_token,
    user_context_text="",
    attachment_path=None,
    attachment_hash=None,
    attachment_content_type=None,
    attachment_original_name=None,
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
            INSERT INTO cash_ai_drafts (
                organization_id,
                created_by_user_id,
                status,
                user_context_text,
                attachment_path,
                attachment_hash,
                attachment_content_type,
                attachment_original_name,
                confirm_token,
                provider,
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                created_by_user_id,
                status,
                user_context_text or "",
                attachment_path,
                attachment_hash,
                attachment_content_type,
                attachment_original_name,
                confirm_token,
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


def get_cash_ai_draft(draft_id, organization_id):
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


def get_cash_ai_draft_by_token(
    organization_id,
    confirm_token,
):
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
                AND confirm_token = ?
            """,
            (organization_id, confirm_token),
        )
        return _build_draft_dict(cursor.fetchone())
    finally:
        connection.close()


def update_cash_ai_draft(
    draft_id,
    organization_id,
    *,
    status=None,
    draft_payload=None,
    fields_needing_review=None,
    confidence=None,
    error_message_key=None,
    confirmed_movement_id=None,
    attachment_path=None,
    attachment_hash=None,
    attachment_content_type=None,
    attachment_original_name=None,
    provider=None,
):
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

    if fields_needing_review is not None:
        clauses.append("fields_needing_review_json = ?")
        params.append(json.dumps(fields_needing_review))

    if confidence is not None:
        clauses.append("confidence = ?")
        params.append(confidence)

    if error_message_key is not None:
        clauses.append("error_message_key = ?")
        params.append(error_message_key)

    if confirmed_movement_id is not None:
        clauses.append("confirmed_movement_id = ?")
        params.append(confirmed_movement_id)

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
            UPDATE cash_ai_drafts
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
