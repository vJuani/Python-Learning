"""
Organization integrations repository.
"""

from __future__ import annotations

import json
from datetime import datetime

from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import (
    TenantError,
    assert_agent_in_organization,
    require_organization_id,
)


PROVIDER_REMAX = "remax"
PROVIDER_ORGANIZATION_WEBSITE = "organization_website"
PROVIDER_STUB_FIXTURE = "stub_fixture"
PROVIDER_CSV_UPLOAD = "csv_upload"

INTEGRATION_PROVIDERS = (
    PROVIDER_REMAX,
    PROVIDER_ORGANIZATION_WEBSITE,
    PROVIDER_STUB_FIXTURE,
    PROVIDER_CSV_UPLOAD,
)

SCOPE_ORGANIZATION = "organization"
SCOPE_AGENT = "agent"

STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"

SYNC_STATUS_OK = "ok"
SYNC_STATUS_PARTIAL = "partial"
SYNC_STATUS_FAILED = "failed"


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _parse_config(raw):
    if raw is None or raw == "":
        return {}

    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}

    if not isinstance(data, dict):
        return {}

    return data


def _build_integration_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "provider": row[2],
        "scope_type": row[3],
        "agent_id": row[4],
        "status": row[5],
        "external_office_id": row[6],
        "config": _parse_config(row[7]),
        "config_json": row[7],
        "last_synced_at": row[8],
        "last_sync_status": row[9],
        "last_sync_error": row[10],
        "created_at": row[11],
        "updated_at": row[12],
    }


INTEGRATIONS_BASE_QUERY = """
    SELECT
        id,
        organization_id,
        provider,
        scope_type,
        agent_id,
        status,
        external_office_id,
        config_json,
        last_synced_at,
        last_sync_status,
        last_sync_error,
        created_at,
        updated_at
    FROM organization_integrations
"""


def get_organization_integration(
    integration_id,
    organization_id,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        INTEGRATIONS_BASE_QUERY
        + """
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            integration_id,
            organization_id,
        ),
    )

    row = cursor.fetchone()
    connection.close()

    return _build_integration_dict(row)


def list_organization_integrations(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        INTEGRATIONS_BASE_QUERY
        + """
        WHERE organization_id = ?
        ORDER BY id
        """,
        (organization_id,),
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        _build_integration_dict(row)
        for row in rows
    ]


def create_organization_integration(
    organization_id,
    provider,
    scope_type,
    *,
    agent_id=None,
    status=STATUS_CONNECTED,
    external_office_id=None,
    config=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    if provider not in INTEGRATION_PROVIDERS:
        raise ValueError("invalid_integration_provider")

    if scope_type == SCOPE_ORGANIZATION:
        if agent_id is not None:
            raise ValueError(
                "organization_scope_requires_null_agent"
            )
    elif scope_type == SCOPE_AGENT:
        if agent_id is None:
            raise ValueError(
                "agent_scope_requires_agent_id"
            )
    else:
        raise ValueError("invalid_integration_scope")

    now = _now_iso()
    config_json = None

    if config is not None:
        config_json = json.dumps(config)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        if agent_id is not None:
            assert_agent_in_organization(
                cursor,
                agent_id,
                organization_id,
            )

        integration_id = execute_insert(
            cursor,
            """
            INSERT INTO organization_integrations (
                organization_id,
                provider,
                scope_type,
                agent_id,
                status,
                external_office_id,
                config_json,
                last_synced_at,
                last_sync_status,
                last_sync_error,
                created_at,
                updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?
            )
            """,
            (
                organization_id,
                provider,
                scope_type,
                agent_id,
                status,
                external_office_id,
                config_json,
                now,
                now,
            ),
        )
        connection.commit()

    except TenantError:
        connection.rollback()
        raise

    finally:
        connection.close()

    return get_organization_integration(
        integration_id,
        organization_id,
    )


def find_organization_integration_by_provider(
    organization_id,
    provider,
    *,
    scope_type=SCOPE_ORGANIZATION,
    agent_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    if scope_type == SCOPE_ORGANIZATION:
        cursor.execute(
            INTEGRATIONS_BASE_QUERY
            + """
            WHERE organization_id = ?
                AND provider = ?
                AND scope_type = ?
            """,
            (
                organization_id,
                provider,
                SCOPE_ORGANIZATION,
            ),
        )
    else:
        cursor.execute(
            INTEGRATIONS_BASE_QUERY
            + """
            WHERE organization_id = ?
                AND provider = ?
                AND scope_type = ?
                AND agent_id = ?
            """,
            (
                organization_id,
                provider,
                SCOPE_AGENT,
                agent_id,
            ),
        )

    row = cursor.fetchone()
    connection.close()

    return _build_integration_dict(row)


def update_organization_integration_config(
    integration_id,
    organization_id,
    config,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    config_json = json.dumps(config)

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE organization_integrations
        SET
            config_json = ?,
            updated_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            config_json,
            now,
            integration_id,
            organization_id,
        ),
    )

    updated = cursor.rowcount > 0
    connection.commit()
    connection.close()

    if not updated:
        return None

    return get_organization_integration(
        integration_id,
        organization_id,
    )


def update_integration_sync_state(
    integration_id,
    organization_id,
    *,
    last_synced_at,
    last_sync_status,
    last_sync_error=None,
    status=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()

    if status is None:
        cursor.execute(
            """
            UPDATE organization_integrations
            SET
                last_synced_at = ?,
                last_sync_status = ?,
                last_sync_error = ?,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                last_synced_at,
                last_sync_status,
                last_sync_error,
                now,
                integration_id,
                organization_id,
            ),
        )
    else:
        cursor.execute(
            """
            UPDATE organization_integrations
            SET
                last_synced_at = ?,
                last_sync_status = ?,
                last_sync_error = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                last_synced_at,
                last_sync_status,
                last_sync_error,
                status,
                now,
                integration_id,
                organization_id,
            ),
        )

    updated = cursor.rowcount > 0
    connection.commit()
    connection.close()

    return updated


def start_integration_sync_run(
    organization_id,
    integration_id,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM organization_integrations
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            integration_id,
            organization_id,
        ),
    )

    if cursor.fetchone() is None:
        connection.close()
        raise TenantError(
            "Integration was not found in this organization."
        )

    run_id = execute_insert(
        cursor,
        """
        INSERT INTO integration_sync_runs (
            organization_id,
            integration_id,
            started_at,
            finished_at,
            status,
            agents_created,
            agents_updated,
            properties_created,
            properties_updated,
            listings_created,
            listings_updated,
            listings_deactivated,
            error_summary
        ) VALUES (
            ?, ?, ?, NULL, 'running', 0, 0, 0, 0, 0, 0, 0, NULL
        )
        """,
        (
            organization_id,
            integration_id,
            now,
        ),
    )
    connection.commit()
    connection.close()

    return run_id


def finish_integration_sync_run(
    run_id,
    organization_id,
    *,
    status,
    agents_created=0,
    agents_updated=0,
    properties_created=0,
    properties_updated=0,
    listings_created=0,
    listings_updated=0,
    listings_deactivated=0,
    error_summary=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE integration_sync_runs
        SET
            finished_at = ?,
            status = ?,
            agents_created = ?,
            agents_updated = ?,
            properties_created = ?,
            properties_updated = ?,
            listings_created = ?,
            listings_updated = ?,
            listings_deactivated = ?,
            error_summary = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            now,
            status,
            agents_created,
            agents_updated,
            properties_created,
            properties_updated,
            listings_created,
            listings_updated,
            listings_deactivated,
            error_summary,
            run_id,
            organization_id,
        ),
    )

    updated = cursor.rowcount > 0
    connection.commit()
    connection.close()

    return updated


def get_integration_sync_run(run_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            organization_id,
            integration_id,
            started_at,
            finished_at,
            status,
            agents_created,
            agents_updated,
            properties_created,
            properties_updated,
            listings_created,
            listings_updated,
            listings_deactivated,
            error_summary
        FROM integration_sync_runs
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            run_id,
            organization_id,
        ),
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "integration_id": row[2],
        "started_at": row[3],
        "finished_at": row[4],
        "status": row[5],
        "agents_created": row[6],
        "agents_updated": row[7],
        "properties_created": row[8],
        "properties_updated": row[9],
        "listings_created": row[10],
        "listings_updated": row[11],
        "listings_deactivated": row[12],
        "error_summary": row[13],
    }
