"""
Public service helpers for integration sync infrastructure.
"""

from __future__ import annotations

from modules.database.agents_repository import get_agent_record
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
from modules.integrations.remax_export import (
    agent_external_id_for_local,
    apply_remax_preview_overrides,
    clear_remax_batch_from_integration,
    parse_remax_export_bytes,
    prepare_remax_integration_for_batch,
    stage_remax_import,
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


def preview_remax_export(
    organization_id,
    raw_bytes,
    *,
    agent_id,
    filename=None,
):
    agent = get_agent_record(agent_id, organization_id)

    if agent is None:
        raise ValueError("remax_agent_not_found")

    parse_result = parse_remax_export_bytes(
        raw_bytes,
        filename=filename,
    )
    parse_result.agent_id = agent["id"]
    parse_result.agent_name = agent["name"]

    return stage_remax_import(
        organization_id,
        parse_result,
        agent_id=agent["id"],
        agent_name=agent["name"],
        agent_external_id=agent_external_id_for_local(agent),
    )


def resolve_remax_export_preview(
    organization_id,
    batch_id,
    overrides,
):
    return apply_remax_preview_overrides(
        organization_id,
        batch_id,
        overrides,
    )


def confirm_remax_export(organization_id, batch_id):
    batch = get_csv_import_batch(batch_id, organization_id)

    if batch is None:
        raise ValueError("csv_batch_not_found")

    if (batch.get("payload") or {}).get("mode") == "remax_catalog":
        raise ValueError("remax_catalog_wrong_mode")

    if not batch["preview"].get("can_confirm"):
        raise ValueError("csv_batch_has_blockers")

    meta = (batch["payload"] or {}).get("meta") or {}
    agent_id = meta.get("agent_id")

    if agent_id is None:
        raise ValueError("remax_agent_required")

    agent = get_agent_record(agent_id, organization_id)

    if agent is None:
        raise ValueError("remax_agent_not_found")

    integration = prepare_remax_integration_for_batch(
        organization_id,
        batch_id,
    )

    try:
        result = run_integration_sync(
            integration["id"],
            organization_id,
        )
    finally:
        clear_remax_batch_from_integration(
            organization_id,
            integration["id"],
            batch_id,
        )

    return result


def cancel_remax_export(organization_id, batch_id):
    return delete_csv_import_batch(batch_id, organization_id)
