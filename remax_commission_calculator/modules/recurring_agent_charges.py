"""Recurring charge orchestration over the canonical agent-account service."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal

from modules.agent_account import AgentAccountError, create_movement
from modules.agent_account_charges import (
    CHARGE_CATEGORIES,
    RECURRENCE_ANNUAL,
    RECURRENCE_MONTHLY,
    VAT_MODES,
    validate_charge_payload,
)
from modules.database import get_organizations
from modules.database.connection import IntegrityError
from modules.database.agent_account_repository import (
    SOURCE_RECURRING_CHARGE,
    get_movement_by_idempotency_key,
)
from modules.database.recurring_charges_repository import (
    RECURRENCE_ACTIVE,
    RECURRENCE_ENDED,
    RECURRENCE_PAUSED,
    create_recurring_charge as create_recurring_charge_record,
    get_recurring_charge,
    list_due_recurring_charges,
    list_recurring_charges,
    mark_recurring_charge_generated,
    set_recurring_charge_status,
    update_recurring_charge as update_recurring_charge_record,
)


RECURRENCE_TYPES = (RECURRENCE_MONTHLY, RECURRENCE_ANNUAL)
MONTH_NAMES_ES = (
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre",
    "Diciembre",
)
MONTH_NAMES_EN = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class RecurringChargeError(Exception):
    def __init__(self, message_key):
        super().__init__(message_key)
        self.message_key = message_key


def _parse_date(value, error_key):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d").date()
    except ValueError:
        raise RecurringChargeError(error_key) from None


def _next_month(value):
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _scheduled_date(year, month, day):
    return date(
        year,
        month,
        min(day, calendar.monthrange(year, month)[1]),
    )


def first_schedule_on_or_after(
    start_date,
    recurrence_type,
    billing_day,
    lower_bound=None,
):
    start = (
        start_date
        if isinstance(start_date, date)
        else _parse_date(
            start_date,
            "agent_recurring_err_invalid_start_date",
        )
    )
    lower = lower_bound or start
    if not isinstance(lower, date):
        lower = _parse_date(
            lower,
            "agent_recurring_err_invalid_start_date",
        )
    lower = max(start, lower)
    if recurrence_type == RECURRENCE_MONTHLY:
        candidate = date(lower.year, lower.month, int(billing_day))
        if candidate < lower:
            month = _next_month(lower)
            candidate = date(month.year, month.month, int(billing_day))
        return candidate
    candidate = _scheduled_date(
        lower.year,
        start.month,
        start.day,
    )
    if candidate < lower:
        candidate = _scheduled_date(
            lower.year + 1,
            start.month,
            start.day,
        )
    return candidate


def next_schedule_after(recurring, run_date):
    run = (
        run_date
        if isinstance(run_date, date)
        else _parse_date(
            run_date,
            "agent_recurring_err_invalid_run_date",
        )
    )
    if recurring["recurrence_type"] == RECURRENCE_MONTHLY:
        month = _next_month(run)
        return date(
            month.year,
            month.month,
            int(recurring["billing_day"]),
        )
    start = _parse_date(
        recurring["start_date"],
        "agent_recurring_err_invalid_start_date",
    )
    return _scheduled_date(
        run.year + 1,
        start.month,
        start.day,
    )


def billing_period_for(recurring, run_date):
    run = (
        run_date
        if isinstance(run_date, date)
        else _parse_date(
            run_date,
            "agent_recurring_err_invalid_run_date",
        )
    )
    if recurring["recurrence_type"] == RECURRENCE_MONTHLY:
        return f"{run.year:04d}-{run.month:02d}"
    return f"{run.year:04d}"


def billing_period_label(recurring, run_date, *, language="es"):
    run = (
        run_date
        if isinstance(run_date, date)
        else _parse_date(
            run_date,
            "agent_recurring_err_invalid_run_date",
        )
    )
    if recurring["recurrence_type"] == RECURRENCE_ANNUAL:
        return str(run.year)
    names = MONTH_NAMES_EN if language == "en" else MONTH_NAMES_ES
    return f"{names[run.month]} {run.year}"


def validate_recurring_charge_payload(payload, *, language="es"):
    recurrence_type = (
        payload.get("recurrence_type") or ""
    ).strip().lower()
    if recurrence_type not in RECURRENCE_TYPES:
        raise RecurringChargeError(
            "agent_recurring_err_invalid_recurrence_type"
        )
    start = _parse_date(
        payload.get("start_date"),
        "agent_recurring_err_invalid_start_date",
    )
    end_raw = (payload.get("end_date") or "").strip()
    end = (
        _parse_date(
            end_raw,
            "agent_recurring_err_invalid_end_date",
        )
        if end_raw
        else None
    )
    if end and end < start:
        raise RecurringChargeError(
            "agent_recurring_err_end_before_start"
        )

    billing_day = None
    if recurrence_type == RECURRENCE_MONTHLY:
        try:
            billing_day = int(payload.get("billing_day") or 0)
        except (TypeError, ValueError):
            billing_day = 0
        if not 1 <= billing_day <= 28:
            raise RecurringChargeError(
                "agent_recurring_err_invalid_billing_day"
            )

    charge_payload = {
        "charge_category": payload.get("charge_category"),
        "currency": payload.get("currency"),
        "amount": payload.get("amount"),
        "vat_mode": payload.get("vat_mode"),
        "vat_rate": payload.get("vat_rate"),
        "description": payload.get("description"),
        "movement_date": start.isoformat(),
        "recurring": "1",
        "recurrence_type": recurrence_type,
    }
    try:
        validated = validate_charge_payload(
            charge_payload,
            language=language,
        )
    except AgentAccountError as error:
        raise RecurringChargeError(error.message_key) from error

    next_run = first_schedule_on_or_after(
        start,
        recurrence_type,
        billing_day,
    )
    return {
        "charge_category": validated["charge_category"],
        "description": (
            (payload.get("description") or "").strip() or None
        ),
        "currency": validated["currency"],
        "input_amount": validated["input_amount"],
        "vat_mode": validated["vat_mode"],
        "net_amount": validated["net_amount"],
        "vat_rate": validated["vat_rate"],
        "vat_amount": validated["vat_amount"],
        "gross_amount": validated["gross_amount"],
        "recurrence_type": recurrence_type,
        "billing_day": billing_day,
        "start_date": start.isoformat(),
        "end_date": end.isoformat() if end else None,
        "next_run_date": next_run.isoformat(),
    }


def create_recurring_charge(
    organization_id,
    agent_id,
    payload,
    *,
    actor_user_id,
    language="es",
):
    fields = validate_recurring_charge_payload(
        payload,
        language=language,
    )
    fields["actor_user_id"] = actor_user_id
    return create_recurring_charge_record(
        organization_id,
        agent_id,
        fields=fields,
    )


def update_recurring_charge(
    organization_id,
    recurring_charge_id,
    payload,
    *,
    actor_user_id,
    as_of=None,
    language="es",
):
    current = get_recurring_charge(
        organization_id,
        recurring_charge_id,
    )
    if current is None:
        raise RecurringChargeError(
            "agent_recurring_err_not_found"
        )
    if current["status"] == RECURRENCE_ENDED:
        raise RecurringChargeError("agent_recurring_err_ended")
    fields = validate_recurring_charge_payload(
        payload,
        language=language,
    )
    lower = as_of or date.today()
    fields["next_run_date"] = first_schedule_on_or_after(
        fields["start_date"],
        fields["recurrence_type"],
        fields["billing_day"],
        lower_bound=lower,
    ).isoformat()
    fields["actor_user_id"] = actor_user_id
    try:
        return update_recurring_charge_record(
            organization_id,
            recurring_charge_id,
            fields=fields,
        )
    except ValueError as error:
        raise RecurringChargeError(
            "agent_recurring_err_not_found"
        ) from error


def pause_recurring_charge(
    organization_id,
    recurring_charge_id,
    *,
    actor_user_id,
):
    recurring = get_recurring_charge(
        organization_id,
        recurring_charge_id,
    )
    if recurring is None:
        raise RecurringChargeError("agent_recurring_err_not_found")
    if recurring["status"] == RECURRENCE_ENDED:
        raise RecurringChargeError("agent_recurring_err_ended")
    return set_recurring_charge_status(
        organization_id,
        recurring_charge_id,
        status=RECURRENCE_PAUSED,
        actor_user_id=actor_user_id,
    )


def resume_recurring_charge(
    organization_id,
    recurring_charge_id,
    *,
    actor_user_id,
    as_of=None,
):
    recurring = get_recurring_charge(
        organization_id,
        recurring_charge_id,
    )
    if recurring is None:
        raise RecurringChargeError("agent_recurring_err_not_found")
    if recurring["status"] == RECURRENCE_ENDED:
        raise RecurringChargeError("agent_recurring_err_ended")
    lower = as_of or date.today()
    next_run = first_schedule_on_or_after(
        recurring["start_date"],
        recurring["recurrence_type"],
        recurring["billing_day"],
        lower_bound=lower,
    )
    return set_recurring_charge_status(
        organization_id,
        recurring_charge_id,
        status=RECURRENCE_ACTIVE,
        actor_user_id=actor_user_id,
        next_run_date=next_run.isoformat(),
    )


def end_recurring_charge(
    organization_id,
    recurring_charge_id,
    *,
    actor_user_id,
):
    recurring = get_recurring_charge(
        organization_id,
        recurring_charge_id,
    )
    if recurring is None:
        raise RecurringChargeError("agent_recurring_err_not_found")
    return set_recurring_charge_status(
        organization_id,
        recurring_charge_id,
        status=RECURRENCE_ENDED,
        actor_user_id=actor_user_id,
    )


def _movement_payload(recurring, run_date, *, language):
    return {
        "charge_category": recurring["charge_category"],
        "currency": recurring["currency"],
        "amount": str(recurring["input_amount"]),
        "vat_mode": recurring["vat_mode"],
        "vat_rate": str(
            Decimal(str(recurring["vat_rate"])) * Decimal("100")
        ),
        "description": recurring.get("description") or "",
        "movement_date": run_date.isoformat(),
        "billing_period": billing_period_for(recurring, run_date),
        "period_label": billing_period_label(
            recurring,
            run_date,
            language=language,
        ),
        "recurring": "1",
        "recurrence_type": recurring["recurrence_type"],
        "_source_type": SOURCE_RECURRING_CHARGE,
        "_source_id": recurring["id"],
    }


def build_due_preview(
    organization_id,
    *,
    as_of=None,
    limit=100,
    language="es",
):
    run_until = as_of or date.today()
    if not isinstance(run_until, date):
        run_until = _parse_date(
            run_until,
            "agent_recurring_err_invalid_run_date",
        )
    preview = []
    for recurring in list_due_recurring_charges(
        organization_id,
        as_of=run_until.isoformat(),
        limit=limit,
    ):
        run_date = _parse_date(
            recurring["next_run_date"],
            "agent_recurring_err_invalid_run_date",
        )
        validated = validate_charge_payload(
            _movement_payload(
                recurring,
                run_date,
                language=language,
            ),
            language=language,
        )
        preview.append(
            {
                "recurring_charge": recurring,
                "run_date": run_date.isoformat(),
                "billing_period": validated["billing_period"],
                "period_label": validated["period_label"],
                "description": validated["description"],
                "currency": validated["currency"],
                "net_amount": validated["net_amount"],
                "vat_rate": validated["vat_rate"],
                "vat_amount": validated["vat_amount"],
                "gross_amount": validated["gross_amount"],
            }
        )
    return preview


def generate_due_recurring_charges(
    organization_id,
    *,
    as_of=None,
    actor_user_id=None,
    dry_run=False,
    limit=100,
    language="es",
):
    preview = build_due_preview(
        organization_id,
        as_of=as_of,
        limit=limit,
        language=language,
    )
    if dry_run:
        return {"preview": preview, "generated": [], "events": []}

    generated = []
    events = []
    for item in preview:
        recurring = item["recurring_charge"]
        run_date = _parse_date(
            item["run_date"],
            "agent_recurring_err_invalid_run_date",
        )
        idempotency_key = (
            f"recurring:{organization_id}:{recurring['id']}:"
            f"{item['billing_period']}"
        )
        try:
            movement = create_movement(
                organization_id,
                recurring["agent_id"],
                _movement_payload(
                    recurring,
                    run_date,
                    language=language,
                ),
                created_by_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                language=language,
            )
        except IntegrityError:
            movement = get_movement_by_idempotency_key(
                organization_id,
                idempotency_key,
            )
            if movement is None:
                raise

        next_run = next_schedule_after(recurring, run_date)
        mark_recurring_charge_generated(
            organization_id,
            recurring["id"],
            expected_run_date=item["run_date"],
            next_run_date=next_run.isoformat(),
            actor_user_id=actor_user_id,
        )
        generated.append(movement)
        events.append(
            {
                "event_type": "recurring_charge.generated",
                "organization_id": organization_id,
                "agent_id": recurring["agent_id"],
                "recurring_charge_id": recurring["id"],
                "charge_movement_id": movement["id"],
                "currency": movement["currency"],
                "amount": movement["gross_amount"],
                "billing_period": movement["billing_period"],
            }
        )
    return {"preview": preview, "generated": generated, "events": events}


def generate_all_active_organizations(
    *,
    as_of=None,
    actor_user_id=None,
    dry_run=False,
    limit_per_organization=100,
    language="es",
):
    results = []
    for organization in get_organizations():
        if not organization.get("is_active"):
            continue
        result = generate_due_recurring_charges(
            organization["id"],
            as_of=as_of,
            actor_user_id=actor_user_id,
            dry_run=dry_run,
            limit=limit_per_organization,
            language=language,
        )
        results.append(
            {"organization": organization, **result}
        )
    return results


__all__ = [
    "RecurringChargeError",
    "billing_period_for",
    "billing_period_label",
    "build_due_preview",
    "create_recurring_charge",
    "end_recurring_charge",
    "generate_all_active_organizations",
    "generate_due_recurring_charges",
    "list_recurring_charges",
    "pause_recurring_charge",
    "resume_recurring_charge",
    "update_recurring_charge",
    "validate_recurring_charge_payload",
]
