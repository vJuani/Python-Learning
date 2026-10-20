"""Turn a confirmed reservation into one closed operation.

A pre-close operation is a technical row for billing. It stays hidden until
the same row is marked closed. At most one operation is linked to the reservation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from modules.config import BACKEND_POSTGRES, get_database_backend
from modules.database.connection import get_connection
from modules.database.operation_parties_repository import create_parties_for_new_operation
from modules.database.operations_repository import add_operation
from modules.database.reservations_repository import (
    add_reservation_event,
    get_reservation,
    insert_reservation_event,
    update_reservation_fields,
)
from modules.operation_prefill import preliminary_commission_rate
from modules.operations import prepare_new_operation_from_form
from modules.organization_time import now_utc, to_utc_iso
from modules.reservations import ReservationError


OPERATION_PRECLOSE = "preclose"
OPERATION_CLOSED = "closed"
RESERVATION_PRE_CLOSE = "pre_close"


def open_preclose(organization_id, reservation_id, *, actor_user_id):
    current = get_reservation(reservation_id, organization_id)
    if current is None:
        raise ReservationError("reservation_err_missing")
    if current["reservation_status"] == RESERVATION_PRE_CLOSE:
        return current
    if current["reservation_status"] != "confirmed":
        raise ReservationError("reservation_err_not_confirmed")
    instant = to_utc_iso(now_utc())
    updated = update_reservation_fields(
        organization_id,
        reservation_id,
        {
            "reservation_status": RESERVATION_PRE_CLOSE,
            "updated_at": instant,
        },
    )
    add_reservation_event(
        organization_id,
        reservation_id,
        "pre_close",
        actor_user_id=actor_user_id,
        payload={},
        created_at=instant,
    )
    return updated


def begin_billing(
    organization_id,
    reservation_id,
    *,
    actor_user_id,
    seller_vat="0",
    buyer_vat="0",
    exchange_rate="",
):
    """Create or reuse the hidden preclose operation. Does not close the deal."""
    return _persist(
        organization_id,
        reservation_id,
        actor_user_id=actor_user_id,
        target_status=OPERATION_PRECLOSE,
        seller_vat=seller_vat,
        buyer_vat=buyer_vat,
        exchange_rate=exchange_rate,
    )


def confirm_close(
    organization_id,
    reservation_id,
    *,
    actor_user_id,
    seller_vat="0",
    buyer_vat="0",
    exchange_rate="",
):
    """Close without billing, or promote the existing preclose row. One transaction."""
    operation_id = _persist(
        organization_id,
        reservation_id,
        actor_user_id=actor_user_id,
        target_status=OPERATION_CLOSED,
        seller_vat=seller_vat,
        buyer_vat=buyer_vat,
        exchange_rate=exchange_rate,
    )
    from modules.agent_wallet import post_wallet_for_approved_operation

    post_wallet_for_approved_operation(organization_id, operation_id)
    return operation_id


def revert_preclose(organization_id, reservation_id, *, actor_user_id):
    """Back to confirmed. Refused once an invoice was issued."""
    connection, cursor = _begin()
    try:
        current = _lock_reservation(cursor, organization_id, reservation_id)
        if current is None:
            raise ReservationError("reservation_err_missing")
        if current["reservation_status"] != RESERVATION_PRE_CLOSE:
            raise ReservationError("reservation_err_not_confirmed")
        operation_id = current.get("operation_id")
        if operation_id:
            status = _operation_status(cursor, organization_id, operation_id)
            if status == OPERATION_CLOSED:
                raise ReservationError("reservation_err_closed")
            if status != OPERATION_PRECLOSE:
                raise ReservationError("reservation_err_close")
            if _has_issued_invoice(cursor, organization_id, operation_id):
                raise ReservationError("reservation_err_preclose_invoiced")
            _delete_preclose_operation(cursor, organization_id, operation_id, reservation_id)
        instant = to_utc_iso(now_utc())
        cursor.execute(
            """
            UPDATE reservations
            SET reservation_status = ?, operation_id = NULL, updated_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            ("confirmed", instant, reservation_id, organization_id),
        )
        insert_reservation_event(
            cursor,
            {
                "organization_id": organization_id,
                "reservation_id": reservation_id,
                "event_type": "pre_close_reverted",
                "actor_user_id": actor_user_id,
                "payload": {},
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


def operation_has_issued_invoice(organization_id, operation_id):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        return _has_issued_invoice(cursor, organization_id, operation_id)
    finally:
        connection.close()


def preclose_side_billing(organization_id, operation):
    """Billing presence for each participating side, from invoice rows."""
    from modules.invoicing import get_operation_sides_state

    if not operation or not operation.get("db_id"):
        return []
    sides = get_operation_sides_state(operation, organization_id)
    from modules.database.operation_parties_repository import get_parties_for_operation

    rows = []
    for party in get_parties_for_operation(organization_id, operation["db_id"]):
        if not party.get("is_participating"):
            continue
        info = sides.get(party["party_role"]) or {}
        state, invoice = _invoice_presence(info.get("invoices") or [])
        rows.append(
            {
                "party": party,
                "state": state,
                "invoice": invoice,
            }
        )
    return rows


def preclose_can_confirm(rows):
    return bool(rows) and all(row["state"] == "issued" for row in rows)


def first_billing_side(reservation):
    if reservation.get("buyer_commission_amount") not in (None, "", 0, 0.0):
        return "buyer"
    return "seller"


def _persist(
    organization_id,
    reservation_id,
    *,
    actor_user_id,
    target_status,
    seller_vat,
    buyer_vat,
    exchange_rate,
):
    connection, cursor = _begin()
    created_id = None
    try:
        current = _lock_reservation(cursor, organization_id, reservation_id)
        if current is None:
            raise ReservationError("reservation_err_missing")
        if current["reservation_status"] not in (RESERVATION_PRE_CLOSE, "confirmed"):
            if current["reservation_status"] == "closed" and current.get("operation_id"):
                return current["operation_id"]
            raise ReservationError("reservation_err_not_confirmed")
        if current["reservation_status"] == "confirmed":
            current["reservation_status"] = RESERVATION_PRE_CLOSE

        operation_id = current.get("operation_id")
        if operation_id:
            status = _operation_status(cursor, organization_id, operation_id)
            if status == OPERATION_CLOSED:
                return operation_id
            if status != OPERATION_PRECLOSE:
                raise ReservationError("reservation_err_close")
            if target_status == OPERATION_PRECLOSE:
                _enable_participating_billing(
                    cursor,
                    organization_id,
                    operation_id,
                    actor_user_id,
                )
                connection.commit()
                return operation_id
            missing = _sides_missing_issued_invoice(
                cursor,
                organization_id,
                operation_id,
            )
            if missing:
                raise ReservationError("reservation_err_preclose_invoices_pending")
            _mark_closed(cursor, organization_id, current, operation_id, actor_user_id)
            connection.commit()
            return operation_id

        calculated = _calculate(
            organization_id,
            current,
            seller_vat=seller_vat,
            buyer_vat=buyer_vat,
            exchange_rate=exchange_rate,
        )
        if _property_is_used(cursor, organization_id, current["property_id"]):
            raise ReservationError("reservation_err_close")
        created_id = _insert_operation(
            connection,
            organization_id,
            current,
            calculated,
            status=target_status,
            actor_user_id=actor_user_id,
        )
        instant = to_utc_iso(now_utc())
        reservation_status = (
            "closed" if target_status == OPERATION_CLOSED else RESERVATION_PRE_CLOSE
        )
        cursor.execute(
            """
            UPDATE reservations
            SET reservation_status = ?, operation_id = ?, updated_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            (reservation_status, created_id, instant, reservation_id, organization_id),
        )
        if target_status == OPERATION_PRECLOSE:
            _enable_participating_billing(
                cursor,
                organization_id,
                created_id,
                actor_user_id,
            )
        _attach_documents(cursor, organization_id, reservation_id, created_id)
        insert_reservation_event(
            cursor,
            {
                "organization_id": organization_id,
                "reservation_id": reservation_id,
                "event_type": "closed" if target_status == OPERATION_CLOSED else "preclose_operation",
                "actor_user_id": actor_user_id,
                "payload": {"operation_id": created_id},
                "created_at": instant,
            },
        )
        if target_status == OPERATION_CLOSED:
            _mark_property_closed(cursor, organization_id, current, actor_user_id, instant)
        connection.commit()
        return created_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _begin():
    connection = get_connection()
    cursor = connection.cursor()
    if get_database_backend() != BACKEND_POSTGRES:
        cursor.execute("BEGIN IMMEDIATE")
    return connection, cursor


def _lock_reservation(cursor, organization_id, reservation_id):
    sql = """
        SELECT
            id, reservation_status, operation_id, property_id, agent_id,
            agreed_currency, agreed_property_price,
            seller_commission_amount, buyer_commission_amount,
            reservation_amount, reservation_currency, visit_id
        FROM reservations
        WHERE organization_id = ? AND id = ?
    """
    if get_database_backend() == BACKEND_POSTGRES:
        sql += " FOR UPDATE"
    cursor.execute(sql, (organization_id, reservation_id))
    row = cursor.fetchone()
    if row is None:
        return None
    keys = (
        "id",
        "reservation_status",
        "operation_id",
        "property_id",
        "agent_id",
        "agreed_currency",
        "agreed_property_price",
        "seller_commission_amount",
        "buyer_commission_amount",
        "reservation_amount",
        "reservation_currency",
        "visit_id",
    )
    return dict(zip(keys, row))


def _operation_status(cursor, organization_id, operation_id):
    cursor.execute(
        """
        SELECT status
        FROM operations
        WHERE id = ? AND organization_id = ?
        """,
        (operation_id, organization_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return row[0]


def _property_is_used(cursor, organization_id, property_id):
    cursor.execute(
        """
        SELECT 1
        FROM operations
        WHERE organization_id = ? AND property_id = ?
            AND COALESCE(status, 'approved') != 'preclose_void'
        LIMIT 1
        """,
        (organization_id, property_id),
    )
    return cursor.fetchone() is not None


def _invoice_presence(invoices):
    """issued wins. draft and ready_to_issue are non-fiscal drafts."""
    active = [
        invoice
        for invoice in invoices
        if invoice.get("status") in ("draft", "ready_to_issue", "issued", "error")
    ]
    issued = next(
        (invoice for invoice in active if invoice.get("status") == "issued"),
        None,
    )
    if issued is not None:
        return "issued", issued
    draft = next(
        (
            invoice
            for invoice in active
            if invoice.get("status") in ("draft", "ready_to_issue", "error")
        ),
        None,
    )
    if draft is not None:
        return "draft", draft
    return "none", None


def _sides_missing_issued_invoice(cursor, organization_id, operation_id):
    cursor.execute(
        """
        SELECT party_role
        FROM operation_parties
        WHERE organization_id = ?
            AND operation_id = ?
            AND is_participating = 1
        ORDER BY party_role
        """,
        (organization_id, operation_id),
    )
    participating = [row[0] for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT DISTINCT side
        FROM invoices
        WHERE organization_id = ?
            AND operation_id = ?
            AND status = 'issued'
            AND side IS NOT NULL
        """,
        (organization_id, operation_id),
    )
    issued = {row[0] for row in cursor.fetchall()}
    return [side for side in participating if side not in issued]


def _has_issued_invoice(cursor, organization_id, operation_id):
    cursor.execute(
        """
        SELECT 1
        FROM invoices
        WHERE organization_id = ? AND operation_id = ? AND status = 'issued'
        LIMIT 1
        """,
        (organization_id, operation_id),
    )
    return cursor.fetchone() is not None


def _calculate(organization_id, reservation, *, seller_vat, buyer_vat, exchange_rate):
    full = get_reservation(reservation["id"], organization_id) or reservation
    form = _form_values(
        full,
        seller_vat=seller_vat,
        buyer_vat=buyer_vat,
        exchange_rate=exchange_rate,
    )
    errors, operation, _parsed = prepare_new_operation_from_form(form, organization_id)
    if errors:
        key = errors[0] if isinstance(errors[0], str) and " " not in errors[0] else "reservation_err_close"
        raise ReservationError(key)
    return operation


def _form_values(reservation, *, seller_vat, buyer_vat, exchange_rate):
    price = reservation.get("agreed_property_price")
    currency = reservation.get("agreed_currency") or reservation.get("currency") or "USD"
    seller_amount = reservation.get("seller_commission_amount")
    buyer_amount = reservation.get("buyer_commission_amount")
    seller_on = _side_on(seller_amount)
    buyer_on = _side_on(buyer_amount)
    if not seller_on and not buyer_on:
        raise ReservationError("reservation_err_commission")
    values = {
        "agent_id": str(reservation["agent_id"]),
        "property_id": str(reservation["property_id"]),
        "currency": currency,
        "original_amount": _plain_amount(price),
        "seller_side_active": "1" if seller_on else "",
        "buyer_side_active": "1" if buyer_on else "",
        "seller_commission_rate": _rate(price, seller_amount) if seller_on else "",
        "buyer_commission_rate": _rate(price, buyer_amount) if buyer_on else "",
        "seller_vat_amount": seller_vat or "0",
        "buyer_vat_amount": buyer_vat or "0",
        "operation_date": date.today().strftime("%d/%m/%Y"),
    }
    if currency != "USD":
        values["exchange_rate"] = exchange_rate or ""
    return values


def _side_on(amount):
    if amount in (None, ""):
        return False
    try:
        return Decimal(str(amount)) > 0
    except Exception:
        return False


def _plain_amount(value):
    if value in (None, ""):
        return ""
    return format(Decimal(str(value)), "f")


def _rate(price, amount):
    rate = preliminary_commission_rate(amount, price)
    if rate is None:
        raise ReservationError("reservation_err_commission")
    return format(Decimal(str(rate)), "f")


def _insert_operation(connection, organization_id, reservation, operation, *, status, actor_user_id):
    side_data = operation.get("side_data") or {}
    operation_id = add_operation(
        operation["date"],
        reservation["agent_id"],
        reservation["property_id"],
        "no",
        operation["vat_amount"],
        operation["sale_price"],
        operation["commission_rate"],
        operation["total_commission"],
        operation["commission_after_abao"],
        operation["abao"],
        operation["martillero"],
        operation["agent_payment"],
        operation["office_payment"],
        operation["office_total"],
        organization_id,
        currency=operation.get("currency", "USD"),
        original_amount=operation.get("original_amount", operation["sale_price"]),
        exchange_rate=operation.get("exchange_rate", 1),
        status=status,
        created_by_user_id=actor_user_id,
        invoice_full_commission="no",
        is_referred=operation.get("is_referred", False),
        referred_side=operation.get("referred_side"),
        seller_vat_original=operation.get("seller_vat_original", 0),
        buyer_vat_original=operation.get("buyer_vat_original", 0),
        connection=connection,
    )
    if side_data:
        create_parties_for_new_operation(
            organization_id,
            operation_id,
            seller_active=side_data["seller_active"],
            buyer_active=side_data["buyer_active"],
            seller_commission_percent=side_data["seller_commission_percent"],
            buyer_commission_percent=side_data["buyer_commission_percent"],
            seller_commission_amount=side_data["seller_commission_amount"],
            buyer_commission_amount=side_data["buyer_commission_amount"],
            connection=connection,
        )
    return operation_id


def _mark_closed(cursor, organization_id, reservation, operation_id, actor_user_id):
    instant = to_utc_iso(now_utc())
    today = date.today().strftime("%d/%m/%Y")
    cursor.execute(
        """
        UPDATE operations
        SET status = ?, operation_date = ?
        WHERE id = ? AND organization_id = ? AND status = ?
        """,
        (OPERATION_CLOSED, today, operation_id, organization_id, OPERATION_PRECLOSE),
    )
    cursor.execute(
        """
        UPDATE reservations
        SET reservation_status = ?, operation_id = ?, updated_at = ?
        WHERE id = ? AND organization_id = ?
        """,
        ("closed", operation_id, instant, reservation["id"], organization_id),
    )
    _attach_documents(cursor, organization_id, reservation["id"], operation_id)
    _mark_property_closed(cursor, organization_id, reservation, actor_user_id, instant)
    insert_reservation_event(
        cursor,
        {
            "organization_id": organization_id,
            "reservation_id": reservation["id"],
            "event_type": "closed",
            "actor_user_id": actor_user_id,
            "payload": {"operation_id": operation_id},
            "created_at": instant,
        },
    )


def _mark_property_closed(cursor, organization_id, reservation, actor_user_id, instant):
    cursor.execute(
        """
        SELECT listing_purpose, commercial_status, listing_price, listing_currency
        FROM properties
        WHERE id = ? AND organization_id = ?
        """,
        (reservation["property_id"], organization_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise ReservationError("reservation_err_missing")
    purpose, previous_status, price, price_currency = row
    commercial = "rented" if purpose in ("rental", "temporary_rental") else "sold"
    cursor.execute(
        """
        UPDATE properties
        SET commercial_status = ?
        WHERE id = ? AND organization_id = ?
        """,
        (commercial, reservation["property_id"], organization_id),
    )
    from modules.database.property_commercial_events_repository import (
        insert_property_commercial_event,
    )

    insert_property_commercial_event(
        cursor,
        {
            "organization_id": organization_id,
            "property_id": reservation["property_id"],
            "actor_user_id": actor_user_id,
            "agent_id": reservation.get("agent_id"),
            "task_id": reservation.get("visit_id"),
            "previous_commercial_status": previous_status,
            "commercial_status": commercial,
            "previous_price": price,
            "previous_currency": price_currency,
            "listing_price": price,
            "listing_currency": price_currency,
            "reservation_amount": reservation.get("reservation_amount"),
            "reservation_currency": reservation.get("reservation_currency"),
            "created_at": instant,
        },
    )


def ensure_preclose_billing(organization_id, operation_id, *, actor_user_id=None):
    """Keep office sides billable on a reservation preclose.

    The prepare link opens billing directly and never calls begin_billing.
    Creating the technical operation and opening it later must both satisfy
    the same rule. Manual operations are left untouched.
    """
    if not operation_id:
        return False
    connection, cursor = _begin()
    try:
        if _operation_status(cursor, organization_id, operation_id) != OPERATION_PRECLOSE:
            return False
        cursor.execute(
            """
            SELECT reservation_status
            FROM reservations
            WHERE organization_id = ? AND operation_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (organization_id, operation_id),
        )
        row = cursor.fetchone()
        if row is None or row[0] != RESERVATION_PRE_CLOSE:
            return False
        _enable_participating_billing(
            cursor,
            organization_id,
            operation_id,
            actor_user_id,
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _enable_participating_billing(cursor, organization_id, operation_id, actor_user_id):
    """Staff already chose billing. Participating sides do not wait for Treasury."""
    instant = to_utc_iso(now_utc())
    cursor.execute(
        """
        UPDATE operation_parties
        SET
            billing_enabled = 1,
            billing_enabled_at = ?,
            billing_enabled_by_user_id = ?,
            updated_at = ?
        WHERE organization_id = ?
            AND operation_id = ?
            AND is_participating = 1
            AND billing_enabled = 0
        """,
        (
            instant,
            actor_user_id,
            instant,
            organization_id,
            operation_id,
        ),
    )


def _attach_documents(cursor, organization_id, reservation_id, operation_id):
    cursor.execute(
        """
        UPDATE reservation_documents
        SET operation_id = ?
        WHERE organization_id = ? AND reservation_id = ?
            AND operation_id IS NULL
        """,
        (operation_id, organization_id, reservation_id),
    )


def _delete_preclose_operation(cursor, organization_id, operation_id, reservation_id):
    cursor.execute(
        """
        UPDATE reservation_documents
        SET operation_id = NULL
        WHERE organization_id = ? AND reservation_id = ? AND operation_id = ?
        """,
        (organization_id, reservation_id, operation_id),
    )
    cursor.execute(
        """
        DELETE FROM invoice_fiscal_events
        WHERE organization_id = ?
            AND invoice_id IN (
                SELECT id FROM invoices
                WHERE organization_id = ? AND operation_id = ?
            )
        """,
        (organization_id, organization_id, operation_id),
    )
    cursor.execute(
        """
        DELETE FROM invoices
        WHERE organization_id = ? AND operation_id = ?
        """,
        (organization_id, operation_id),
    )
    cursor.execute(
        """
        DELETE FROM operation_parties
        WHERE organization_id = ? AND operation_id = ?
        """,
        (organization_id, operation_id),
    )
    cursor.execute(
        """
        DELETE FROM operations
        WHERE id = ? AND organization_id = ? AND status = ?
        """,
        (operation_id, organization_id, OPERATION_PRECLOSE),
    )

