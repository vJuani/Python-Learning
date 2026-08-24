from modules.cli_tenant import get_cli_organization_id

from modules.database import (
    get_agents,
    get_operations,
    get_properties
)


def load_agents(organization_id=None):
    if organization_id is None:
        organization_id = get_cli_organization_id()

    return get_agents(organization_id)


def load_properties(organization_id=None):
    if organization_id is None:
        organization_id = get_cli_organization_id()

    return get_properties(organization_id)


def load_history(organization_id=None):
    if organization_id is None:
        organization_id = get_cli_organization_id()

    return get_operations(organization_id)
