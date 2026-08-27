from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import (
    TenantError,
    assert_operation_pair_in_organization,
    require_organization_id
)


OPERATIONS_BASE_QUERY = """
    SELECT
        operations.id,
        operations.operation_date,

        agents.name,
        agents.type,

        properties.id,
        properties.address,
        properties.jurisdiction,

        operations.was_invoiced,
        operations.vat_amount,
        operations.sale_price,
        operations.commission_rate,
        operations.total_commission,
        operations.commission_after_abao,
        operations.abao,
        operations.martillero,
        operations.agent_payment,
        operations.office_payment,
        operations.office_total,

        operations.currency,
        operations.original_amount,
        operations.exchange_rate,

        operations.agent_id,
        operations.property_id,
        operations.organization_id,

        operations.status,
        operations.rejection_reason,
        operations.created_by_user_id,
        operations.reviewed_by_user_id,
        operations.reviewed_at,
        operations.invoice_full_commission,
        properties.external_id,
        operations.invoice_amount,
        operations.invoice_currency,
        operations.invoice_exchange_rate,
        operations.invoice_amount_set_at,
        operations.invoice_amount_set_by_user_id

    FROM operations

    JOIN agents
        ON operations.agent_id = agents.id
        AND agents.organization_id
            = operations.organization_id

    JOIN properties
        ON operations.property_id = properties.id
        AND properties.organization_id
            = operations.organization_id
"""


def build_operation_dict(rows):
    operations = []

    for row in rows:
        original_amount = row[19]

        if original_amount is None:
            original_amount = row[9]

        external_id = None
        if len(row) > 30 and row[30]:
            external_id = str(row[30]).strip() or None

        operations.append({
            "db_id": row[0],
            "id": f"COM-{row[0]:06d}",
            "date": row[1],
            "agent": row[2],
            "agent_type": row[3],
            "property_id": f"PROP-{row[4]:06d}",
            "property": row[5],
            "jurisdiction": row[6],
            "was_invoiced": row[7],
            "vat_amount": row[8],
            "sale_price": row[9],
            "commission_rate": row[10],
            "total_commission": row[11],
            "commission_after_abao": row[12],
            "abao": row[13],
            "martillero": row[14],
            "agent_payment": row[15],
            "office_payment": row[16],
            "office_total": row[17],
            "currency": row[18] or "USD",
            "original_amount": original_amount,
            "exchange_rate": row[20] if row[20] is not None else 1,
            "agent_db_id": row[21],
            "property_db_id": row[22],
            "organization_id": row[23],
            "status": row[24] or "approved",
            "rejection_reason": row[25],
            "created_by_user_id": row[26],
            "reviewed_by_user_id": row[27],
            "reviewed_at": row[28],
            "invoice_full_commission": (
                row[29] if len(row) > 29 and row[29]
                else "no"
            ),
            "property_external_id": external_id,
            "invoice_amount": (
                float(row[31])
                if len(row) > 31 and row[31] is not None
                else None
            ),
            "invoice_currency": (
                row[32] if len(row) > 32 and row[32]
                else None
            ),
            "invoice_exchange_rate": (
                float(row[33])
                if len(row) > 33 and row[33] is not None
                else None
            ),
            "invoice_amount_set_at": (
                row[34] if len(row) > 34 else None
            ),
            "invoice_amount_set_by_user_id": (
                row[35] if len(row) > 35 else None
            ),
        })

    return operations


def add_operation(
    operation_date,
    agent_id,
    property_id,
    was_invoiced,
    vat_amount,
    sale_price,
    commission_rate,
    total_commission,
    commission_after_abao,
    abao,
    martillero,
    agent_payment,
    office_payment,
    office_total,
    organization_id,
    currency="USD",
    original_amount=None,
    exchange_rate=1,
    status="approved",
    created_by_user_id=None,
    rejection_reason=None,
    require_property_owner=False,
    invoice_full_commission="no"
):
    organization_id = require_organization_id(
        organization_id
    )

    if original_amount is None:
        original_amount = sale_price

    connection = get_connection()
    cursor = connection.cursor()

    assert_operation_pair_in_organization(
        cursor,
        agent_id,
        property_id,
        organization_id,
        require_property_owner=require_property_owner
    )

    operation_id = execute_insert(
        cursor,
        """
        INSERT INTO operations (
            operation_date,
            agent_id,
            property_id,
            organization_id,
            was_invoiced,
            vat_amount,
            sale_price,
            commission_rate,
            total_commission,
            commission_after_abao,
            abao,
            martillero,
            agent_payment,
            office_payment,
            office_total,
            currency,
            original_amount,
            exchange_rate,
            status,
            rejection_reason,
            created_by_user_id,
            invoice_full_commission
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        """,
        (
            operation_date,
            agent_id,
            property_id,
            organization_id,
            was_invoiced,
            vat_amount,
            sale_price,
            commission_rate,
            total_commission,
            commission_after_abao,
            abao,
            martillero,
            agent_payment,
            office_payment,
            office_total,
            currency,
            original_amount,
            exchange_rate,
            status,
            rejection_reason,
            created_by_user_id,
            invoice_full_commission
        )
    )

    connection.commit()
    connection.close()

    return operation_id


def get_operations(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE operations.organization_id = ?
        ORDER BY operations.id
        """,
        (
            organization_id,
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return build_operation_dict(rows)


def search_operations_by_agent(
    agent_name,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE operations.organization_id = ?
            AND LOWER(agents.name) LIKE LOWER(?)
        ORDER BY operations.id
        """,
        (
            organization_id,
            f"%{agent_name}%",
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return build_operation_dict(rows)


def search_operations_by_id(
    operation_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        database_id = int(
            operation_id
            .upper()
            .replace("COM-", "")
        )

    except ValueError:
        connection.close()
        return []

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE operations.id = ?
            AND operations.organization_id = ?
        """,
        (
            database_id,
            organization_id
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return build_operation_dict(rows)


def search_operations_by_property(
    property_search,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE operations.organization_id = ?
            AND LOWER(properties.address)
                LIKE LOWER(?)
        ORDER BY operations.id
        """,
        (
            organization_id,
            f"%{property_search}%",
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return build_operation_dict(rows)


def search_operations_by_date(
    date_search,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE operations.organization_id = ?
            AND operations.operation_date = ?
        ORDER BY operations.id
        """,
        (
            organization_id,
            date_search,
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return build_operation_dict(rows)


DATE_SORTABLE_SQL = (
    "substr(operations.operation_date, 7, 4) || "
    "substr(operations.operation_date, 4, 2) || "
    "substr(operations.operation_date, 1, 2)"
)

MONTH_KEY_SQL = (
    "substr(operations.operation_date, 7, 4) || '-' || "
    "substr(operations.operation_date, 4, 2)"
)


def build_operation_filter_conditions(
    organization_id,
    operation_id=None,
    agent_name=None,
    property_address=None,
    min_amount=None,
    max_amount=None,
    date_from=None,
    date_to=None,
    was_invoiced=None,
    jurisdiction=None,
    agent_id=None,
    status=None,
    currency=None,
    agent_type=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    conditions = [
        "operations.organization_id = ?"
    ]
    params = [organization_id]

    if operation_id is not None:
        conditions.append("operations.id = ?")
        params.append(operation_id)

    if agent_id is not None:
        conditions.append("operations.agent_id = ?")
        params.append(agent_id)

    if agent_name is not None:
        conditions.append(
            "LOWER(agents.name) LIKE LOWER(?)"
        )
        params.append(f"%{agent_name}%")

    if property_address is not None:
        conditions.append(
            "LOWER(properties.address) LIKE LOWER(?)"
        )
        params.append(f"%{property_address}%")

    if min_amount is not None:
        conditions.append("operations.sale_price >= ?")
        params.append(min_amount)

    if max_amount is not None:
        conditions.append("operations.sale_price <= ?")
        params.append(max_amount)

    if date_from is not None:
        conditions.append(f"{DATE_SORTABLE_SQL} >= ?")
        params.append(date_from)

    if date_to is not None:
        conditions.append(f"{DATE_SORTABLE_SQL} <= ?")
        params.append(date_to)

    if was_invoiced is not None:
        conditions.append("operations.was_invoiced = ?")
        params.append(was_invoiced)

    if jurisdiction is not None:
        conditions.append("properties.jurisdiction = ?")
        params.append(jurisdiction)

    if currency is not None:
        conditions.append("operations.currency = ?")
        params.append(currency)

    if agent_type is not None:
        conditions.append("agents.type = ?")
        params.append(agent_type)

    if status is not None:
        if isinstance(status, (list, tuple)):
            placeholders = ", ".join("?" for _ in status)
            conditions.append(
                f"operations.status IN ({placeholders})"
            )
            params.extend(status)
        else:
            conditions.append("operations.status = ?")
            params.append(status)

    return conditions, params


def filter_operations(
    organization_id,
    operation_id=None,
    agent_name=None,
    property_address=None,
    min_amount=None,
    max_amount=None,
    date_from=None,
    date_to=None,
    was_invoiced=None,
    jurisdiction=None,
    agent_id=None,
    status=None,
    currency=None,
    agent_type=None,
):
    conditions, params = build_operation_filter_conditions(
        organization_id,
        operation_id=operation_id,
        agent_name=agent_name,
        property_address=property_address,
        min_amount=min_amount,
        max_amount=max_amount,
        date_from=date_from,
        date_to=date_to,
        was_invoiced=was_invoiced,
        jurisdiction=jurisdiction,
        agent_id=agent_id,
        status=status,
        currency=currency,
        agent_type=agent_type,
    )

    connection = get_connection()
    cursor = connection.cursor()
    query = (
        OPERATIONS_BASE_QUERY
        + " WHERE "
        + " AND ".join(conditions)
        + " ORDER BY operations.id"
    )
    cursor.execute(query, params)
    rows = cursor.fetchall()
    connection.close()

    return build_operation_dict(rows)


def list_operations_for_property(
    property_id,
    organization_id,
    agent_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    query = (
        OPERATIONS_BASE_QUERY
        + """
        WHERE operations.organization_id = ?
            AND operations.property_id = ?
        """
    )
    params = [
        organization_id,
        property_id,
    ]

    if agent_id is not None:
        query += " AND operations.agent_id = ?"
        params.append(agent_id)

    query += " ORDER BY operations.id DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    connection.close()

    return build_operation_dict(rows)


def get_operation_record(
    operation_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE operations.id = ?
            AND operations.organization_id = ?
        """,
        (
            operation_id,
            organization_id
        )
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return build_operation_dict([row])[0]


def update_operation(
    operation_id,
    operation_date,
    agent_id,
    property_id,
    was_invoiced,
    vat_amount,
    sale_price,
    commission_rate,
    total_commission,
    commission_after_abao,
    abao,
    martillero,
    agent_payment,
    office_payment,
    office_total,
    organization_id,
    currency="USD",
    original_amount=None,
    exchange_rate=1,
    status=None,
    rejection_reason=None,
    require_property_owner=False,
    invoice_full_commission="no"
):
    organization_id = require_organization_id(
        organization_id
    )

    if original_amount is None:
        original_amount = sale_price

    connection = get_connection()
    cursor = connection.cursor()

    assert_operation_pair_in_organization(
        cursor,
        agent_id,
        property_id,
        organization_id,
        require_property_owner=require_property_owner
    )

    if status is None:
        cursor.execute(
            """
            UPDATE operations
            SET
                operation_date = ?,
                agent_id = ?,
                property_id = ?,
                was_invoiced = ?,
                vat_amount = ?,
                sale_price = ?,
                commission_rate = ?,
                total_commission = ?,
                commission_after_abao = ?,
                abao = ?,
                martillero = ?,
                agent_payment = ?,
                office_payment = ?,
                office_total = ?,
                currency = ?,
                original_amount = ?,
                exchange_rate = ?,
                invoice_full_commission = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                operation_date,
                agent_id,
                property_id,
                was_invoiced,
                vat_amount,
                sale_price,
                commission_rate,
                total_commission,
                commission_after_abao,
                abao,
                martillero,
                agent_payment,
                office_payment,
                office_total,
                currency,
                original_amount,
                exchange_rate,
                invoice_full_commission,
                operation_id,
                organization_id
            )
        )
    else:
        cursor.execute(
            """
            UPDATE operations
            SET
                operation_date = ?,
                agent_id = ?,
                property_id = ?,
                was_invoiced = ?,
                vat_amount = ?,
                sale_price = ?,
                commission_rate = ?,
                total_commission = ?,
                commission_after_abao = ?,
                abao = ?,
                martillero = ?,
                agent_payment = ?,
                office_payment = ?,
                office_total = ?,
                currency = ?,
                original_amount = ?,
                exchange_rate = ?,
                status = ?,
                rejection_reason = ?,
                invoice_full_commission = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                operation_date,
                agent_id,
                property_id,
                was_invoiced,
                vat_amount,
                sale_price,
                commission_rate,
                total_commission,
                commission_after_abao,
                abao,
                martillero,
                agent_payment,
                office_payment,
                office_total,
                currency,
                original_amount,
                exchange_rate,
                status,
                rejection_reason,
                invoice_full_commission,
                operation_id,
                organization_id
            )
        )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Operation was not found in this organization."
        )

    connection.commit()
    connection.close()


def update_operation_status(
    operation_id,
    organization_id,
    status,
    reviewed_by_user_id=None,
    reviewed_at=None,
    rejection_reason=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE operations
        SET
            status = ?,
            rejection_reason = ?,
            reviewed_by_user_id = ?,
            reviewed_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            status,
            rejection_reason,
            reviewed_by_user_id,
            reviewed_at,
            operation_id,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Operation was not found in this organization."
        )

    connection.commit()
    connection.close()


def count_operations_by_status(
    organization_id,
    agent_id=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    if agent_id is None:
        cursor.execute(
            """
            SELECT
                status,
                COUNT(*)
            FROM operations
            WHERE organization_id = ?
            GROUP BY status
            """,
            (
                organization_id,
            )
        )
    else:
        cursor.execute(
            """
            SELECT
                status,
                COUNT(*)
            FROM operations
            WHERE organization_id = ?
                AND agent_id = ?
            GROUP BY status
            """,
            (
                organization_id,
                agent_id
            )
        )

    rows = cursor.fetchall()
    connection.close()

    counts = {
        "draft": 0,
        "pending": 0,
        "approved": 0,
        "rejected": 0
    }

    for status, count in rows:
        if status in counts:
            counts[status] = count

    return counts


def delete_operation(
    operation_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM operations
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            operation_id,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Operation was not found in this organization."
        )

    connection.commit()
    connection.close()


def update_operation_invoice_amount(
    operation_id,
    organization_id,
    amount,
    currency,
    exchange_rate,
    set_at,
    set_by_user_id,
):
    """
    Set billable invoice amount on an operation.
    Does not modify was_invoiced.
    """
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE operations
        SET
            invoice_amount = ?,
            invoice_currency = ?,
            invoice_exchange_rate = ?,
            invoice_amount_set_at = ?,
            invoice_amount_set_by_user_id = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            amount,
            currency,
            exchange_rate,
            set_at,
            set_by_user_id,
            operation_id,
            organization_id,
        ),
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Operation was not found in this organization."
        )

    connection.commit()
    connection.close()
