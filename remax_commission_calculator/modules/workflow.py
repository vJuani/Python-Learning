STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

OPERATION_STATUSES = (
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED
)

PROPERTY_STATUSES = (
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED
)

AGENT_EDITABLE_STATUSES = (
    STATUS_DRAFT,
    STATUS_REJECTED
)

AGENT_PROPERTY_EDITABLE_STATUSES = (
    STATUS_PENDING,
    STATUS_REJECTED
)


def is_valid_status(status):
    return status in OPERATION_STATUSES


def is_valid_property_status(status):
    return status in PROPERTY_STATUSES


def agent_can_edit_status(status):
    return status in AGENT_EDITABLE_STATUSES


def agent_can_submit_status(status):
    return status in AGENT_EDITABLE_STATUSES


def agent_can_edit_property_directly(status):
    return status in AGENT_PROPERTY_EDITABLE_STATUSES


def property_is_official(status):
    return status == STATUS_APPROVED
