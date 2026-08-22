import sqlite3


DATABASE_NAME = "commission.db"


def get_connection():
    connection = sqlite3.connect(
        DATABASE_NAME
    )

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection
