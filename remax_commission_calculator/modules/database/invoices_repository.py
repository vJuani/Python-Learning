"""
Repository for internal / fiscal invoices.
"""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


ACTIVE_STATUSES = (
    "draft",
    "ready_to_issue",
    "issued",
    "error",
)

# v1 KPI "Total facturado del mes": sum of draft + ready
# created this month. Never treat as fiscal issued totals.
KPI_FACTURADO_STATUSES = ("draft", "ready_to_issue")


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def format_invoice_number_internal(invoice_seq):
    return f"FAC-{int(invoice_seq):06d}"


def build_invoice_dict(row):
    if row is None:
        return None

    invoice = {
        "id": row[0],
        "organization_id": row[1],
        "invoice_seq": row[2],
        "invoice_number_internal": row[3],
        "operation_id": row[4],
        "agent_id": row[5],
        "issuer_user_id": row[6],
        "issuer_type": row[7],
        "issuer_name": row[8],
        "issuer_tax_id": row[9],
        "issuer_tax_condition": row[10],
        "issuer_address": row[11],
        "recipient_name": row[12],
        "recipient_tax_id": row[13],
        "recipient_tax_condition": row[14],
        "recipient_address": row[15],
        "invoice_type": row[16],
        "service_type": row[17],
        "description": row[18],
        "quantity": float(row[19] or 0),
        "unit_price": float(row[20] or 0),
        "subtotal": float(row[21] or 0),
        "vat_amount": float(row[22] or 0),
        "total_amount": float(row[23] or 0),
        "currency": row[24] or "ARS",
        "exchange_rate": (
            float(row[25]) if row[25] is not None else 1.0
        ),
        "payment_condition": row[26],
        "issue_date": row[27],
        "status": row[28],
        "source": row[29],
        "external_invoice_number": row[30],
        "point_of_sale": row[31],
        "cae": row[32],
        "cae_expiration": row[33],
        "provider": row[34] or "internal",
        "provider_reference": row[35],
        "pdf_path": row[36],
        "created_at": row[37],
        "created_by_user_id": row[38],
        "confirmed_at": row[39],
        "confirmed_by_user_id": row[40],
        "updated_at": row[41],
        "cancelled_at": row[42],
        "cancelled_by_user_id": row[43],
        "cancellation_reason": row[44],
        "cash_movement_id": row[45] if len(row) > 45 else None,
        "side": row[46] if len(row) > 46 else None,
        "issuer_profile_id": (
            row[47] if len(row) > 47 else None
        ),
        "issuer_key": row[48] if len(row) > 48 else None,
        "recipient_party_id": (
            row[49] if len(row) > 49 else None
        ),
    }

    # Optional joined display fields (list_invoices).
    if len(row) > 50:
        invoice["agent_name"] = row[50]
    if len(row) > 51:
        invoice["property_address"] = row[51]
    if len(row) > 52:
        invoice["operation_display_id"] = (
            f"COM-{int(row[52]):06d}"
            if row[52] is not None
            else None
        )
    if len(row) > 53:
        invoice["operation_date"] = row[53]

    return invoice


INVOICE_COLUMNS = """
        invoices.id,
        invoices.organization_id,
        invoices.invoice_seq,
        invoices.invoice_number_internal,
        invoices.operation_id,
        invoices.agent_id,
        invoices.issuer_user_id,
        invoices.issuer_type,
        invoices.issuer_name,
        invoices.issuer_tax_id,
        invoices.issuer_tax_condition,
        invoices.issuer_address,
        invoices.recipient_name,
        invoices.recipient_tax_id,
        invoices.recipient_tax_condition,
        invoices.recipient_address,
        invoices.invoice_type,
        invoices.service_type,
        invoices.description,
        invoices.quantity,
        invoices.unit_price,
        invoices.subtotal,
        invoices.vat_amount,
        invoices.total_amount,
        invoices.currency,
        invoices.exchange_rate,
        invoices.payment_condition,
        invoices.issue_date,
        invoices.status,
        invoices.source,
        invoices.external_invoice_number,
        invoices.point_of_sale,
        invoices.cae,
        invoices.cae_expiration,
        invoices.provider,
        invoices.provider_reference,
        invoices.pdf_path,
        invoices.created_at,
        invoices.created_by_user_id,
        invoices.confirmed_at,
        invoices.confirmed_by_user_id,
        invoices.updated_at,
        invoices.cancelled_at,
        invoices.cancelled_by_user_id,
        invoices.cancellation_reason,
        invoices.cash_movement_id,
        invoices.side,
        invoices.issuer_profile_id,
        invoices.issuer_key,
        invoices.recipient_party_id
"""

INVOICES_BASE_QUERY = f"""
    SELECT
{INVOICE_COLUMNS}
    FROM invoices
"""

INVOICES_LIST_QUERY = f"""
    SELECT
{INVOICE_COLUMNS},
        agents.name,
        properties.address,
        operations.id,
        operations.operation_date
    FROM invoices
    LEFT JOIN agents
        ON invoices.agent_id = agents.id
        AND agents.organization_id
            = invoices.organization_id
    LEFT JOIN operations
        ON invoices.operation_id = operations.id
        AND operations.organization_id
            = invoices.organization_id
    LEFT JOIN properties
        ON operations.property_id = properties.id
        AND properties.organization_id
            = invoices.organization_id
"""


def get_invoice(organization_id, invoice_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            INVOICES_BASE_QUERY
            + """
            WHERE invoices.id = ?
                AND invoices.organization_id = ?
            """,
            (invoice_id, organization_id),
        )
        return build_invoice_dict(cursor.fetchone())
    finally:
        connection.close()


def get_active_invoice_for_operation(
    organization_id,
    operation_id,
):
    """Legacy: any active invoice for the operation."""
    organization_id = require_organization_id(
        organization_id
    )
    placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            INVOICES_BASE_QUERY
            + f"""
            WHERE invoices.organization_id = ?
                AND invoices.operation_id = ?
                AND invoices.status IN ({placeholders})
            ORDER BY invoices.id DESC
            LIMIT 1
            """,
            (organization_id, operation_id, *ACTIVE_STATUSES),
        )
        return build_invoice_dict(cursor.fetchone())
    finally:
        connection.close()


def get_active_invoice_for_side_issuer(
    organization_id,
    operation_id,
    side,
    issuer_key,
):
    organization_id = require_organization_id(
        organization_id
    )
    placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            INVOICES_BASE_QUERY
            + f"""
            WHERE invoices.organization_id = ?
                AND invoices.operation_id = ?
                AND invoices.side = ?
                AND invoices.issuer_key = ?
                AND invoices.status IN ({placeholders})
            ORDER BY invoices.id DESC
            LIMIT 1
            """,
            (
                organization_id,
                operation_id,
                side,
                issuer_key,
                *ACTIVE_STATUSES,
            ),
        )
        return build_invoice_dict(cursor.fetchone())
    finally:
        connection.close()


def list_invoices_for_operation(
    organization_id,
    operation_id,
    side=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    clauses = [
        "invoices.organization_id = ?",
        "invoices.operation_id = ?",
    ]
    params = [organization_id, operation_id]

    if side is not None:
        clauses.append("invoices.side = ?")
        params.append(side)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            INVOICES_BASE_QUERY
            + f"""
            WHERE {" AND ".join(clauses)}
            ORDER BY invoices.id DESC
            """,
            params,
        )
        return [
            build_invoice_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def list_invoices(
    organization_id,
    *,
    agent_id=None,
    status=None,
    operation_id=None,
    date_from=None,
    date_to=None,
    invoice_type=None,
    payment_condition=None,
    side=None,
    issuer_key=None,
    q=None,
    limit=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    clauses = ["invoices.organization_id = ?"]
    params = [organization_id]

    if agent_id is not None:
        clauses.append("invoices.agent_id = ?")
        params.append(agent_id)

    if status is not None:
        if isinstance(status, (list, tuple)):
            placeholders = ", ".join("?" for _ in status)
            clauses.append(
                f"invoices.status IN ({placeholders})"
            )
            params.extend(status)
        else:
            clauses.append("invoices.status = ?")
            params.append(status)

    if operation_id is not None:
        clauses.append("invoices.operation_id = ?")
        params.append(operation_id)

    if date_from is not None:
        clauses.append("invoices.issue_date >= ?")
        params.append(date_from)

    if date_to is not None:
        clauses.append("invoices.issue_date <= ?")
        params.append(date_to)

    if invoice_type is not None:
        clauses.append("invoices.invoice_type = ?")
        params.append(invoice_type)

    if payment_condition is not None:
        clauses.append("invoices.payment_condition = ?")
        params.append(payment_condition)

    if side is not None:
        clauses.append("invoices.side = ?")
        params.append(side)

    if issuer_key is not None:
        clauses.append("invoices.issuer_key = ?")
        params.append(issuer_key)

    if q:
        like = f"%{str(q).strip()}%"
        clauses.append(
            """
            (
                invoices.invoice_number_internal LIKE ?
                OR invoices.issuer_name LIKE ?
                OR invoices.recipient_name LIKE ?
                OR invoices.description LIKE ?
                OR agents.name LIKE ?
                OR properties.address LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like, like])

    sql = (
        INVOICES_LIST_QUERY
        + " WHERE "
        + " AND ".join(clauses)
        + """
        ORDER BY invoices.id DESC
        """
    )

    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(sql, params)
        return [
            build_invoice_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def next_invoice_seq(cursor, organization_id):
    cursor.execute(
        """
        SELECT COALESCE(MAX(invoice_seq), 0)
        FROM invoices
        WHERE organization_id = ?
        """,
        (organization_id,),
    )
    row = cursor.fetchone()
    return int(row[0] or 0) + 1


def create_invoice_atomic(
    organization_id,
    *,
    fields,
    cursor=None,
    connection=None,
):
    """
    Insert an invoice row.

    If ``cursor`` (and optionally ``connection``) is
    provided, the caller owns the transaction.
    Otherwise a local connection is opened and committed.
    """
    organization_id = require_organization_id(
        organization_id
    )
    owns_connection = cursor is None
    now = _now_iso()

    if owns_connection:
        connection = get_connection()
        cursor = connection.cursor()

    try:
        invoice_seq = next_invoice_seq(
            cursor,
            organization_id,
        )
        invoice_number = format_invoice_number_internal(
            invoice_seq
        )

        invoice_id = execute_insert(
            cursor,
            """
            INSERT INTO invoices (
                organization_id,
                invoice_seq,
                invoice_number_internal,
                operation_id,
                agent_id,
                issuer_user_id,
                issuer_type,
                issuer_name,
                issuer_tax_id,
                issuer_tax_condition,
                issuer_address,
                recipient_name,
                recipient_tax_id,
                recipient_tax_condition,
                recipient_address,
                invoice_type,
                service_type,
                description,
                quantity,
                unit_price,
                subtotal,
                vat_amount,
                total_amount,
                currency,
                exchange_rate,
                payment_condition,
                issue_date,
                status,
                source,
                external_invoice_number,
                point_of_sale,
                cae,
                cae_expiration,
                provider,
                provider_reference,
                pdf_path,
                created_at,
                created_by_user_id,
                confirmed_at,
                confirmed_by_user_id,
                updated_at,
                cancelled_at,
                cancelled_by_user_id,
                cancellation_reason,
                cash_movement_id,
                side,
                issuer_profile_id,
                issuer_key,
                recipient_party_id
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                invoice_seq,
                invoice_number,
                fields["operation_id"],
                fields["agent_id"],
                fields.get("issuer_user_id"),
                fields.get("issuer_type"),
                fields.get("issuer_name"),
                fields.get("issuer_tax_id"),
                fields.get("issuer_tax_condition"),
                fields.get("issuer_address"),
                fields.get("recipient_name"),
                fields.get("recipient_tax_id"),
                fields.get("recipient_tax_condition"),
                fields.get("recipient_address"),
                fields.get("invoice_type", "internal"),
                fields.get("service_type", "services"),
                fields.get("description"),
                fields.get("quantity", 1),
                fields.get("unit_price", 0),
                fields.get("subtotal", 0),
                fields.get("vat_amount", 0),
                fields.get("total_amount", 0),
                fields.get("currency", "ARS"),
                fields.get("exchange_rate", 1),
                fields.get("payment_condition"),
                fields.get("issue_date"),
                fields.get("status", "draft"),
                fields.get("source"),
                fields.get("external_invoice_number"),
                fields.get("point_of_sale"),
                fields.get("cae"),
                fields.get("cae_expiration"),
                fields.get("provider", "internal"),
                fields.get("provider_reference"),
                fields.get("pdf_path"),
                now,
                fields.get("created_by_user_id"),
                fields.get("confirmed_at"),
                fields.get("confirmed_by_user_id"),
                now,
                fields.get("cancelled_at"),
                fields.get("cancelled_by_user_id"),
                fields.get("cancellation_reason"),
                fields.get("cash_movement_id"),
                fields.get("side"),
                fields.get("issuer_profile_id"),
                fields.get("issuer_key"),
                fields.get("recipient_party_id"),
            ),
        )

        if owns_connection:
            connection.commit()

        return {
            "id": invoice_id,
            "invoice_seq": invoice_seq,
            "invoice_number_internal": invoice_number,
        }
    except Exception:
        if owns_connection and connection is not None:
            connection.rollback()
        raise
    finally:
        if owns_connection and connection is not None:
            connection.close()


def update_invoice_status(
    organization_id,
    invoice_id,
    status,
    *,
    confirmed_at=None,
    confirmed_by_user_id=None,
    cancelled_at=None,
    cancelled_by_user_id=None,
    cancellation_reason=None,
    clear_cancellation=False,
    clear_confirmation=False,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    clauses = ["status = ?", "updated_at = ?"]
    params = [status, now]

    if confirmed_at is not None:
        clauses.append("confirmed_at = ?")
        params.append(confirmed_at)
    elif clear_confirmation:
        clauses.append("confirmed_at = NULL")

    if confirmed_by_user_id is not None:
        clauses.append("confirmed_by_user_id = ?")
        params.append(confirmed_by_user_id)
    elif clear_confirmation:
        clauses.append("confirmed_by_user_id = NULL")

    if cancelled_at is not None:
        clauses.append("cancelled_at = ?")
        params.append(cancelled_at)
    elif clear_cancellation:
        clauses.append("cancelled_at = NULL")

    if cancelled_by_user_id is not None:
        clauses.append("cancelled_by_user_id = ?")
        params.append(cancelled_by_user_id)
    elif clear_cancellation:
        clauses.append("cancelled_by_user_id = NULL")

    if cancellation_reason is not None:
        clauses.append("cancellation_reason = ?")
        params.append(cancellation_reason)
    elif clear_cancellation:
        clauses.append("cancellation_reason = NULL")

    params.extend([invoice_id, organization_id])
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            UPDATE invoices
            SET {", ".join(clauses)}
            WHERE id = ?
                AND organization_id = ?
            """,
            params,
        )
        updated = cursor.rowcount > 0
        connection.commit()
        return updated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_invoice_fields(
    organization_id,
    invoice_id,
    *,
    payment_condition=None,
    issue_date=None,
):
    """Draft edits: payment_condition and issue_date only."""
    organization_id = require_organization_id(
        organization_id
    )
    clauses = ["updated_at = ?"]
    params = [_now_iso()]

    if payment_condition is not None:
        clauses.append("payment_condition = ?")
        params.append(payment_condition)

    if issue_date is not None:
        clauses.append("issue_date = ?")
        params.append(issue_date)

    if len(clauses) == 1:
        return False

    params.extend([invoice_id, organization_id])
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            UPDATE invoices
            SET {", ".join(clauses)}
            WHERE id = ?
                AND organization_id = ?
                AND status = 'draft'
            """,
            params,
        )
        updated = cursor.rowcount > 0
        connection.commit()
        return updated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def count_invoices_by_status(
    organization_id,
    *,
    agent_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    clauses = ["organization_id = ?"]
    params = [organization_id]

    if agent_id is not None:
        clauses.append("agent_id = ?")
        params.append(agent_id)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            SELECT status, COUNT(*)
            FROM invoices
            WHERE {" AND ".join(clauses)}
            GROUP BY status
            """,
            params,
        )
        counts = {
            "draft": 0,
            "ready_to_issue": 0,
            "issued": 0,
            "error": 0,
            "cancelled": 0,
        }
        for status, count in cursor.fetchall():
            if status in counts:
                counts[status] = int(count)
        return counts
    finally:
        connection.close()


def sum_invoiced_amount(
    organization_id,
    *,
    month_start,
    month_end=None,
    agent_id=None,
    currency=None,
):
    """
    v1 KPI "Total facturado del mes".

    Sums total_amount for invoices in draft/ready_to_issue
    created in the month window. Does not pretend fiscal
    issued totals (provider internal never issues).
    """
    organization_id = require_organization_id(
        organization_id
    )
    placeholders = ", ".join(
        "?" for _ in KPI_FACTURADO_STATUSES
    )
    clauses = [
        "organization_id = ?",
        f"status IN ({placeholders})",
        "created_at >= ?",
    ]
    params = [
        organization_id,
        *KPI_FACTURADO_STATUSES,
        month_start,
    ]

    if month_end is not None:
        clauses.append("created_at <= ?")
        params.append(month_end)

    if agent_id is not None:
        clauses.append("agent_id = ?")
        params.append(agent_id)

    if currency is not None:
        clauses.append("currency = ?")
        params.append(currency)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            SELECT COALESCE(SUM(total_amount), 0)
            FROM invoices
            WHERE {" AND ".join(clauses)}
            """,
            params,
        )
        row = cursor.fetchone()
        return float(row[0] or 0)
    finally:
        connection.close()


def count_pending_operations_to_invoice(
    organization_id,
    agent_id=None,
):
    """
    Legacy: operations with invoice_amount > 0 and no
    active invoice. Prefer count_pending_parties_to_invoice.
    """
    organization_id = require_organization_id(
        organization_id
    )
    placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
    clauses = [
        "operations.organization_id = ?",
        "operations.invoice_amount IS NOT NULL",
        "operations.invoice_amount > 0",
        f"""
        NOT EXISTS (
            SELECT 1
            FROM invoices
            WHERE invoices.organization_id
                = operations.organization_id
                AND invoices.operation_id = operations.id
                AND invoices.status IN ({placeholders})
        )
        """,
    ]
    params = [organization_id, *ACTIVE_STATUSES]

    if agent_id is not None:
        clauses.append("operations.agent_id = ?")
        params.append(agent_id)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM operations
            WHERE {" AND ".join(clauses)}
            """,
            params,
        )
        row = cursor.fetchone()
        return int(row[0] or 0)
    finally:
        connection.close()


def count_pending_parties_to_invoice(
    organization_id,
    agent_id=None,
):
    """
    Count operation parties ready to invoice.

    Staff (no agent_id): participating + billing_enabled +
    invoice_amount > 0.

    Agent: same, limited to the agent's operations, and
    without an active invoice for issuer_key=agent:{id}
    on that operation+side.
    """
    organization_id = require_organization_id(
        organization_id
    )
    clauses = [
        "operation_parties.organization_id = ?",
        "operation_parties.is_participating = 1",
        "operation_parties.billing_enabled = 1",
        "operation_parties.invoice_amount IS NOT NULL",
        "operation_parties.invoice_amount > 0",
    ]
    params = [organization_id]

    join_sql = ""
    if agent_id is not None:
        placeholders = ", ".join(
            "?" for _ in ACTIVE_STATUSES
        )
        issuer_key = f"agent:{agent_id}"
        join_sql = """
            INNER JOIN operations
                ON operations.id
                    = operation_parties.operation_id
                AND operations.organization_id
                    = operation_parties.organization_id
        """
        clauses.append("operations.agent_id = ?")
        params.append(agent_id)
        clauses.append(
            f"""
            NOT EXISTS (
                SELECT 1
                FROM invoices
                WHERE invoices.organization_id
                    = operation_parties.organization_id
                    AND invoices.operation_id
                        = operation_parties.operation_id
                    AND invoices.side
                        = operation_parties.party_role
                    AND invoices.issuer_key = ?
                    AND invoices.status IN ({placeholders})
            )
            """
        )
        params.append(issuer_key)
        params.extend(ACTIVE_STATUSES)

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM operation_parties
            {join_sql}
            WHERE {" AND ".join(clauses)}
            """,
            params,
        )
        row = cursor.fetchone()
        return int(row[0] or 0)
    finally:
        connection.close()
