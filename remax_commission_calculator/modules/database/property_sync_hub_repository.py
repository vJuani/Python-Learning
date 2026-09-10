"""Organization property integrations, sync runs, agent mappings, conflicts."""

from __future__ import annotations

import json
from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import TenantError, assert_agent_in_organization, require_organization_id


PROVIDER_MOCK_NETWORK = "mock_network"

STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"
STATUS_SYNCING = "syncing"

RUN_RUNNING = "running"
RUN_OK = "ok"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"

CONFLICT_OPEN = "open"
CONFLICT_LINKED = "linked"
CONFLICT_CREATED = "created_new"
CONFLICT_DISMISSED = "dismissed"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _parse_json(raw, default=None):
    if raw in (None, ""):
        return {} if default is None else default
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {} if default is None else default
    return data if isinstance(data, dict) else ({} if default is None else default)


def _integration_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "provider": row[2],
        "status": row[3],
        "sync_enabled": bool(row[4]),
        "last_sync_at": row[5],
        "last_success_at": row[6],
        "last_error": row[7],
        "config": _parse_json(row[8]),
        "created_at": row[9],
        "updated_at": row[10],
    }


INTEGRATION_SELECT = """
    SELECT id, organization_id, provider, status, sync_enabled,
           last_sync_at, last_success_at, last_error, config_json,
           created_at, updated_at
    FROM organization_property_integrations
"""


def get_property_integration(organization_id, provider):
    organization_id = require_organization_id(organization_id)
    provider = str(provider or "").strip()
    connection = get_connection()
    try:
        row = connection.execute(
            INTEGRATION_SELECT
            + " WHERE organization_id = ? AND provider = ?",
            (organization_id, provider),
        ).fetchone()
    finally:
        connection.close()
    return _integration_dict(row)


def list_property_integrations(organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            INTEGRATION_SELECT
            + " WHERE organization_id = ? ORDER BY id",
            (organization_id,),
        ).fetchall()
    finally:
        connection.close()
    return [_integration_dict(row) for row in rows]


def upsert_property_integration(
    organization_id,
    provider,
    *,
    status=STATUS_CONNECTED,
    sync_enabled=True,
    config=None,
):
    organization_id = require_organization_id(organization_id)
    provider = str(provider or "").strip()
    existing = get_property_integration(organization_id, provider)
    now = _now_iso()
    config_json = json.dumps(config) if config is not None else None
    connection = get_connection()
    try:
        if existing:
            connection.execute(
                """
                UPDATE organization_property_integrations
                SET status = ?,
                    sync_enabled = ?,
                    config_json = COALESCE(?, config_json),
                    updated_at = ?
                WHERE id = ? AND organization_id = ?
                """,
                (
                    status,
                    1 if sync_enabled else 0,
                    config_json,
                    now,
                    existing["id"],
                    organization_id,
                ),
            )
            connection.commit()
        else:
            execute_insert(
                connection.cursor(),
                """
                INSERT INTO organization_property_integrations (
                    organization_id, provider, status, sync_enabled,
                    config_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    provider,
                    status,
                    1 if sync_enabled else 0,
                    config_json or "{}",
                    now,
                    now,
                ),
            )
            connection.commit()
    finally:
        connection.close()
    return get_property_integration(organization_id, provider)


def try_begin_sync(organization_id, provider):
    """Claim the integration lock. Returns integration or None if busy."""
    organization_id = require_organization_id(organization_id)
    integration = get_property_integration(organization_id, provider)
    if integration is None:
        return None
    if integration["status"] == STATUS_SYNCING:
        return None
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE organization_property_integrations
            SET status = ?, updated_at = ?
            WHERE id = ? AND organization_id = ? AND status != ?
            """,
            (
                STATUS_SYNCING,
                _now_iso(),
                integration["id"],
                organization_id,
                STATUS_SYNCING,
            ),
        )
        connection.commit()
        if cursor.rowcount == 0:
            return None
    finally:
        connection.close()
    return get_property_integration(organization_id, provider)


def finish_integration_state(
    organization_id,
    provider,
    *,
    status,
    last_error=None,
    success=False,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    try:
        if success:
            connection.execute(
                """
                UPDATE organization_property_integrations
                SET status = ?, last_sync_at = ?, last_success_at = ?,
                    last_error = ?, updated_at = ?
                WHERE organization_id = ? AND provider = ?
                """,
                (status, now, now, last_error, now, organization_id, provider),
            )
        else:
            connection.execute(
                """
                UPDATE organization_property_integrations
                SET status = ?, last_sync_at = ?, last_error = ?, updated_at = ?
                WHERE organization_id = ? AND provider = ?
                """,
                (status, now, last_error, now, organization_id, provider),
            )
        connection.commit()
    finally:
        connection.close()


def start_property_sync_run(organization_id, integration_id, provider):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        run_id = execute_insert(
            connection.cursor(),
            """
            INSERT INTO property_sync_runs (
                organization_id, integration_id, provider, started_at, status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (organization_id, integration_id, provider, _now_iso(), RUN_RUNNING),
        )
        connection.commit()
    finally:
        connection.close()
    return run_id


def set_integration_status(
    organization_id,
    provider,
    *,
    status,
    last_error=None,
    config_updates=None,
):
    """Update status/error/config without treating the change as a sync."""
    organization_id = require_organization_id(organization_id)
    existing = get_property_integration(organization_id, provider)
    if existing is None:
        return None
    merged = dict(existing.get("config") or {})
    if config_updates:
        merged.update(config_updates)
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE organization_property_integrations
            SET status = ?,
                last_error = ?,
                config_json = ?,
                updated_at = ?
            WHERE organization_id = ? AND provider = ?
            """,
            (
                status,
                last_error,
                json.dumps(merged),
                _now_iso(),
                organization_id,
                provider,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_property_integration(organization_id, provider)


def finish_property_sync_run(
    run_id,
    organization_id,
    *,
    status,
    created_count=0,
    updated_count=0,
    unchanged_count=0,
    warning_count=0,
    failed_count=0,
    conflict_count=0,
    error_summary=None,
    extra_stats=None,
):
    organization_id = require_organization_id(organization_id)
    stats = {
        "created": created_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "warnings": warning_count,
        "failed": failed_count,
        "conflicts": conflict_count,
    }
    if extra_stats:
        stats.update(extra_stats)
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE property_sync_runs
            SET finished_at = ?,
                status = ?,
                created_count = ?,
                updated_count = ?,
                unchanged_count = ?,
                warning_count = ?,
                failed_count = ?,
                conflict_count = ?,
                error_summary = ?,
                stats_json = ?
            WHERE id = ? AND organization_id = ?
            """,
            (
                _now_iso(),
                status,
                created_count,
                updated_count,
                unchanged_count,
                warning_count,
                failed_count,
                conflict_count,
                error_summary,
                json.dumps(stats),
                run_id,
                organization_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_property_sync_run(run_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, integration_id, provider, started_at,
                   finished_at, status, created_count, updated_count,
                   unchanged_count, warning_count, failed_count,
                   conflict_count, error_summary, stats_json
            FROM property_sync_runs
            WHERE id = ? AND organization_id = ?
            """,
            (run_id, organization_id),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "integration_id": row[2],
        "provider": row[3],
        "started_at": row[4],
        "finished_at": row[5],
        "status": row[6],
        "created": row[7],
        "updated": row[8],
        "unchanged": row[9],
        "warnings": row[10],
        "failed": row[11],
        "conflicts": row[12],
        "error_summary": row[13],
        "stats": _parse_json(row[14]),
    }


def add_sync_run_item(
    organization_id,
    run_id,
    *,
    external_id,
    outcome,
    property_id=None,
    message=None,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        execute_insert(
            connection.cursor(),
            """
            INSERT INTO property_sync_run_items (
                organization_id, run_id, external_id, property_id, outcome, message
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                run_id,
                str(external_id or "").strip() or None,
                property_id,
                outcome,
                message,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def count_synced_properties(organization_id, provider):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM properties
            WHERE organization_id = ?
              AND external_source = ?
            """,
            (organization_id, provider),
        ).fetchone()
    finally:
        connection.close()
    return int(row[0] if row else 0)


def _mapping_dict(row):
    if row is None:
        return None
    metadata = _parse_json(row[7] if len(row) > 7 else None, default={})
    return {
        "id": row[0],
        "organization_id": row[1],
        "source": row[2],
        "external_agent_id": row[3],
        "agent_id": row[4],
        "created_at": row[5] if len(row) > 5 else None,
        "updated_at": row[6] if len(row) > 6 else None,
        "external_metadata": metadata if isinstance(metadata, dict) else {},
        "agent_name": row[8] if len(row) > 8 else None,
    }


def get_external_agent_mapping(organization_id, source, external_agent_id):
    organization_id = require_organization_id(organization_id)
    source = str(source or "").strip()
    identity = str(external_agent_id or "").strip()
    if not source or not identity:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT m.id, m.organization_id, m.source, m.external_agent_id, m.agent_id,
                   m.created_at, m.updated_at, m.external_metadata_json, a.name
            FROM external_agent_mappings AS m
            LEFT JOIN agents AS a
                ON a.id = m.agent_id
                AND a.organization_id = m.organization_id
            WHERE m.organization_id = ? AND m.source = ? AND m.external_agent_id = ?
            """,
            (organization_id, source, identity),
        ).fetchone()
    finally:
        connection.close()
    return _mapping_dict(row)


def list_external_agent_mappings(organization_id, source=None):
    organization_id = require_organization_id(organization_id)
    source = str(source or "").strip()
    connection = get_connection()
    try:
        sql = """
            SELECT m.id, m.organization_id, m.source, m.external_agent_id, m.agent_id,
                   m.created_at, m.updated_at, m.external_metadata_json, a.name
            FROM external_agent_mappings AS m
            LEFT JOIN agents AS a
                ON a.id = m.agent_id
                AND a.organization_id = m.organization_id
            WHERE m.organization_id = ?
        """
        params = [organization_id]
        if source:
            sql += " AND m.source = ?"
            params.append(source)
        sql += " ORDER BY m.external_agent_id"
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [_mapping_dict(row) for row in rows]


def upsert_external_agent_mapping(
    organization_id,
    source,
    external_agent_id,
    agent_id,
    *,
    metadata=None,
):
    organization_id = require_organization_id(organization_id)
    source = str(source or "").strip()
    identity = str(external_agent_id or "").strip()
    if not source or not identity:
        raise ValueError("external agent identity required")
    payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True) if isinstance(metadata, dict) else None
    now = _now_iso()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        assert_agent_in_organization(cursor, agent_id, organization_id)
        existing = get_external_agent_mapping(organization_id, source, identity)
        if existing:
            if payload is None:
                cursor.execute(
                    """
                    UPDATE external_agent_mappings
                    SET agent_id = ?, updated_at = ?
                    WHERE id = ? AND organization_id = ?
                    """,
                    (agent_id, now, existing["id"], organization_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE external_agent_mappings
                    SET agent_id = ?, updated_at = ?, external_metadata_json = ?
                    WHERE id = ? AND organization_id = ?
                    """,
                    (agent_id, now, payload, existing["id"], organization_id),
                )
        else:
            execute_insert(
                cursor,
                """
                INSERT INTO external_agent_mappings (
                    organization_id, source, external_agent_id, agent_id,
                    created_at, updated_at, external_metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (organization_id, source, identity, agent_id, now, now, payload),
            )
        connection.commit()
    finally:
        connection.close()
    return get_external_agent_mapping(organization_id, source, identity)


def delete_external_agent_mapping(organization_id, source, external_agent_id):
    organization_id = require_organization_id(organization_id)
    source = str(source or "").strip()
    identity = str(external_agent_id or "").strip()
    if not source or not identity:
        return None
    existing = get_external_agent_mapping(organization_id, source, identity)
    if existing is None:
        return None
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE FROM external_agent_mappings
            WHERE id = ? AND organization_id = ?
            """,
            (existing["id"], organization_id),
        )
        connection.commit()
    finally:
        connection.close()
    return existing


def find_open_conflict(organization_id, provider, external_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, provider, external_id, existing_property_id,
                   address_external, address_existing, status, created_at,
                   payload_snapshot_json
            FROM property_sync_conflicts
            WHERE organization_id = ? AND provider = ? AND external_id = ?
              AND status = ?
            """,
            (organization_id, provider, str(external_id), CONFLICT_OPEN),
        ).fetchone()
    finally:
        connection.close()
    return _conflict_dict(row)


def list_open_conflicts(organization_id, provider=None):
    organization_id = require_organization_id(organization_id)
    sql = """
        SELECT id, organization_id, provider, external_id, existing_property_id,
               address_external, address_existing, status, created_at,
               payload_snapshot_json
        FROM property_sync_conflicts
        WHERE organization_id = ? AND status = ?
    """
    params = [organization_id, CONFLICT_OPEN]
    if provider:
        sql += " AND provider = ?"
        params.append(provider)
    sql += " ORDER BY id"
    connection = get_connection()
    try:
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [_conflict_dict(row) for row in rows]


def add_sync_conflict(
    organization_id,
    provider,
    external_id,
    *,
    existing_property_id,
    address_external,
    address_existing,
    payload_snapshot=None,
):
    organization_id = require_organization_id(organization_id)
    existing = find_open_conflict(organization_id, provider, external_id)
    if existing:
        return existing
    snapshot_json = None
    if isinstance(payload_snapshot, dict) and payload_snapshot:
        snapshot_json = json.dumps(payload_snapshot, ensure_ascii=False, default=str)
    connection = get_connection()
    try:
        execute_insert(
            connection.cursor(),
            """
            INSERT INTO property_sync_conflicts (
                organization_id, provider, external_id, existing_property_id,
                address_external, address_existing, status, created_at,
                payload_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                provider,
                str(external_id),
                existing_property_id,
                address_external,
                address_existing,
                CONFLICT_OPEN,
                _now_iso(),
                snapshot_json,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return find_open_conflict(organization_id, provider, external_id)


def resolve_sync_conflict(conflict_id, organization_id, status):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE property_sync_conflicts
            SET status = ?, resolved_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            (status, _now_iso(), conflict_id, organization_id),
        )
        if cursor.rowcount == 0:
            raise TenantError("Conflict not found in organization.")
        connection.commit()
    finally:
        connection.close()


def get_sync_conflict(conflict_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, provider, external_id, existing_property_id,
                   address_external, address_existing, status, created_at,
                   payload_snapshot_json
            FROM property_sync_conflicts
            WHERE id = ? AND organization_id = ?
            """,
            (conflict_id, organization_id),
        ).fetchone()
    finally:
        connection.close()
    return _conflict_dict(row)


def _conflict_dict(row):
    if row is None:
        return None
    snapshot = None
    if len(row) > 9:
        snapshot = _parse_json(row[9], default={}) or None
        if snapshot == {}:
            snapshot = None
    return {
        "id": row[0],
        "organization_id": row[1],
        "provider": row[2],
        "external_id": row[3],
        "existing_property_id": row[4],
        "address_external": row[5],
        "address_existing": row[6],
        "status": row[7],
        "created_at": row[8],
        "payload_snapshot": snapshot,
    }


def find_manual_property_by_address(organization_id, address):
    """Possible-duplicate hint only. Never used as upsert identity."""
    organization_id = require_organization_id(organization_id)
    needle = _normalize_address(address)
    if not needle:
        return None
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, address, external_source, external_id
            FROM properties
            WHERE organization_id = ?
            """,
            (organization_id,),
        ).fetchall()
    finally:
        connection.close()
    for row in rows:
        if _normalize_address(row[1]) != needle:
            continue
        source = (row[2] or "").strip()
        if source:
            continue
        return {
            "id": row[0],
            "address": row[1],
            "external_source": row[2],
            "external_id": row[3],
        }
    return None


def _normalize_address(value):
    return " ".join(str(value or "").lower().split())
