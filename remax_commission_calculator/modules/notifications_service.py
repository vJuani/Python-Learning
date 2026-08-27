from modules.database.notifications_repository import (
    create_notification
)
from modules.database.users_repository import (
    get_user_by_agent_id
)


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
