"""Read-only presentation for the reservations board.

Numbers shown here are labels over rows already stored. Commission figures are
copied from the linked operation row and are never recalculated.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from modules.database.connection import get_connection
from modules.database.reservations_repository import OPEN_STATUSES
from modules.database.tenant import require_organization_id
from modules.reservations import (
    PAYMENT_METHODS,
    RENT_MILESTONES,
    SALE_MILESTONES,
    STATUSES,
    format_money,
    list_visible_reservations,
)


VIEWS = ("active", "closing", "financing", "agent", "history", "delayed")
CLOSING_WINDOW_DAYS = 14
CREDIT_PAYMENTS = ("mortgage", "mixed")
RENT_PURPOSES = ("rental", "temporary_rental")
SKIPPED_COVER_SOURCES = frozenset({"acm", "comparable", "mock_network"})

SALE_PROGRESS = (
    ("reserved", "reservation_status_reserved"),
    ("documentation", "reservation_status_documentation"),
    ("credit_contract", "reservation_progress_credit_contract"),
    ("deed_scheduled", "reservation_status_deed_scheduled"),
    ("in_operation", "reservation_status_in_operation"),
    ("closed", "reservation_status_closed"),
)
RENT_PROGRESS = (
    ("reserved", "reservation_status_reserved"),
    ("documentation", "reservation_status_documentation"),
    ("contract", "reservation_progress_contract"),
    ("handover", "reservation_progress_handover"),
    ("in_operation", "reservation_status_in_operation"),
    ("closed", "reservation_status_closed"),
)


def fold_text(value):
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return text.casefold().strip()


def parse_board_date(value):
    raw = str(value or "").strip()
    if len(raw) >= 10:
        raw = raw[:10]
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def display_date(value):
    parsed = parse_board_date(value)
    if parsed is None:
        return ""
    return parsed.strftime("%d/%m/%Y")


def property_cover_url(cover, property_id):
    """Cover of this organization's property gallery. Never an ACM comparable."""
    if not cover:
        return None
    source = str(cover.get("source") or "").strip().lower()
    if source in SKIPPED_COVER_SOURCES:
        return None
    from modules.property_sync.media import get_property_media_url

    return get_property_media_url(cover, property_id)


def _money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _purpose_key(listing_purpose):
    if str(listing_purpose or "").strip() in RENT_PURPOSES:
        return "rent"
    return "sale"


def _is_open(row):
    return row.get("reservation_status") in OPEN_STATUSES


def _close_date(row):
    return parse_board_date(row.get("estimated_closing_date"))


def is_delayed(row, today):
    if not _is_open(row):
        return False
    closing = _close_date(row)
    if closing is None:
        return True
    return closing < today


def is_closing_soon(row, today):
    if not _is_open(row):
        return False
    closing = _close_date(row)
    if closing is None:
        return False
    return today <= closing <= today + timedelta(days=CLOSING_WINDOW_DAYS)


def status_label_key(row):
    status = row.get("reservation_status") or ""
    if _purpose_key(row.get("listing_purpose")) == "rent":
        if status == "contract":
            return "reservation_progress_contract"
        if status == "deed_scheduled":
            return "reservation_progress_handover"
    return "reservation_status_" + status


def progress_for(row):
    rent = _purpose_key(row.get("listing_purpose")) == "rent"
    spec = RENT_PROGRESS if rent else SALE_PROGRESS
    status = row.get("reservation_status") or ""
    if rent:
        index = {
            "reserved": 0,
            "documentation": 1,
            "contract": 2,
            "deed_scheduled": 3,
            "in_operation": 4,
            "closed": 5,
        }.get(status)
    else:
        index = {
            "reserved": 0,
            "documentation": 1,
            "financing": 2,
            "contract": 2,
            "deed_scheduled": 3,
            "in_operation": 4,
            "closed": 5,
        }.get(status)
    cancelled = status == "cancelled"
    steps = []
    for position, (key, label_key) in enumerate(spec):
        if cancelled or index is None:
            state = "upcoming"
        elif position < index:
            state = "done"
        elif position == index:
            state = "current"
        else:
            state = "upcoming"
        step = {"key": key, "label_key": label_key, "state": state}
        if key == "credit_contract" and state == "current":
            if status == "financing":
                step["detail_key"] = "reservation_status_financing"
            elif status == "contract":
                step["detail_key"] = "reservation_status_contract"
        steps.append(step)
    return {"steps": steps, "cancelled": cancelled}


def closing_gaps(row):
    """What is actually missing. No synthetic tasks."""
    status = row.get("reservation_status")
    if status in ("closed", "cancelled"):
        return []
    gaps = []
    purpose = _purpose_key(row.get("listing_purpose"))
    milestone = str(row.get("next_milestone") or "").strip()
    if not row.get("estimated_closing_date"):
        gaps.append("reservation_gap_no_close")
    if not milestone or milestone == "undefined":
        gaps.append("reservation_gap_no_milestone")
    if status == "documentation":
        gaps.append("reservation_gap_documentation")
    if status == "financing":
        gaps.append("reservation_gap_financing")
    if (
        purpose == "sale"
        and milestone == "boleto"
        and status in ("reserved", "documentation", "financing")
    ):
        gaps.append("reservation_gap_boleto")
    if purpose == "sale" and status == "deed_scheduled":
        gaps.append("reservation_gap_deed")
    if purpose == "rent" and status == "deed_scheduled":
        gaps.append("reservation_gap_handover")
    if not row.get("operation_id"):
        gaps.append("reservation_gap_prepare")
    return gaps


def row_alerts(row, today):
    if not _is_open(row):
        return []
    alerts = []
    closing = _close_date(row)
    if closing is None:
        alerts.append("reservation_alert_no_close")
    elif closing < today:
        alerts.append("reservation_alert_close_overdue")
    milestone = str(row.get("next_milestone") or "").strip()
    if not milestone or milestone == "undefined":
        alerts.append("reservation_alert_no_milestone")
    if not row.get("operation_id"):
        alerts.append("reservation_alert_no_operation")
    return alerts


def _commission(row, operation):
    if not row.get("operation_id") or not operation:
        return {
            "commission_label": "",
            "commission_state": "pending_operation",
            "commission_total_label": "",
            "commission_agent_label": "",
            "operation_amount_label": "",
            "operation_paid": None,
        }
    currency = operation.get("currency") or "USD"
    paid = str(operation.get("was_invoiced") or "no").strip().lower() == "yes"
    return {
        "commission_label": format_money(operation.get("agent_payment"), currency),
        "commission_state": "paid" if paid else "unpaid",
        "commission_total_label": format_money(operation.get("total_commission"), currency),
        "commission_agent_label": format_money(operation.get("agent_payment"), currency),
        "operation_amount_label": format_money(
            operation.get("sale_price"),
            currency,
        ),
        "operation_paid": paid,
    }


def present_reservation(row, *, cover_url=None, operation=None, today=None):
    today = today or date.today()
    item = dict(row)
    item["zone"] = (row.get("neighborhood") or "").strip()
    item["external_ref"] = (row.get("property_external_id") or "").strip()
    item["purpose_key"] = _purpose_key(row.get("listing_purpose"))
    item["agreed_label"] = format_money(
        row.get("agreed_property_price"),
        row.get("agreed_currency"),
    )
    item["amount_label"] = format_money(
        row.get("reservation_amount"),
        row.get("reservation_currency"),
    )
    item["original_label"] = format_money(
        row.get("original_property_price"),
        row.get("original_currency"),
    )
    item["close_display"] = display_date(row.get("estimated_closing_date"))
    item["reserved_display"] = display_date(row.get("reserved_at"))
    item["thumbnail_url"] = cover_url or None
    item["status_label_key"] = status_label_key(item)
    item["delayed"] = is_delayed(item, today)
    item["closing_soon"] = is_closing_soon(item, today)
    item["alerts"] = row_alerts(item, today)
    item["gaps"] = closing_gaps(item)
    item["progress"] = progress_for(item)
    item.update(_commission(item, operation))
    return item


def _operations_by_id(organization_id, operation_ids):
    ids = []
    for value in operation_ids or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    organization_id = require_organization_id(organization_id)
    placeholders = ", ".join("?" for _ in ids)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT id, sale_price, total_commission, agent_payment,
                   was_invoiced, currency, status
            FROM operations
            WHERE organization_id = ? AND id IN ({placeholders})
            """,
            (organization_id, *ids),
        ).fetchall()
    finally:
        connection.close()
    found = {}
    for row in rows:
        found[int(row[0])] = {
            "id": row[0],
            "sale_price": row[1],
            "total_commission": row[2],
            "agent_payment": row[3],
            "was_invoiced": row[4],
            "currency": row[5] or "USD",
            "status": row[6],
        }
    return found


def _covers_by_property(organization_id, property_ids):
    from modules.property_sync.media import list_covers_for_properties

    covers = list_covers_for_properties(organization_id, property_ids)
    urls = {}
    for property_id, cover in covers.items():
        urls[property_id] = property_cover_url(cover, property_id)
    return urls


def _blank_filters(raw):
    raw = raw or {}
    view = str(raw.get("view") or "active").strip()
    if view not in VIEWS:
        view = "active"
    credit = str(raw.get("credit") or "").strip()
    if credit not in ("yes", "no"):
        credit = ""
    has_operation = str(raw.get("has_operation") or "").strip()
    if has_operation not in ("yes", "no"):
        has_operation = ""
    purpose = str(raw.get("purpose") or "").strip()
    if purpose not in ("sale", "rent"):
        purpose = ""
    return {
        "view": view,
        "q": str(raw.get("q") or "").strip(),
        "status": str(raw.get("status") or "").strip(),
        "agent_id": raw.get("agent_id") or "",
        "agent_query": str(raw.get("agent_query") or "").strip(),
        "address": str(raw.get("address") or "").strip(),
        "client": str(raw.get("client") or "").strip(),
        "purpose": purpose,
        "payment_method": str(raw.get("payment_method") or "").strip(),
        "credit": credit,
        "next_milestone": str(raw.get("next_milestone") or "").strip(),
        "close_from": str(raw.get("close_from") or "").strip(),
        "close_to": str(raw.get("close_to") or "").strip(),
        "reserved_from": str(raw.get("reserved_from") or "").strip(),
        "reserved_to": str(raw.get("reserved_to") or "").strip(),
        "has_operation": has_operation,
    }


def _matches_text(haystack, needle):
    folded = fold_text(needle)
    if not folded:
        return True
    return folded in fold_text(haystack)


def _in_view(row, view, today):
    status = row.get("reservation_status")
    if view == "history":
        return status in ("closed", "cancelled")
    if view == "financing":
        return status == "financing"
    if view == "closing":
        return is_closing_soon(row, today)
    if view == "delayed":
        return is_delayed(row, today)
    if view in ("active", "agent"):
        return status in OPEN_STATUSES
    return True


def _matches_filters(row, filters):
    if filters["status"] and row.get("reservation_status") != filters["status"]:
        return False
    if filters["agent_id"]:
        try:
            if int(row.get("agent_id") or 0) != int(filters["agent_id"]):
                return False
        except (TypeError, ValueError):
            return False
    elif filters["agent_query"] and not _matches_text(
        row.get("agent_name"),
        filters["agent_query"],
    ):
        return False
    if filters["address"] and not _matches_text(
        " ".join(
            [
                str(row.get("property_address") or ""),
                str(row.get("zone") or ""),
                str(row.get("external_ref") or ""),
            ]
        ),
        filters["address"],
    ):
        return False
    if filters["client"] and not _matches_text(row.get("contact_name"), filters["client"]):
        return False
    if filters["purpose"] and row.get("purpose_key") != filters["purpose"]:
        return False
    if filters["payment_method"] and row.get("payment_method") != filters["payment_method"]:
        return False
    if filters["credit"] == "yes" and row.get("payment_method") not in CREDIT_PAYMENTS:
        return False
    if filters["credit"] == "no" and row.get("payment_method") in CREDIT_PAYMENTS:
        return False
    if filters["next_milestone"] and row.get("next_milestone") != filters["next_milestone"]:
        return False
    if filters["has_operation"] == "yes" and not row.get("operation_id"):
        return False
    if filters["has_operation"] == "no" and row.get("operation_id"):
        return False
    closing = _close_date(row)
    close_from = parse_board_date(filters["close_from"])
    close_to = parse_board_date(filters["close_to"])
    if close_from or close_to:
        if closing is None:
            return False
        if close_from and closing < close_from:
            return False
        if close_to and closing > close_to:
            return False
    reserved = parse_board_date(row.get("reserved_at"))
    reserved_from = parse_board_date(filters["reserved_from"])
    reserved_to = parse_board_date(filters["reserved_to"])
    if reserved_from or reserved_to:
        if reserved is None:
            return False
        if reserved_from and reserved < reserved_from:
            return False
        if reserved_to and reserved > reserved_to:
            return False
    if filters["q"]:
        blob = " ".join(
            [
                str(row.get("property_address") or ""),
                str(row.get("contact_name") or ""),
                str(row.get("agent_name") or ""),
                str(row.get("zone") or ""),
                str(row.get("external_ref") or ""),
            ]
        )
        if not _matches_text(blob, filters["q"]):
            return False
    return True


def _add_money(bucket, amount, currency):
    code = str(currency or "").strip().upper()
    if code not in bucket:
        return
    number = _money(amount)
    if number is None:
        return
    bucket[code] += number


def _amount_text(number):
    if number == number.to_integral():
        return f"{int(number):,}".replace(",", ".")
    return format(number, "f").rstrip("0").rstrip(".")


def _money_lines(bucket):
    lines = []
    for code in ("USD", "ARS"):
        lines.append(
            {
                "currency": code,
                "label": format_money(bucket[code], code),
                "amount": _amount_text(bucket[code]),
            }
        )
    return lines


def _counts(rows, today):
    return {
        "active": sum(1 for row in rows if row.get("reservation_status") in OPEN_STATUSES),
        "closing": sum(1 for row in rows if is_closing_soon(row, today)),
        "financing": sum(1 for row in rows if row.get("reservation_status") == "financing"),
        "history": sum(
            1 for row in rows if row.get("reservation_status") in ("closed", "cancelled")
        ),
        "delayed": sum(1 for row in rows if is_delayed(row, today)),
    }


def _kpis(rows):
    agreed = {"USD": Decimal("0"), "ARS": Decimal("0")}
    deposits = {"USD": Decimal("0"), "ARS": Decimal("0")}
    for row in rows:
        if row.get("reservation_status") not in OPEN_STATUSES:
            continue
        _add_money(agreed, row.get("agreed_property_price"), row.get("agreed_currency"))
        _add_money(deposits, row.get("reservation_amount"), row.get("reservation_currency"))
    return {"agreed": _money_lines(agreed), "deposits": _money_lines(deposits)}


def _groups(rows):
    buckets = {}
    order = []
    for row in rows:
        key = row.get("agent_id")
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(row)
    return [
        {
            "agent_name": (buckets[key][0].get("agent_name") or "—") if buckets[key] else "—",
            "rows": buckets[key],
        }
        for key in order
    ]


def build_reservation_board(organization_id, *, agent_id=None, filters=None, today=None):
    """One list query, one cover query, one operation query. Filters stay in memory."""
    today = today or date.today()
    selected = _blank_filters(filters)
    if agent_id is not None:
        selected["agent_id"] = ""
        selected["agent_query"] = ""
    rows = list_visible_reservations(organization_id, agent_id=agent_id, filters={})
    property_ids = [row.get("property_id") for row in rows if row.get("property_id")]
    covers = _covers_by_property(organization_id, property_ids)
    operations = _operations_by_id(
        organization_id,
        [row.get("operation_id") for row in rows if row.get("operation_id")],
    )
    presented = []
    for row in rows:
        property_id = row.get("property_id")
        try:
            cover_url = covers.get(int(property_id)) if property_id else None
        except (TypeError, ValueError):
            cover_url = None
        operation = None
        if row.get("operation_id"):
            try:
                operation = operations.get(int(row["operation_id"]))
            except (TypeError, ValueError):
                operation = None
        presented.append(
            present_reservation(
                row,
                cover_url=cover_url,
                operation=operation,
                today=today,
            )
        )
    counts = _counts(presented, today)
    kpis = _kpis(presented)
    visible = [
        row
        for row in presented
        if _in_view(row, selected["view"], today) and _matches_filters(row, selected)
    ]
    return {
        "rows": visible,
        "groups": _groups(visible) if selected["view"] == "agent" else [],
        "counts": counts,
        "kpis": kpis,
        "filters": selected,
        "today": today.isoformat(),
    }


def board_milestones():
    seen = []
    for key in SALE_MILESTONES + RENT_MILESTONES:
        if key not in seen:
            seen.append(key)
    return seen


def board_filter_choices():
    return {
        "statuses": STATUSES,
        "payments": PAYMENT_METHODS,
        "milestones": board_milestones(),
    }
