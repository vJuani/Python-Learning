"""Local development server."""

from modules.config import (
    get_flask_debug,
    get_host,
    get_port
)
from modules.database import create_tables
from web_app import app


def main():
    create_tables()

    app.run(
        host=get_host(),
        port=get_port(),
        debug=get_flask_debug()
    )


if __name__ == "__main__":
    main()
