"""Office inventory stays separate from market comparables."""

from modules.database.property_sync_hub_repository import (
    STATUS_CONNECTED,
    get_property_integration,
)
from modules.database.tenant import require_organization_id
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX


def organization_uses_redremax(organization_id):
    """True only when this office actually enabled the RedREMAX connector."""
    organization_id = require_organization_id(organization_id)
    item = get_property_integration(organization_id, PROVIDER_REDREMAX)
    if not item:
        return False
    if item.get("sync_enabled"):
        return True
    if item.get("status") == STATUS_CONNECTED:
        return True
    office_id = str((item.get("config") or {}).get("external_office_id") or "").strip()
    return bool(office_id)
