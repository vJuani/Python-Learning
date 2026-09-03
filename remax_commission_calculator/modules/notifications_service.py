"""
Informational notification events.

A notification is an event addressed to a user and it can be read.
It is NOT a pending action: pending actions are derived on the fly by
``modules.pending_actions``, so marking a notification as read never
resolves the underlying work.

Staff pendings are fully derived from real data, which is why there is
no ``notify_staff``: persisting a copy for every admin would only add
noise that could drift from the source of truth.
"""

import logging

from modules.database.notifications_repository import (
    create_notification
)
from modules.database.users_repository import (
    get_user_by_agent_id
)


logger = logging.getLogger(__name__)


def notify_agent_for_property(
    organization_id,
    agent_id,
    kind,
    property_id,
    payload,
    actor_user_id=None
):
    user = get_user_by_agent_id(
        agent_id,
        organization_id
    )

    if user is None:
        return None

    return create_notification(
        organization_id,
        user["id"],
        kind,
        "property",
        property_id,
        payload=payload,
        actor_user_id=actor_user_id
    )


def notify_agent_for_property_change(
    organization_id,
    agent_id,
    kind,
    change_request_id,
    payload,
    actor_user_id=None
):
    user = get_user_by_agent_id(
        agent_id,
        organization_id
    )

    if user is None:
        return None

    return create_notification(
        organization_id,
        user["id"],
        kind,
        "property_change",
        change_request_id,
        payload=payload,
        actor_user_id=actor_user_id
    )


def notify_agent_for_operation(
    organization_id,
    agent_id,
    kind,
    operation_id,
    payload,
    actor_user_id=None
):
    user = get_user_by_agent_id(
        agent_id,
        organization_id
    )

    if user is None:
        return None

    return create_notification(
        organization_id,
        user["id"],
        kind,
        "operation",
        operation_id,
        payload=payload,
        actor_user_id=actor_user_id
    )


def notify_operation_invoice_amount_ready(
    organization_id,
    agent_id,
    operation_id,
    payload=None,
    actor_user_id=None,
):
    """Notify agent that invoice amount is ready to bill."""
    return notify_agent_for_operation(
        organization_id,
        agent_id,
        "operation_invoice_amount_ready",
        operation_id,
        payload=payload or {},
        actor_user_id=actor_user_id,
    )


def notify_operation_side_ready_to_invoice(
    organization_id,
    agent_id,
    operation_id,
    payload=None,
    actor_user_id=None,
):
    """Notify agent that a buyer/seller side is ready to bill."""
    return notify_agent_for_operation(
        organization_id,
        agent_id,
        "operation_side_ready_to_invoice",
        operation_id,
        payload=payload or {},
        actor_user_id=actor_user_id,
    )


def notify_user(
    organization_id,
    user_id,
    kind,
    entity_type,
    entity_id,
    *,
    payload=None,
    actor_user_id=None,
    event_key=None,
):
    """
    Create one informational event for a user.

    ``event_key`` makes the write idempotent, so a retried job or a
    double POST cannot produce two notifications for the same logical
    event.
    """
    if user_id is None:
        return None

    return create_notification(
        organization_id,
        user_id,
        kind,
        entity_type,
        entity_id,
        payload=payload or {},
        actor_user_id=actor_user_id,
        event_key=event_key,
    )


def notify_agent(
    organization_id,
    agent_id,
    kind,
    entity_type,
    entity_id,
    *,
    payload=None,
    actor_user_id=None,
    event_key=None,
):
    """Same as ``notify_user`` but resolving the agent's user account."""
    if agent_id is None:
        return None

    user = get_user_by_agent_id(agent_id, organization_id)

    if user is None:
        return None

    return notify_user(
        organization_id,
        user["id"],
        kind,
        entity_type,
        entity_id,
        payload=payload,
        actor_user_id=actor_user_id,
        event_key=event_key,
    )


def _safe_notify_agent(organization_id, agent_id, kind, **kwargs):
    """
    Emit an event without ever breaking the caller.

    Notifications are informational, so a failure here must not roll
    back a financial operation that already succeeded.
    """
    try:
        return notify_agent(
            organization_id,
            agent_id,
            kind,
            **kwargs,
        )
    except Exception:
        logger.warning(
            "notification_emit_failed kind=%s org=%s",
            kind,
            organization_id,
            exc_info=True,
        )
        return None


def emit_agent_payment_confirmed(
    organization_id,
    agent_id,
    movement_id,
    *,
    currency,
    amount,
    actor_user_id=None,
):
    """Agent-facing event: their payment was registered."""
    return _safe_notify_agent(
        organization_id,
        agent_id,
        "agent_payment_confirmed",
        entity_type="agent_account_movement",
        entity_id=movement_id,
        payload={
            "currency": currency,
            "amount": amount,
        },
        actor_user_id=actor_user_id,
        event_key=f"agent_payment_confirmed:{movement_id}",
    )


def emit_commission_credited(
    organization_id,
    agent_id,
    movement_id,
    *,
    currency,
    amount,
    operation_reference=None,
    actor_user_id=None,
):
    """Agent-facing event: their commission was credited."""
    return _safe_notify_agent(
        organization_id,
        agent_id,
        "commission_credited",
        entity_type="agent_account_movement",
        entity_id=movement_id,
        payload={
            "currency": currency,
            "amount": amount,
            "operation_reference": operation_reference,
        },
        actor_user_id=actor_user_id,
        event_key=f"commission_credited:{movement_id}",
    )


def emit_invoice_created(
    organization_id,
    agent_id,
    invoice_id,
    *,
    invoice_number_internal=None,
    actor_user_id=None,
):
    """Agent-facing event: an invoice of theirs is available."""
    return _safe_notify_agent(
        organization_id,
        agent_id,
        "invoice_created",
        entity_type="invoice",
        entity_id=invoice_id,
        payload={
            "invoice_number_internal": invoice_number_internal,
        },
        actor_user_id=actor_user_id,
        event_key=f"invoice_created:{invoice_id}",
    )


def emit_recurring_charge_generated(
    organization_id,
    agent_id,
    movement_id,
    *,
    currency,
    amount,
    period_label=None,
):
    """Agent-facing event: a recurring charge was generated."""
    return _safe_notify_agent(
        organization_id,
        agent_id,
        "recurring_charge_generated",
        entity_type="agent_account_movement",
        entity_id=movement_id,
        payload={
            "currency": currency,
            "amount": amount,
            "period_label": period_label,
        },
        event_key=f"recurring_charge_generated:{movement_id}",
    )
