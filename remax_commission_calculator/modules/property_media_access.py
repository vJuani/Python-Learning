"""
Server-side access rules for property brochures and documents.
"""

from __future__ import annotations

from modules.auth import is_admin, is_agent


class PropertyMediaError(Exception):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


def can_access_property_media(user, property_data, *, is_guest=False):
    if is_guest or user is None or property_data is None:
        return False

    if user.get("organization_id") != property_data.get("organization_id"):
        return False

    if is_admin(user):
        return True

    if is_agent(user) and user.get("agent_id") == property_data.get("agent_id"):
        return True

    return False


def require_property_media_access(
    user,
    property_data,
    *,
    is_guest=False,
    write=False,
):
    if property_data is None:
        raise PropertyMediaError("property_brochure_not_found", 404)

    if not can_access_property_media(
        user,
        property_data,
        is_guest=is_guest,
    ):
        raise PropertyMediaError("access_denied", 403)

    if write and not (is_admin(user) or is_agent(user)):
        raise PropertyMediaError("access_denied", 403)

    return property_data
