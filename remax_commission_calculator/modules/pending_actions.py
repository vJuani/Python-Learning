"""
Pending Center service (Phase 4A).

A pending action is always DERIVED from the real domain data, never
stored. Resolving the underlying record (confirming a receipt, crediting
a commission, creating an invoice, completing a fiscal profile,
generating a recurring charge) makes the pending disappear on the next
read, so nothing has to be marked as resolved.

Informational notifications live in ``modules.notifications_service``
and are a different concept: reading one never resolves a pending.
"""

from __future__ import annotations

from datetime import date

from modules.agent_tasks import list_overdue_tasks
from modules.database.agent_payment_ai_drafts_repository import (
    OPEN_STATUSES as AGENT_DRAFT_OPEN_STATUSES,
    STATUS_REVIEW as AGENT_DRAFT_REVIEW,
)
from modules.database.cash_ai_drafts_repository import (
    STATUS_FAILED as CASH_DRAFT_FAILED,
    STATUS_PROCESSING as CASH_DRAFT_PROCESSING,
    STATUS_REVIEW as CASH_DRAFT_REVIEW,
)
from modules.database.invoices_repository import (
    count_invoices_by_status,
)
from modules.database.pending_actions_repository import (
    count_agents_blocked_by_fiscal_profile,
    count_open_ai_drafts,
    count_recurring_charges_due,
    list_agent_available_invoices,
    list_agent_unpaid_charges,
    list_agents_blocked_by_fiscal_profile,
    list_charges_without_invoice,
    list_commissions_ready_to_credit,
    list_open_agent_payment_drafts,
)
from modules.database.recurring_charges_repository import (
    list_due_recurring_charges,
)
from modules.database.tenant import require_organization_id
from modules.i18n import translate


PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"

_PRIORITY_ORDER = {
    PRIORITY_HIGH: 0,
    PRIORITY_MEDIUM: 1,
    PRIORITY_LOW: 2,
}

CATEGORY_FINANCE = "finance"
CATEGORY_BILLING = "billing"
CATEGORY_OPERATIONS = "operations"
CATEGORY_AGENTS = "agents"

STAFF_CATEGORIES = (
    CATEGORY_FINANCE,
    CATEGORY_BILLING,
    CATEGORY_OPERATIONS,
    CATEGORY_AGENTS,
)

_AGENT_DRAFT_PENDING_STATUSES = AGENT_DRAFT_OPEN_STATUSES
_CASH_DRAFT_PENDING_STATUSES = (
    CASH_DRAFT_REVIEW,
    CASH_DRAFT_PROCESSING,
    CASH_DRAFT_FAILED,
)

# Detail rows are capped per pending type so the center, the bell and
# the dashboard never grow with the size of the organization.
_DETAIL_LIMIT = 5


def _t(key, language, **kwargs):
    return translate(key, language, **kwargs)


def _as_of_iso(as_of=None):
    if as_of is None:
        return date.today().isoformat()
    if isinstance(as_of, date):
        return as_of.isoformat()
    return str(as_of)


def _action(
    *,
    kind,
    category,
    priority,
    title,
    subtitle=None,
    amount=None,
    currency=None,
    action_label,
    endpoint,
    endpoint_args=None,
    occurred_at=None,
    count=1,
):
    return {
        "kind": kind,
        "category": category,
        "priority": priority,
        "title": title,
        "subtitle": subtitle,
        "amount": amount,
        "currency": currency,
        "action_label": action_label,
        "endpoint": endpoint,
        "endpoint_args": endpoint_args or {},
        "occurred_at": occurred_at,
        "count": count,
    }


def _sort_actions(actions):
    return sorted(
        actions,
        key=lambda item: (
            _PRIORITY_ORDER.get(item["priority"], 9),
            item.get("occurred_at") or "",
            item["kind"],
        ),
    )


def build_staff_pending_actions(
    organization_id,
    *,
    language="es",
    as_of=None,
):
    """Build every derived staff pending action, highest priority first."""
    organization_id = require_organization_id(organization_id)
    as_of_iso = _as_of_iso(as_of)
    actions = []

    for draft in list_open_agent_payment_drafts(
        organization_id,
        _AGENT_DRAFT_PENDING_STATUSES,
        limit=_DETAIL_LIMIT,
    ):
        actions.append(
            _action(
                kind="agent_payment_receipt_review",
                category=CATEGORY_FINANCE,
                priority=PRIORITY_HIGH,
                title=_t("pending_receipt_review_title", language),
                subtitle=(
                    draft.get("agent_name")
                    or _t("pending_agent_unassigned", language)
                ),
                amount=draft.get("amount"),
                currency=draft.get("currency"),
                action_label=_t("pending_action_review", language),
                endpoint="agent_payment_ai_review",
                endpoint_args={"draft_id": draft["draft_id"]},
                occurred_at=draft.get("created_at"),
            )
        )

    cash_drafts_pending = count_open_ai_drafts(
        organization_id,
        "cash_ai_drafts",
        _CASH_DRAFT_PENDING_STATUSES,
    )

    if cash_drafts_pending:
        actions.append(
            _action(
                kind="cash_receipt_review",
                category=CATEGORY_FINANCE,
                priority=PRIORITY_HIGH,
                title=_t("pending_cash_review_title", language),
                subtitle=_t(
                    "pending_cash_review_summary",
                    language,
                    count=cash_drafts_pending,
                ),
                action_label=_t("pending_action_review", language),
                endpoint="cash_ai_new",
                count=cash_drafts_pending,
            )
        )

    for commission in list_commissions_ready_to_credit(
        organization_id,
        limit=_DETAIL_LIMIT,
    ):
        actions.append(
            _action(
                kind="commission_ready_to_credit",
                category=CATEGORY_OPERATIONS,
                priority=PRIORITY_MEDIUM,
                title=_t("pending_commission_ready_title", language),
                subtitle=(
                    f"{commission['operation_reference']} · "
                    f"{commission['agent_name']}"
                ),
                amount=commission["amount"],
                currency=commission["currency"],
                action_label=_t("pending_action_credit", language),
                endpoint="operations_detail",
                endpoint_args={
                    "operation_id": commission["operation_id"],
                },
                occurred_at=commission.get("operation_date"),
            )
        )

    for charge in list_charges_without_invoice(
        organization_id,
        limit=_DETAIL_LIMIT,
    ):
        actions.append(
            _action(
                kind="charge_without_invoice",
                category=CATEGORY_BILLING,
                priority=PRIORITY_MEDIUM,
                title=_t("pending_charge_no_invoice_title", language),
                subtitle=(
                    f"{charge['description']} · {charge['agent_name']}"
                ),
                amount=charge["amount"],
                currency=charge["currency"],
                action_label=_t(
                    "pending_action_generate_invoice",
                    language,
                ),
                endpoint="billing_prepare_charge",
                endpoint_args={
                    "charge_id": charge["charge_movement_id"],
                },
                occurred_at=charge.get("movement_date"),
            )
        )

    invoice_counts = count_invoices_by_status(organization_id)
    ready_to_issue = int(invoice_counts.get("ready_to_issue") or 0)
    invoices_with_error = int(invoice_counts.get("error") or 0)

    if invoices_with_error:
        actions.append(
            _action(
                kind="invoice_error",
                category=CATEGORY_BILLING,
                priority=PRIORITY_HIGH,
                title=_t("pending_invoice_error_title", language),
                subtitle=_t(
                    "pending_invoice_error_summary",
                    language,
                    count=invoices_with_error,
                ),
                action_label=_t("pending_action_review", language),
                endpoint="billing_list",
                endpoint_args={"status": "error"},
                count=invoices_with_error,
            )
        )

    if ready_to_issue:
        actions.append(
            _action(
                kind="invoice_ready_to_issue",
                category=CATEGORY_BILLING,
                priority=PRIORITY_MEDIUM,
                title=_t("pending_invoice_ready_title", language),
                subtitle=_t(
                    "pending_invoice_ready_summary",
                    language,
                    count=ready_to_issue,
                ),
                action_label=_t("pending_action_review", language),
                endpoint="billing_list",
                endpoint_args={"tab": "ready"},
                count=ready_to_issue,
            )
        )

    for blocked in list_agents_blocked_by_fiscal_profile(
        organization_id,
        limit=_DETAIL_LIMIT,
    ):
        actions.append(
            _action(
                kind="agent_fiscal_profile_incomplete",
                category=CATEGORY_AGENTS,
                priority=PRIORITY_MEDIUM,
                title=_t("pending_fiscal_profile_title", language),
                subtitle=_t(
                    "pending_fiscal_profile_summary",
                    language,
                    agent=blocked["agent_name"],
                    count=blocked["charge_count"],
                ),
                action_label=_t(
                    "pending_action_complete_profile",
                    language,
                ),
                endpoint="agents_detail",
                endpoint_args={"agent_id": blocked["agent_id"]},
                occurred_at=blocked.get("oldest_charge_date"),
            )
        )

    due_recurring = list_due_recurring_charges(
        organization_id,
        as_of=as_of_iso,
        limit=_DETAIL_LIMIT + 1,
    )

    if due_recurring:
        actions.append(
            _action(
                kind="recurring_charges_due",
                category=CATEGORY_FINANCE,
                priority=PRIORITY_LOW,
                title=_t("pending_recurring_due_title", language),
                subtitle=_t(
                    "pending_recurring_due_summary",
                    language,
                    count=count_recurring_charges_due(
                        organization_id,
                        as_of=as_of_iso,
                    ),
                ),
                action_label=_t("pending_action_review", language),
                endpoint="agent_recurring_generate",
                count=len(due_recurring),
            )
        )

    # Deliberately not derived: agent debt has no due date in the
    # schema, so "overdue" cannot be computed without inventing it.
    # An outstanding balance is already actionable through the charge
    # and payment pendings above plus the current-account index.

    return _sort_actions(actions)


def build_agent_pending_actions(
    organization_id,
    agent_id,
    *,
    user_id=None,
    language="es",
    as_of=None,
):
    """Build the agent's own pending actions. Never other agents' data."""
    organization_id = require_organization_id(organization_id)
    as_of_iso = _as_of_iso(as_of)
    actions = []

    # Overdue follow-ups are derived from status + due_at, so they
    # disappear on their own once the task is completed or rescheduled.
    for task in list_overdue_tasks(
        organization_id,
        agent_id=agent_id,
        language=language,
        limit=_DETAIL_LIMIT,
    ):
        subtitle = task["title"]

        if task["relation_label"]:
            subtitle = f"{subtitle} · {task['relation_label']}"

        actions.append(
            _action(
                kind="own_task_overdue",
                category=CATEGORY_OPERATIONS,
                priority=PRIORITY_HIGH,
                title=_t("pending_own_task_overdue_title", language),
                subtitle=f"{subtitle} · {task['overdue_label']}",
                action_label=_t("pending_action_open_agenda", language),
                endpoint="agenda_index",
                endpoint_args={"filter": "overdue"},
                occurred_at=task["due_date_value"],
            )
        )

    for charge in list_agent_unpaid_charges(
        organization_id,
        agent_id,
        limit=_DETAIL_LIMIT,
    ):
        actions.append(
            _action(
                kind="own_charge_pending_payment",
                category=CATEGORY_FINANCE,
                priority=PRIORITY_HIGH,
                title=_t("pending_own_payment_title", language),
                subtitle=charge["description"],
                amount=charge["remaining_amount"],
                currency=charge["currency"],
                action_label=_t("pending_action_view_account", language),
                endpoint="my_agent_account",
                occurred_at=charge.get("movement_date"),
            )
        )

    if count_agents_blocked_by_fiscal_profile(
        organization_id,
        agent_id=agent_id,
    ):
        actions.append(
            _action(
                kind="own_fiscal_profile_incomplete",
                category=CATEGORY_AGENTS,
                priority=PRIORITY_MEDIUM,
                title=_t("pending_fiscal_profile_title", language),
                subtitle=_t(
                    "pending_own_fiscal_profile_summary",
                    language,
                ),
                action_label=_t("pending_action_view_profile", language),
                endpoint="billing_agent_profile_self",
            )
        )

    for invoice in list_agent_available_invoices(
        organization_id,
        agent_id,
        limit=_DETAIL_LIMIT,
    ):
        actions.append(
            _action(
                kind="own_invoice_available",
                category=CATEGORY_BILLING,
                priority=PRIORITY_LOW,
                title=_t("pending_own_invoice_title", language),
                subtitle=(
                    f"{invoice['invoice_number_internal']} · "
                    f"{invoice['description']}"
                ),
                amount=invoice["total_amount"],
                currency=invoice["currency"],
                action_label=_t("pending_action_view_invoice", language),
                endpoint="billing_detail",
                endpoint_args={"invoice_id": invoice["invoice_id"]},
                occurred_at=invoice.get("created_at"),
            )
        )

    if user_id is not None:
        own_drafts = count_open_ai_drafts(
            organization_id,
            "agent_payment_ai_drafts",
            (AGENT_DRAFT_REVIEW,),
            created_by_user_id=user_id,
        )

        if own_drafts:
            actions.append(
                _action(
                    kind="own_receipt_in_review",
                    category=CATEGORY_FINANCE,
                    priority=PRIORITY_LOW,
                    title=_t("pending_own_receipt_title", language),
                    subtitle=_t(
                        "pending_own_receipt_summary",
                        language,
                        count=own_drafts,
                    ),
                    action_label=_t(
                        "pending_action_view_account",
                        language,
                    ),
                    endpoint="my_agent_account",
                    count=own_drafts,
                )
            )

    upcoming_recurring = count_recurring_charges_due(
        organization_id,
        as_of=as_of_iso,
        agent_id=agent_id,
    )

    if upcoming_recurring:
        actions.append(
            _action(
                kind="own_recurring_upcoming",
                category=CATEGORY_FINANCE,
                priority=PRIORITY_LOW,
                title=_t("pending_own_recurring_title", language),
                subtitle=_t(
                    "pending_own_recurring_summary",
                    language,
                    count=upcoming_recurring,
                ),
                action_label=_t("pending_action_view_account", language),
                endpoint="my_agent_account",
                count=upcoming_recurring,
            )
        )

    return _sort_actions(actions)


def count_agent_pending_actions(
    organization_id,
    agent_id,
    *,
    user_id=None,
    as_of=None,
):
    return len(
        build_agent_pending_actions(
            organization_id,
            agent_id,
            user_id=user_id,
            as_of=as_of,
        )
    )


def summarize_pending_actions(actions, *, language="es"):
    """Group actions by category for the compact dashboard block."""
    groups = {}

    for action in actions:
        category = action["category"]
        group = groups.setdefault(
            category,
            {
                "category": category,
                "label": _t(
                    f"pending_category_{category}",
                    language,
                ),
                "count": 0,
            },
        )
        group["count"] += 1

    ordered = [
        groups[category]
        for category in STAFF_CATEGORIES
        if category in groups
    ]

    return {
        "total": len(actions),
        "groups": ordered,
        "top": actions[:5],
    }


def filter_pending_actions(actions, category):
    if not category or category == "all":
        return actions

    return [
        action
        for action in actions
        if action["category"] == category
    ]


__all__ = [
    "CATEGORY_AGENTS",
    "CATEGORY_BILLING",
    "CATEGORY_FINANCE",
    "CATEGORY_OPERATIONS",
    "PRIORITY_HIGH",
    "PRIORITY_LOW",
    "PRIORITY_MEDIUM",
    "STAFF_CATEGORIES",
    "build_agent_pending_actions",
    "build_staff_pending_actions",
    "count_agent_pending_actions",
    "filter_pending_actions",
    "summarize_pending_actions",
]
