"""Reservation lifecycle. Commission numbers always come from the linked operation."""

from __future__ import annotations

import uuid
from pathlib import Path

from modules.config import get_private_upload_root
from modules.database.properties_repository import get_property_record
from modules.database.reservations_repository import (
    OPEN_STATUSES,
    add_reservation_event,
    add_reservation_note,
    attach_reservation_documents_to_operation,
    get_open_reservation_for_property,
    get_reservation,
    get_reservation_by_operation,
    get_reservation_document,
    insert_reservation_deposit,
    insert_reservation_document,
    list_reservation_deposits,
    list_reservation_documents,
    list_reservation_documents_for_operation,
    list_reservation_events,
    list_reservation_notes,
    list_reservations,
    update_reservation_fields,
)
from modules.organization_time import now_utc, to_utc_iso


PAYMENT_METHODS = ("cash", "mortgage", "mixed", "other")
SALE_MILESTONES = ("boleto", "mortgage", "deed", "possession", "undefined", "other")
RENT_MILESTONES = ("paperwork", "contract_sign", "keys", "move_in", "undefined", "other")
STATUSES = OPEN_STATUSES + ("closed", "cancelled")
PRICE_RESTORE = "restore_original"
PRICE_KEEP = "keep_agreed"
DOCUMENT_TYPES = (
    "uif",
    "invoice",
    "reservation_receipt",
    "reinforcement",
    "boleto",
    "deed",
    "credit",
    "other",
)
DEPOSIT_CURRENCIES = ("USD", "ARS")


class ReservationError(Exception):
    def __init__(self, message_key):
        self.message_key = message_key
        super().__init__(message_key)


def format_money(amount, currency):
    if amount in (None, ""):
        return ""
    from decimal import Decimal, InvalidOperation

    try:
        number = Decimal(str(amount))
    except InvalidOperation:
        return ""
    label = currency or "USD"
    if number == number.to_integral():
        grouped = f"{int(number):,}".replace(",", ".")
        return f"{label} {grouped}"
    return f"{label} {number}"


def milestones_for_purpose(listing_purpose):
    purpose = str(listing_purpose or "").strip()
    if purpose in ("rental", "temporary_rental"):
        return RENT_MILESTONES
    return SALE_MILESTONES


def list_visible_reservations(organization_id, *, agent_id=None, filters=None):
    filters = filters or {}
    date_to = filters.get("date_to") or None
    if date_to and len(str(date_to)) == 10:
        date_to = f"{date_to}T23:59:59"
    return list_reservations(
        organization_id,
        agent_id=agent_id,
        status=(filters.get("status") or None),
        property_id=_optional_int(filters.get("property_id")),
        date_from=(filters.get("date_from") or None),
        date_to=date_to,
    )


def load_reservation_detail(organization_id, reservation_id, *, agent_id=None):
    row = get_reservation(reservation_id, organization_id)
    if row is None:
        return None
    if agent_id is not None and row.get("agent_id") != agent_id:
        return None
    property_row = get_property_record(row["property_id"], organization_id)
    row = dict(row)
    row["property_address"] = (property_row or {}).get("address")
    notes = list_reservation_notes(organization_id, reservation_id)
    events = list_reservation_events(organization_id, reservation_id)
    documents = list_reservation_documents(organization_id, reservation_id)
    deposits = list_reservation_deposits(organization_id, reservation_id)
    operation = None
    if row.get("operation_id"):
        from modules.database.operations_repository import get_operation_record

        operation = get_operation_record(row["operation_id"], organization_id)
    return {
        "reservation": row,
        "property": property_row,
        "notes": notes,
        "events": events,
        "documents": documents,
        "deposits": deposits,
        "deposit_totals": grouped_deposit_totals(row, deposits),
        "operation": operation,
        "milestones": milestones_for_purpose(
            (property_row or {}).get("listing_purpose")
        ),
    }


def update_reservation(organization_id, reservation_id, fields, *, actor_user_id):
    current = get_reservation(reservation_id, organization_id)
    if current is None:
        raise ReservationError("reservation_err_missing")
    if current["reservation_status"] in ("closed", "cancelled"):
        raise ReservationError("reservation_err_closed")
    status = fields.get("reservation_status") or current["reservation_status"]
    if status == "confirmed" and current["reservation_status"] != "confirmed":
        status = current["reservation_status"]
    if status not in STATUSES or status in ("closed", "cancelled", "in_operation"):
        status = current["reservation_status"]
    milestone = fields.get("next_milestone") or current.get("next_milestone")
    property_row = get_property_record(current["property_id"], organization_id)
    allowed = milestones_for_purpose((property_row or {}).get("listing_purpose"))
    if milestone and milestone not in allowed:
        raise ReservationError("reservation_err_milestone")
    payment = fields.get("payment_method") or current.get("payment_method")
    if payment and payment not in PAYMENT_METHODS:
        raise ReservationError("reservation_err_payment")
    instant = to_utc_iso(now_utc())
    updated = update_reservation_fields(
        organization_id,
        reservation_id,
        {
            "reservation_status": status,
            "payment_method": payment,
            "next_milestone": milestone,
            "estimated_closing_date": fields.get("estimated_closing_date")
            or current.get("estimated_closing_date"),
            "notes": fields.get("notes") if fields.get("notes") is not None else current.get("notes"),
            "updated_at": instant,
        },
    )
    add_reservation_event(
        organization_id,
        reservation_id,
        "updated",
        actor_user_id=actor_user_id,
        payload={"reservation_status": status, "next_milestone": milestone},
        created_at=instant,
    )
    return updated


def add_note(organization_id, reservation_id, body, *, actor_user_id):
    text = (body or "").strip()
    if not text:
        raise ReservationError("reservation_err_note")
    if get_reservation(reservation_id, organization_id) is None:
        raise ReservationError("reservation_err_missing")
    instant = to_utc_iso(now_utc())
    return add_reservation_note(
        organization_id,
        reservation_id,
        text,
        actor_user_id=actor_user_id,
        created_at=instant,
    )


def cancel_reservation(
    organization_id,
    reservation_id,
    *,
    actor_user_id,
    keep_negotiated_price=False,
):
    current = get_reservation(reservation_id, organization_id)
    if current is None:
        raise ReservationError("reservation_err_missing")
    if current["reservation_status"] not in OPEN_STATUSES:
        raise ReservationError("reservation_err_closed")
    decision = PRICE_KEEP if keep_negotiated_price else PRICE_RESTORE
    instant = to_utc_iso(now_utc())
    from modules.database.connection import get_connection
    from modules.database.property_commercial_events_repository import (
        insert_property_commercial_event,
    )

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT commercial_status, listing_price, listing_currency
            FROM properties
            WHERE id = ? AND organization_id = ?
            """,
            (current["property_id"], organization_id),
        )
        property_row = cursor.fetchone()
        if property_row is None:
            raise ReservationError("reservation_err_missing")
        other_open = get_open_reservation_for_property(
            organization_id, current["property_id"]
        )
        another = other_open and other_open["id"] != current["id"]
        next_status = property_row[0]
        next_price = property_row[1]
        next_currency = property_row[2]
        if not another:
            next_status = "available"
            if decision == PRICE_RESTORE:
                next_price = current.get("original_property_price")
                next_currency = current.get("original_currency") or next_currency
            cursor.execute(
                """
                UPDATE properties
                SET commercial_status = ?, listing_price = ?, listing_currency = ?
                WHERE id = ? AND organization_id = ?
                """,
                (
                    next_status,
                    next_price,
                    next_currency,
                    current["property_id"],
                    organization_id,
                ),
            )
        cursor.execute(
            """
            UPDATE reservations
            SET reservation_status = ?, price_decision = ?, cancelled_by_user_id = ?,
                cancelled_at = ?, updated_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            (
                "cancelled",
                decision,
                actor_user_id,
                instant,
                instant,
                reservation_id,
                organization_id,
            ),
        )
        insert_property_commercial_event(
            cursor,
            {
                "organization_id": organization_id,
                "property_id": current["property_id"],
                "actor_user_id": actor_user_id,
                "agent_id": current.get("agent_id"),
                "task_id": current.get("visit_id"),
                "previous_commercial_status": property_row[0],
                "commercial_status": next_status,
                "previous_price": property_row[1],
                "previous_currency": property_row[2],
                "listing_price": next_price if decision == PRICE_RESTORE and not another else None,
                "listing_currency": next_currency if decision == PRICE_RESTORE and not another else None,
                "reservation_amount": current.get("reservation_amount"),
                "reservation_currency": current.get("reservation_currency"),
                "created_at": instant,
            },
        )
        from modules.database.reservations_repository import insert_reservation_event

        insert_reservation_event(
            cursor,
            {
                "organization_id": organization_id,
                "reservation_id": reservation_id,
                "event_type": "cancelled",
                "actor_user_id": actor_user_id,
                "payload": {"price_decision": decision},
                "created_at": instant,
            },
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_reservation(reservation_id, organization_id)


def link_operation(organization_id, reservation_id, operation_id, *, actor_user_id):
    current = get_reservation(reservation_id, organization_id)
    if current is None:
        raise ReservationError("reservation_err_missing")
    instant = to_utc_iso(now_utc())
    updated = update_reservation_fields(
        organization_id,
        reservation_id,
        {
            "operation_id": operation_id,
            "reservation_status": "in_operation",
            "updated_at": instant,
        },
    )
    add_reservation_event(
        organization_id,
        reservation_id,
        "operation_linked",
        actor_user_id=actor_user_id,
        payload={"operation_id": operation_id},
        created_at=instant,
    )
    attach_reservation_documents_to_operation(
        organization_id, reservation_id, operation_id
    )
    return updated


def sync_reservation_from_operation(organization_id, operation_id):
    """When Operaciones marks the deal invoiced, close the reservation and the listing."""
    from modules.database.operations_repository import get_operation_record

    reservation = get_reservation_by_operation(organization_id, operation_id)
    if reservation is None or reservation["reservation_status"] in ("closed", "cancelled"):
        return reservation
    operation = get_operation_record(operation_id, organization_id)
    if operation is None:
        return reservation
    if (operation.get("was_invoiced") or "no") != "yes":
        return reservation
    property_row = get_property_record(reservation["property_id"], organization_id)
    purpose = (property_row or {}).get("listing_purpose")
    final_status = "rented" if purpose in ("rental", "temporary_rental") else "sold"
    instant = to_utc_iso(now_utc())
    from modules.database.connection import get_connection

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE properties
            SET commercial_status = ?
            WHERE id = ? AND organization_id = ?
            """,
            (final_status, reservation["property_id"], organization_id),
        )
        cursor.execute(
            """
            UPDATE reservations
            SET reservation_status = ?, updated_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            ("closed", instant, reservation["id"], organization_id),
        )
        from modules.database.reservations_repository import insert_reservation_event

        insert_reservation_event(
            cursor,
            {
                "organization_id": organization_id,
                "reservation_id": reservation["id"],
                "event_type": "closed",
                "actor_user_id": None,
                "payload": {
                    "operation_id": operation_id,
                    "commercial_status": final_status,
                },
                "created_at": instant,
            },
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_reservation(reservation["id"], organization_id)


def confirm_reservation(organization_id, reservation_id, *, actor_user_id):
    current = get_reservation(reservation_id, organization_id)
    if current is None:
        raise ReservationError("reservation_err_missing")
    if current["reservation_status"] != "reserved":
        raise ReservationError("reservation_err_confirm")
    instant = to_utc_iso(now_utc())
    updated = update_reservation_fields(
        organization_id,
        reservation_id,
        {
            "reservation_status": "confirmed",
            "confirmed_at": instant,
            "confirmed_by_user_id": actor_user_id,
            "updated_at": instant,
        },
    )
    add_reservation_event(
        organization_id,
        reservation_id,
        "confirmed",
        actor_user_id=actor_user_id,
        payload={"reservation_status": "confirmed"},
        created_at=instant,
    )
    return updated


def add_reinforcement(
    organization_id,
    reservation_id,
    *,
    amount,
    currency,
    note,
    deposited_at,
    actor_user_id,
):
    from modules.visit_outcome import parse_jrh_money

    current = get_reservation(reservation_id, organization_id)
    if current is None:
        raise ReservationError("reservation_err_missing")
    if current["reservation_status"] in ("closed", "cancelled"):
        raise ReservationError("reservation_err_closed")
    parsed = parse_jrh_money(amount)
    if parsed is None:
        raise ReservationError("reservation_err_deposit")
    code = str(currency or "").strip().upper()
    if code not in DEPOSIT_CURRENCIES:
        raise ReservationError("reservation_err_deposit")
    instant = to_utc_iso(now_utc())
    insert_reservation_deposit(
        organization_id,
        {
            "reservation_id": reservation_id,
            "deposit_type": "reinforcement",
            "amount": parsed,
            "currency": code,
            "note": (note or "").strip() or None,
            "deposited_at": (deposited_at or "").strip() or None,
            "created_by_user_id": actor_user_id,
            "created_at": instant,
        },
    )
    add_reservation_event(
        organization_id,
        reservation_id,
        "reinforcement",
        actor_user_id=actor_user_id,
        payload={"amount": parsed, "currency": code},
        created_at=instant,
    )
    return get_reservation(reservation_id, organization_id)


def grouped_deposit_totals(reservation, deposits):
    from decimal import Decimal, InvalidOperation

    buckets = {}

    def add(currency, amount):
        if amount in (None, ""):
            return
        try:
            number = Decimal(str(amount))
        except InvalidOperation:
            return
        key = currency or "USD"
        buckets[key] = buckets.get(key, Decimal("0")) + number

    add(reservation.get("reservation_currency"), reservation.get("reservation_amount"))
    for item in deposits or []:
        add(item.get("currency"), item.get("amount"))
    return [
        {"currency": key, "amount": value, "label": format_money(value, key)}
        for key, value in buckets.items()
    ]


def save_document(organization_id, reservation_id, upload, doc_type, *, actor_user_id):
    reservation = get_reservation(reservation_id, organization_id)
    if reservation is None:
        raise ReservationError("reservation_err_missing")
    if doc_type not in DOCUMENT_TYPES:
        raise ReservationError("reservation_err_document")
    filename = Path(getattr(upload, "filename", "") or "document").name
    if not filename:
        raise ReservationError("reservation_err_document")
    payload = upload.read()
    closer = getattr(upload, "close", None)
    if closer:
        closer()
    if not payload:
        raise ReservationError("reservation_err_document")
    stored_name = f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
    directory = (
        get_private_upload_root()
        / "organizations"
        / str(organization_id)
        / "reservations"
        / str(reservation_id)
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / stored_name).resolve()
    if directory.resolve() not in target.parents:
        raise ReservationError("reservation_err_document")
    target.write_bytes(payload)
    instant = to_utc_iso(now_utc())
    return insert_reservation_document(
        organization_id,
        {
            "reservation_id": reservation_id,
            "doc_type": doc_type,
            "original_filename": filename,
            "stored_name": stored_name,
            "content_type": getattr(upload, "mimetype", None),
            "size_bytes": len(payload),
            "uploaded_by_user_id": actor_user_id,
            "created_at": instant,
            "operation_id": reservation.get("operation_id"),
        },
    )


def document_path(organization_id, document_id):
    document = get_reservation_document(organization_id, document_id)
    if document is None:
        return None, None
    path = (
        get_private_upload_root()
        / "organizations"
        / str(organization_id)
        / "reservations"
        / str(document["reservation_id"])
        / document["stored_name"]
    )
    return document, path


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
