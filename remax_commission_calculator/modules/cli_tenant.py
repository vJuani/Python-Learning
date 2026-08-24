import os

from modules.database.schema import (
    DEFAULT_ORGANIZATION_ID
)


def get_cli_organization_id():
    raw_value = os.environ.get(
        "DEFAULT_ORGANIZATION_ID",
        str(DEFAULT_ORGANIZATION_ID)
    )

    try:
        organization_id = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_ORGANIZATION_ID

    if organization_id <= 0:
        return DEFAULT_ORGANIZATION_ID

    return organization_id
