import sqlite3

from modules.config import get_database_path


def get_connection():
    connection = sqlite3.connect(
        get_database_path()
    )

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection
