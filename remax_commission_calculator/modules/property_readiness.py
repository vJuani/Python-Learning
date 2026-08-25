from modules.property_types import PROPERTY_TYPES
from modules.workflow import STATUS_APPROVED


def is_valid_property_type(value):
    return value in PROPERTY_TYPES


def validate_property_readiness(property_data):
    """
    Return (is_ready, missing_keys) for using a property in an operation.
    """
    missing = []

    if property_data is None:
        return False, ["property"]

    if (property_data.get("status") or "") != STATUS_APPROVED:
        missing.append("property_status")

    if not (property_data.get("address") or "").strip():
        missing.append("property_address")

    if not (property_data.get("jurisdiction") or "").strip():
        missing.append("property_jurisdiction")

    if property_data.get("agent_id") is None:
        missing.append("property_agent")

    property_type = property_data.get("property_type")
    if not property_type or not is_valid_property_type(property_type):
        missing.append("property_type")

    is_ready = len(missing) == 0
    return is_ready, missing


def property_readiness_requirement(property_data):
    is_ready, _missing = validate_property_readiness(property_data)
    return {
        "key": "property",
        "label_key": "readiness_property",
        "complete": is_ready,
        "required": True,
    }
