"""Persistence for public property links, shortlist collections, and opens."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from modules.database.connection import execute_insert, get_connection
from modules.database.tenant import require_organization_id


STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
KIND_PROPERTY = "property"
KIND_SHORTLIST = "shortlist"


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _link_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "property_id": row[2],
        "token": row[3],
        "status": row[4],
        "created_at": row[5],
        "revoked_at": row[6],
        "created_by_agent_id": row[7],
    }


def _shortlist_dict(row):
    if row is None:
        return None
    raw = row[4] or "[]"
    try:
        property_ids = [int(value) for value in json.loads(raw)]
    except (TypeError, ValueError, json.JSONDecodeError):
        property_ids = []
    return {
        "id": row[0],
        "organization_id": row[1],
        "contact_id": row[2],
        "token": row[3],
        "property_ids": property_ids,
        "status": row[5],
        "expires_at": row[6],
        "created_at": row[7],
        "revoked_at": row[8],
        "created_by_agent_id": row[9],
    }


def get_property_link_by_token(token):
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, property_id, token, status,
                   created_at, revoked_at, created_by_agent_id
            FROM property_public_links
            WHERE token = ?
            """,
            (token,),
        ).fetchone()
    finally:
        connection.close()
    return _link_dict(row)


def get_active_property_link(organization_id, property_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, property_id, token, status,
                   created_at, revoked_at, created_by_agent_id
            FROM property_public_links
            WHERE organization_id = ?
              AND property_id = ?
              AND status = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (organization_id, property_id, STATUS_ACTIVE),
        ).fetchone()
    finally:
        connection.close()
    return _link_dict(row)


def insert_property_link(organization_id, property_id, token, *, agent_id=None):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        link_id = execute_insert(
            connection.cursor(),
            """
            INSERT INTO property_public_links (
                organization_id, property_id, token, status, created_at,
                created_by_agent_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                property_id,
                token,
                STATUS_ACTIVE,
                _now_iso(),
                agent_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_property_link_by_token(token) or {"id": link_id, "token": token}


def revoke_property_links(organization_id, property_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE property_public_links
            SET status = ?, revoked_at = ?
            WHERE organization_id = ?
              AND property_id = ?
              AND status = ?
            """,
            (STATUS_REVOKED, _now_iso(), organization_id, property_id, STATUS_ACTIVE),
        )
        connection.commit()
    finally:
        connection.close()


def get_shortlist_by_token(token):
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, contact_id, token, property_ids_json,
                   status, expires_at, created_at, revoked_at, created_by_agent_id
            FROM public_shortlists
            WHERE token = ?
            """,
            (token,),
        ).fetchone()
    finally:
        connection.close()
    return _shortlist_dict(row)


def insert_shortlist(
    organization_id,
    token,
    property_ids,
    expires_at,
    *,
    contact_id=None,
    agent_id=None,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        execute_insert(
            connection.cursor(),
            """
            INSERT INTO public_shortlists (
                organization_id, contact_id, token, property_ids_json, status,
                expires_at, created_at, created_by_agent_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                contact_id,
                token,
                json.dumps([int(value) for value in property_ids]),
                STATUS_ACTIVE,
                expires_at,
                _now_iso(),
                agent_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_shortlist_by_token(token)


def update_shortlist_properties(organization_id, token, property_ids, expires_at=None):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        if expires_at:
            connection.execute(
                """
                UPDATE public_shortlists
                SET property_ids_json = ?, expires_at = ?
                WHERE organization_id = ? AND token = ? AND status = ?
                """,
                (
                    json.dumps([int(value) for value in property_ids]),
                    expires_at,
                    organization_id,
                    token,
                    STATUS_ACTIVE,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE public_shortlists
                SET property_ids_json = ?
                WHERE organization_id = ? AND token = ? AND status = ?
                """,
                (
                    json.dumps([int(value) for value in property_ids]),
                    organization_id,
                    token,
                    STATUS_ACTIVE,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return get_shortlist_by_token(token)


def revoke_shortlist(organization_id, token):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            UPDATE public_shortlists
            SET status = ?, revoked_at = ?
            WHERE organization_id = ? AND token = ? AND status = ?
            """,
            (STATUS_REVOKED, _now_iso(), organization_id, token, STATUS_ACTIVE),
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def record_open(organization_id, kind, link_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        execute_insert(
            connection.cursor(),
            """
            INSERT INTO public_share_opens (
                organization_id, kind, link_id, opened_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (organization_id, kind, link_id, _now_iso()),
        )
        connection.commit()
    finally:
        connection.close()


def count_opens(organization_id, kind, link_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM public_share_opens
            WHERE organization_id = ? AND kind = ? AND link_id = ?
            """,
            (organization_id, kind, link_id),
        ).fetchone()
    finally:
        connection.close()
    return int(row[0] if row else 0)
