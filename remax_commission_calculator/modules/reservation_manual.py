"""Staff manual reservation. Same row and insert as a visit close."""

from __future__ import annotations

from decimal import Decimal

from modules.database.agents_repository import get_agent_record, get_agents
from modules.database.connection import get_connection
from modules.database.contacts_repository import get_contact, list_contacts
from modules.database.properties_repository import get_property_record
from modules.database.property_commercial_events_repository import (
    insert_property_commercial_event,
)
from modules.database.reservations_repository import (
    ReservationConflict,
    get_open_reservation_for_property,
    insert_reservation,
    insert_reservation_event,
)
from modules.mortgage_banks import is_mortgage_bank, mortgage_banks
from modules.organization_time import now_utc, to_utc_iso
from modules.reservations import PAYMENT_METHODS, ReservationError, format_money
from modules.visit_close import (
    VisitCloseError,
    _prepare_reserved_outcome,
    _validate_reserved_outcome,
)
from modules.visit_outcome import parse_jrh_money


RENT_PURPOSES = ("rental", "temporary_rental")
UNAVAILABLE_COMMERCIAL = ("sold", "rented", "withdrawn")
CURRENCIES = ("USD", "ARS")


def purpose_key(listing_purpose):
    if str(listing_purpose or "").strip() in RENT_PURPOSES:
        return "rent"
    return "sale"


def manual_form_choices(organization_id):
    return {
        "properties": _reservable_properties(organization_id),
        "contacts": [
            {"id": row["id"], "name": row.get("name") or ""}
            for row in list_contacts(organization_id, limit=1000)
            if (row.get("name") or "").strip()
        ],
        "agents": [
            {"id": agent["id"], "name": agent["name"]}
            for agent in get_agents(organization_id)
            if agent.get("name")
        ],
        "banks": mortgage_banks(),
        "payments": PAYMENT_METHODS,
    }


def create_manual_reservation(organization_id, form, *, actor_user_id):
    form = form or {}
    property_row = _owned(
        get_property_record(_require_int(form.get("property_id")), organization_id),
        organization_id,
        "reservation_err_property",
    )
    contact = _owned(
        get_contact(_require_int(form.get("contact_id")), organization_id),
        organization_id,
        "reservation_err_contact",
    )
    agent = _owned(
        get_agent_record(_require_int(form.get("agent_id")), organization_id),
        organization_id,
        "reservation_err_agent",
    )
    purpose = str(form.get("purpose") or "").strip()
    if purpose not in ("sale", "rent") or purpose != purpose_key(property_row.get("listing_purpose")):
        raise ReservationError("reservation_err_purpose")
    if str(property_row.get("commercial_status") or "") in UNAVAILABLE_COMMERCIAL:
        raise ReservationError("reservation_err_unavailable")
    if get_open_reservation_for_property(organization_id, property_row["id"]):
        raise ReservationError("reservation_err_open")

    currency = str(form.get("currency") or "").strip().upper()
    if currency not in CURRENCIES:
        raise ReservationError("reservation_err_currency")
    price_change = str(form.get("price_change") or "").strip()
    if price_change not in ("keep", "change"):
        raise ReservationError("reservation_err_price")
    payment = str(form.get("payment_method") or "").strip()
    if payment not in PAYMENT_METHODS:
        raise ReservationError("reservation_err_payment")
    deal = str(form.get("deal_kind") or "").strip()
    shared = deal == "shared"
    if deal not in ("own", "shared"):
        raise ReservationError("reservation_err_shared")

    data = {
        "price_change": price_change,
        "estimated_closing_date": str(form.get("estimated_closing_date") or "").strip(),
        "payment_method": payment,
        "reservation_notes": str(form.get("notes") or "").strip(),
        "is_shared_transaction": 1 if shared else 0,
        "shared_brokerage_name": str(form.get("shared_brokerage_name") or "").strip(),
        "deposit_type": "reservation",
        "next_milestone": "keys" if purpose == "rent" else "boleto",
    }
    agreed_text = str(form.get("agreed_property_price") or "").strip()
    if price_change == "change":
        agreed = parse_jrh_money(agreed_text)
        if not agreed_text or agreed is None:
            data["agreed_property_price_invalid"] = True
        else:
            data["agreed_property_price"] = agreed
            data["agreed_property_currency"] = currency
    amount_text = str(form.get("reservation_amount") or "").strip()
    amount = parse_jrh_money(amount_text)
    if not amount_text or amount is None:
        data["reservation_amount_invalid"] = True
    else:
        data["reservation_amount"] = amount
        data["reservation_currency"] = currency
    seller = _optional_commission(form.get("seller_commission_amount"))
    buyer = _optional_commission(form.get("buyer_commission_amount"))
    if seller is not None:
        data["seller_commission_amount"] = seller
        data["seller_commission_currency"] = currency
    if buyer is not None:
        data["buyer_commission_amount"] = buyer
        data["buyer_commission_currency"] = currency
    try:
        _prepare_reserved_outcome(data, property_row)
        _validate_reserved_outcome(data)
    except VisitCloseError as error:
        raise ReservationError(error.message_key) from error

    credit, bank = _financing(form, purpose)
    return _commit(
        organization_id,
        property_row,
        contact,
        agent,
        data,
        credit=credit,
        bank=bank,
        actor_user_id=actor_user_id,
    )


def _financing(form, purpose):
    if purpose != "sale":
        return 0, None
    raw = str(form.get("purchase_with_credit") or "").strip()
    if raw not in ("0", "1"):
        raise ReservationError("reservation_err_credit")
    if raw == "0":
        return 0, None
    bank = str(form.get("financing_bank") or "").strip()
    if not is_mortgage_bank(bank):
        raise ReservationError("reservation_err_bank")
    return 1, bank


def _optional_commission(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    amount = parse_jrh_money(text)
    if amount is None or Decimal(str(amount)) <= 0:
        raise ReservationError("reservation_err_commission")
    return amount


def _require_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _owned(row, organization_id, error_key):
    if row is None or int(row.get("organization_id") or 0) != int(organization_id):
        raise ReservationError(error_key)
    return row


def _reservable_properties(organization_id):
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT properties.id, properties.address, properties.neighborhood,
                   properties.listing_purpose, properties.listing_price,
                   properties.listing_currency, properties.commercial_status,
                   agents.name
            FROM properties
            LEFT JOIN agents
                ON agents.id = properties.agent_id
                AND agents.organization_id = properties.organization_id
            WHERE properties.organization_id = ?
                AND (
                    properties.commercial_status IS NULL
                    OR properties.commercial_status NOT IN ('sold', 'rented', 'withdrawn', 'reserved')
                )
            ORDER BY properties.address, properties.id
            """,
            (organization_id,),
        )
        rows = cursor.fetchall()
    finally:
        connection.close()
    properties = []
    for row in rows:
        if get_open_reservation_for_property(organization_id, row[0]):
            continue
        properties.append(
            {
                "id": row[0],
                "address": row[1] or "",
                "neighborhood": row[2] or "",
                "purpose_key": purpose_key(row[3]),
                "price_label": format_money(row[4], row[5] or "USD"),
                "listing_currency": row[5] or "USD",
                "agent_name": row[7] or "",
            }
        )
    return properties


def _commit(
    organization_id,
    property_row,
    contact,
    agent,
    outcome,
    *,
    credit,
    bank,
    actor_user_id,
):
    property_id = property_row["id"]
    agreed = outcome.get("agreed_property_price")
    connection = get_connection()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        cursor.execute(
            """
            SELECT commercial_status, listing_price, listing_currency, status
            FROM properties
            WHERE id = ?
                AND organization_id = ?
            """,
            (property_id, organization_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ReservationError("reservation_err_property")
        previous_status, previous_price, previous_currency, approval_status = row
        if previous_status in UNAVAILABLE_COMMERCIAL:
            raise ReservationError("reservation_err_unavailable")
        assignments = ["commercial_status = ?"]
        params = ["reserved"]
        new_price = previous_price
        new_currency = previous_currency
        update_price = outcome.get("price_change") == "change" and agreed is not None
        if update_price:
            new_price = agreed
            new_currency = outcome.get("agreed_property_currency") or previous_currency or "USD"
            assignments.append("listing_price = ?")
            assignments.append("listing_currency = ?")
            params.extend([new_price, new_currency])
        params.extend([property_id, organization_id, approval_status])
        cursor.execute(
            f"""
            UPDATE properties
            SET {", ".join(assignments)}
            WHERE id = ?
                AND organization_id = ?
                AND status = ?
            """,
            params,
        )
        if cursor.rowcount == 0:
            raise ReservationError("reservation_err_property")
        created_at = to_utc_iso(now_utc())
        insert_property_commercial_event(
            cursor,
            {
                "organization_id": organization_id,
                "property_id": property_id,
                "actor_user_id": actor_user_id,
                "agent_id": agent["id"],
                "task_id": None,
                "previous_commercial_status": previous_status,
                "commercial_status": "reserved",
                "previous_price": previous_price,
                "previous_currency": previous_currency,
                "listing_price": new_price if update_price else None,
                "listing_currency": new_currency if update_price else None,
                "reservation_amount": outcome.get("reservation_amount"),
                "reservation_currency": outcome.get("reservation_currency"),
                "created_at": created_at,
            },
        )
        try:
            reservation_id = insert_reservation(
                cursor,
                {
                    "organization_id": organization_id,
                    "property_id": property_id,
                    "contact_id": contact["id"],
                    "agent_id": agent["id"],
                    "visit_id": None,
                    "operation_id": None,
                    "reservation_status": "reserved",
                    "original_property_price": previous_price,
                    "original_currency": previous_currency,
                    "agreed_property_price": agreed if agreed is not None else previous_price,
                    "agreed_currency": (
                        outcome.get("agreed_property_currency")
                        or previous_currency
                        or "USD"
                    ),
                    "reservation_amount": outcome.get("reservation_amount"),
                    "reservation_currency": outcome.get("reservation_currency"),
                    "payment_method": outcome.get("payment_method"),
                    "next_milestone": outcome.get("next_milestone"),
                    "estimated_closing_date": outcome.get("estimated_closing_date"),
                    "notes": outcome.get("reservation_notes") or None,
                    "is_shared_transaction": 1 if outcome.get("is_shared_transaction") else 0,
                    "shared_brokerage_name": outcome.get("shared_brokerage_name"),
                    "seller_commission_amount": outcome.get("seller_commission_amount"),
                    "seller_commission_currency": outcome.get("seller_commission_currency"),
                    "buyer_commission_amount": outcome.get("buyer_commission_amount"),
                    "buyer_commission_currency": outcome.get("buyer_commission_currency"),
                    "deposit_type": outcome.get("deposit_type") or "reservation",
                    "purchase_with_credit": credit,
                    "financing_bank": bank,
                    "reserved_at": created_at,
                    "created_by_user_id": actor_user_id,
                    "created_at": created_at,
                    "updated_at": created_at,
                },
            )
        except ReservationConflict as error:
            raise ReservationError("reservation_err_open") from error
        insert_reservation_event(
            cursor,
            {
                "organization_id": organization_id,
                "reservation_id": reservation_id,
                "event_type": "created",
                "actor_user_id": actor_user_id,
                "payload": {
                    "source": "manual",
                    "purchase_with_credit": credit,
                    "financing_bank": bank,
                },
                "created_at": created_at,
            },
        )
        connection.commit()
        return reservation_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
