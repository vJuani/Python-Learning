"""Explicit organization for administrative CLIs. Never assume id 1."""

import os


class CliOrganizationError(RuntimeError):
    """ORGANIZATION_ID was missing or not a positive integer."""


def get_cli_organization_id():
    raw_value = os.environ.get("ORGANIZATION_ID", "").strip()
    if not raw_value:
        raise CliOrganizationError(
            "ORGANIZATION_ID is required. "
            "Refusing to assume organization 1."
        )
    try:
        organization_id = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise CliOrganizationError(
            "ORGANIZATION_ID must be a positive integer."
        ) from exc
    if organization_id <= 0:
        raise CliOrganizationError(
            "ORGANIZATION_ID must be a positive integer."
        )
    return organization_id
