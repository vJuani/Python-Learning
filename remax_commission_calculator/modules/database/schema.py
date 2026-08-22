from .connection import get_connection


def create_tables():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            jurisdiction TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            operation_date TEXT NOT NULL,

            agent_id INTEGER NOT NULL,
            property_id INTEGER NOT NULL,

            was_invoiced TEXT NOT NULL,

            vat_amount REAL NOT NULL,

            sale_price REAL NOT NULL,
            commission_rate REAL NOT NULL,

            total_commission REAL NOT NULL,
            commission_after_abao REAL NOT NULL,

            abao REAL NOT NULL,
            martillero REAL NOT NULL,

            agent_payment REAL NOT NULL,
            office_payment REAL NOT NULL,
            office_total REAL NOT NULL,

            FOREIGN KEY (agent_id)
                REFERENCES agents(id)
                ON DELETE RESTRICT,

            FOREIGN KEY (property_id)
                REFERENCES properties(id)
                ON DELETE RESTRICT
        )
    """)

    connection.commit()
    connection.close()
