from .connection import get_connection


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

        operations.agent_id,
        operations.property_id

    FROM operations

    JOIN agents
        ON operations.agent_id = agents.id

    JOIN properties
        ON operations.property_id = properties.id
"""


def build_operation_dict(rows):
    operations = []

    for row in rows:
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
            "agent_db_id": row[18],
            "property_db_id": row[19]
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
    office_total
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO operations (
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
            office_total
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
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
            office_total
        )
    )

    operation_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return operation_id


def get_operations():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        ORDER BY operations.id
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return build_operation_dict(
        rows
    )


def search_operations_by_agent(
    agent_name
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE
            LOWER(agents.name)
            LIKE LOWER(?)

        ORDER BY operations.id
        """,
        (
            f"%{agent_name}%",
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return build_operation_dict(
        rows
    )


def search_operations_by_id(
    operation_id
):
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
        """,
        (
            database_id,
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return build_operation_dict(
        rows
    )


def search_operations_by_property(
    property_search
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE
            LOWER(properties.address)
            LIKE LOWER(?)

        ORDER BY operations.id
        """,
        (
            f"%{property_search}%",
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return build_operation_dict(
        rows
    )


def search_operations_by_date(
    date_search
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE
            operations.operation_date = ?

        ORDER BY operations.id
        """,
        (
            date_search,
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return build_operation_dict(
        rows
    )


def get_operation_record(
    operation_id
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        OPERATIONS_BASE_QUERY
        + """
        WHERE operations.id = ?
        """,
        (
            operation_id,
        )
    )

    row = cursor.fetchone()

    connection.close()

    if row is None:
        return None

    return build_operation_dict(
        [row]
    )[0]


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
    office_total
):
    connection = get_connection()
    cursor = connection.cursor()

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
            office_total = ?
        WHERE id = ?
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
            operation_id
        )
    )

    connection.commit()
    connection.close()


def delete_operation(
    operation_id
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM operations
        WHERE id = ?
        """,
        (
            operation_id,
        )
    )

    connection.commit()
    connection.close()
