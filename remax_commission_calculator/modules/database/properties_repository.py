from .connection import get_connection


def get_properties():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            address,
            jurisdiction
        FROM properties
        ORDER BY id
        """
    )

    rows = cursor.fetchall()

    connection.close()

    properties = []

    for row in rows:
        properties.append({
            "id": row[0],
            "address": row[1],
            "jurisdiction": row[2]
        })

    return properties


def add_property(
    address,
    jurisdiction
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO properties (
            address,
            jurisdiction
        )
        VALUES (?, ?)
        """,
        (
            address,
            jurisdiction
        )
    )

    connection.commit()
    connection.close()


def update_property(
    property_id,
    address,
    jurisdiction
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE properties
        SET
            address = ?,
            jurisdiction = ?
        WHERE id = ?
        """,
        (
            address,
            jurisdiction,
            property_id
        )
    )

    connection.commit()
    connection.close()


def delete_property(
    property_id
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM properties
        WHERE id = ?
        """,
        (
            property_id,
        )
    )

    connection.commit()
    connection.close()
