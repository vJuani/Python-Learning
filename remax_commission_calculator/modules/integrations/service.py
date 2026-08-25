"""
Public service helpers for integration sync infrastructure.
"""

from __future__ import annotations

from modules.database.csv_import_batches_repository import (
    delete_csv_import_batch,
    get_csv_import_batch,
)
from modules.database.organization_integrations_repository import (
    PROVIDER_STUB_FIXTURE,
    SCOPE_AGENT,
    SCOPE_ORGANIZATION,
    STATUS_CONNECTED,
    create_organization_integration,
    get_organization_integration,
)
from modules.database.tenant import TenantError
from modules.integrations.csv_import import (
    clear_csv_batch_from_integration,
    parse_csv_bytes,
    prepare_csv_integration_for_batch,
    stage_csv_import,
)
from modules.integrations.sync_engine import run_sync


def create_stub_integration(
    organization_id,
    *,
    scope_type,
    fixture_key,
    external_office_id,
    agent_id=None,
):
    if scope_type == SCOPE_ORGANIZATION and agent_id is not None:
        raise ValueError(
            "organization_scope_requires_null_agent"
        )

    if scope_type == SCOPE_AGENT and agent_id is None:
        raise ValueError("agent_scope_requires_agent_id")

    return create_organization_integration(
        organization_id,
        PROVIDER_STUB_FIXTURE,
        scope_type,
        agent_id=agent_id,
        status=STATUS_CONNECTED,
        external_office_id=external_office_id,
        config={"fixture_key": fixture_key},
    )


def run_integration_sync(integration_id, organization_id):
    integration = get_organization_integration(
        integration_id,
        organization_id,
    )

    if integration is None:
        raise TenantError(
            "Integration was not found in this organization."
        )

    return run_sync(integration)


def preview_csv_upload(
    organization_id,
    raw_bytes,
    *,
    filename=None,
):
    parse_result = parse_csv_bytes(
        raw_bytes,
        filename=filename,
    )
    batch = stage_csv_import(organization_id, parse_result)
    return batch


def confirm_csv_upload(organization_id, batch_id):
    batch = get_csv_import_batch(batch_id, organization_id)

    if batch is None:
        raise ValueError("csv_batch_not_found")

    if not batch["preview"].get("can_confirm"):
        raise ValueError("csv_batch_has_blockers")

    integration = prepare_csv_integration_for_batch(
        organization_id,
        batch_id,
    )

    try:
        result = run_integration_sync(
            integration["id"],
            organization_id,
        )
    finally:
        clear_csv_batch_from_integration(
            organization_id,
            integration["id"],
            batch_id,
        )

    return result


def cancel_csv_upload(organization_id, batch_id):
    return delete_csv_import_batch(batch_id, organization_id)
