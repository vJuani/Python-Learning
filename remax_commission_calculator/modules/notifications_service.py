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

from modules.database.users_repository import (
    get_user_by_agent_id
)
from modules.notifications.service import notify_user as dispatch_user_notification
from modules.notifications.service import send_user_notification
from modules.web_push import send_user_pushes


logger = logging.getLogger(__name__)

PREFS_SAVE_MATCH_NOTIFY_LIMIT = 5


def _legacy_dispatch(
    *,
    organization_id,
    agent_id=None,
    user_id=None,
    kind,
    entity_type,
    entity_id,
    payload=None,
    actor_user_id=None,
    event_key=None,
    url=None,
):
    payload = dict(payload or {})
    title = (
        payload.get("title")
        or payload.get("address")
        or payload.get("property")
        or kind
    )
    body = payload.get("body") or payload.get("address") or payload.get("reason") or ""
    result = dispatch_user_notification(
        user_id,
        organization_id,
        kind,
        title,
        body,
        url or payload.get("url"),
        event_key=event_key or payload.get("event_key") or f"{kind}_{entity_id}",
        metadata=payload,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        agent_id=agent_id,
    )
    return None if not result else result.get("notification_id")


def status_transition_event_key(entity, entity_id, from_status, to_status, occurred_at):
    """
    Key for a review transition that can legitimately repeat.

    An operation or listing can be rejected, fixed and rejected again;
    each review is a distinct event, so the key carries the transition
    and the review timestamp instead of only the entity id.
    """
    stamp = str(occurred_at or "").replace(" ", "T")
    return (
        f"{entity}_{int(entity_id)}_{from_status or 'none'}_to_"
        f"{to_status or 'none'}_{stamp}"
    )


def notify_agent_for_property(
    organization_id,
    agent_id,
    kind,
    property_id,
    payload,
    actor_user_id=None,
    event_key=None,
):
    return _legacy_dispatch(
        organization_id=organization_id,
        agent_id=agent_id,
        kind=kind,
        entity_type="property",
        entity_id=property_id,
        payload=payload,
        actor_user_id=actor_user_id,
        event_key=event_key,
        url=f"/properties/{int(property_id)}",
    )


def notify_agent_for_property_change(
    organization_id,
    agent_id,
    kind,
    change_request_id,
    payload,
    actor_user_id=None
):
    return _legacy_dispatch(
        organization_id=organization_id,
        agent_id=agent_id,
        kind=kind,
        entity_type="property_change",
        entity_id=change_request_id,
        payload=payload,
        actor_user_id=actor_user_id,
    )


def notify_agent_for_operation(
    organization_id,
    agent_id,
    kind,
    operation_id,
    payload,
    actor_user_id=None,
    event_key=None,
):
    return _legacy_dispatch(
        organization_id=organization_id,
        agent_id=agent_id,
        kind=kind,
        entity_type="operation",
        entity_id=operation_id,
        payload=payload,
        actor_user_id=actor_user_id,
        event_key=event_key,
        url=f"/operations/{int(operation_id)}",
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
    event_key=None,
):
    """Notify agent that a buyer/seller side is ready to bill (in-app + push)."""
    user = get_user_by_agent_id(agent_id, organization_id)
    if user is None:
        return None

    payload = dict(payload or {})
    property_name = (payload.get("property") or "").strip() or "—"
    from modules.database.organization_settings_repository import (
        get_organization_settings,
    )
    from modules.i18n import translate

    language = (get_organization_settings(organization_id) or {}).get(
        "default_language"
    ) or "es"
    title = translate("push_invoice_ready_title", language)
    body = translate(
        "push_invoice_ready_body",
        language,
        property=property_name,
    )
    url = "/billing?tab=pending"
    key = event_key or f"operation_{operation_id}_ready_to_invoice"
    result = send_user_notification(
        user["id"],
        organization_id,
        "operation_side_ready_to_invoice",
        title,
        body,
        url,
        metadata=payload,
        event_key=key,
        entity_type="operation",
        entity_id=operation_id,
        actor_user_id=actor_user_id,
    )
    return result.get("notification_id") if result else None


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

    return _legacy_dispatch(
        organization_id=organization_id,
        user_id=user_id,
        kind=kind,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
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


def property_match_event_key(contact_id, match_row):
    property_id = (
        match_row.get("internal_property_id")
        or match_row.get("property_id")
    )
    external_id = match_row.get("external_listing_id")
    if property_id is not None:
        return f"need_{int(contact_id)}_property_{int(property_id)}_match"
    if external_id is not None:
        return f"need_{int(contact_id)}_external_{int(external_id)}_match"
    return None


def _contact_display_name(contact):
    name = (
        (contact.get("first_name") or "").strip()
        or (contact.get("name") or "").strip()
    )
    if name:
        return name.split()[0]
    return "—"


def _org_language(organization_id):
    from modules.database.organization_settings_repository import (
        get_organization_settings,
    )

    settings = get_organization_settings(organization_id) or {}
    return settings.get("default_language") or "es"


def notify_new_property_matches_for_contact(
    organization_id,
    contact,
    *,
    listings=None,
    limit=None,
):
    """Notify the contact owner about newly ranked matches above threshold."""
    if not contact or contact.get("id") is None:
        return []

    contact_org = contact.get("organization_id")
    if contact_org is not None and int(contact_org) != int(organization_id):
        return []

    agent_id = contact.get("agent_id")
    user = get_user_by_agent_id(agent_id, organization_id) if agent_id else None
    if user is None:
        return []

    from modules.property_match import HIDDEN_SCORE, rank_contact_properties

    try:
        ranked = rank_contact_properties(
            organization_id,
            contact,
            agent_id=agent_id,
            listings=listings,
        )
    except Exception:
        logger.warning(
            "property_match_rank_failed organization_id=%s contact_id=%s",
            organization_id,
            contact.get("id"),
            exc_info=True,
        )
        return []

    language = _org_language(organization_id)
    from modules.i18n import translate

    title = translate("push_property_match_title", language)
    name = _contact_display_name(contact)
    body = translate("push_property_match_body", language, name=name)
    url = f"/contacts/{int(contact['id'])}/property-matches"
    sent = []
    for match_row in ranked:
        if limit is not None and len(sent) >= int(limit):
            break
        if match_row.get("hidden") or match_row.get("discarded"):
            continue
        if int(match_row.get("score") or 0) < HIDDEN_SCORE:
            continue
        event_key = property_match_event_key(contact["id"], match_row)
        if not event_key:
            continue
        result = send_user_notification(
            user["id"],
            organization_id,
            "property_match",
            title,
            body,
            url,
            metadata={
                "contact_id": contact["id"],
                "contact_name": name,
                "property_id": match_row.get("internal_property_id")
                or match_row.get("property_id"),
                "external_listing_id": match_row.get("external_listing_id"),
                "score": match_row.get("score"),
            },
            event_key=event_key,
            entity_type="contact",
            entity_id=contact["id"],
        )
        if result.get("created"):
            sent.append(result)
    return sent


def notify_new_property_matches_for_property(organization_id, property_row):
    """After a listing is approved, notify agents whose needs now match it."""
    if not property_row:
        return []
    if int(property_row.get("organization_id") or 0) != int(organization_id):
        return []
    from modules.database.properties_repository import STATUS_APPROVED

    if (property_row.get("status") or STATUS_APPROVED) != STATUS_APPROVED:
        return []

    agent_id = property_row.get("agent_id")
    if agent_id is None:
        return []

    from modules.database.contacts_repository import list_contacts
    from modules.listings_normalize import (
        attach_listing_identity,
        listing_from_property,
    )

    listing = attach_listing_identity(
        listing_from_property(property_row),
        property_id=property_row.get("id"),
    )
    listing["status"] = property_row.get("status")
    listing["organization_id"] = organization_id
    listing["agent_id"] = agent_id

    results = []
    for contact in list_contacts(
        organization_id,
        agent_id=agent_id,
        limit=500,
    ):
        results.extend(
            notify_new_property_matches_for_contact(
                organization_id,
                contact,
                listings=[listing],
                limit=1,
            )
        )
    return results
