from .connection import (
    get_connection,
    get_database_path
)

from .schema import (
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_ORGANIZATION_NAME,
    create_tables
)

from .tenant import TenantError

from .agents_repository import (
    add_agent,
    delete_agent,
    get_agent_record,
    get_agents,
    update_agent
)

from .properties_repository import (
    STATUS_APPROVED as PROPERTY_STATUS_APPROVED,
    STATUS_PENDING as PROPERTY_STATUS_PENDING,
    STATUS_REJECTED as PROPERTY_STATUS_REJECTED,
    add_property,
    count_pending_properties,
    count_properties,
    delete_property,
    filter_properties,
    get_properties,
    get_property_record,
    update_property,
    update_property_status
)

from .property_change_requests_repository import (
    approve_property_change_request,
    count_pending_property_changes,
    create_property_change_request,
    get_pending_change_for_property,
    get_property_change_request,
    reject_property_change_request
)

from .notifications_repository import (
    count_unread_notifications,
    create_notification,
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read
)

from .pending_approvals_repository import (
    count_pending_approvals,
    list_pending_approval_items
)

from .operations_repository import (
    add_operation,
    build_operation_dict,
    count_operations_by_status,
    delete_operation,
    filter_operations,
    get_operation_record,
    get_operations,
    search_operations_by_agent,
    search_operations_by_date,
    search_operations_by_id,
    search_operations_by_property,
    update_operation,
    update_operation_status
)

from .dashboard_repository import (
    get_agent_ranking,
    get_dashboard_metrics
)

from .users_repository import (
    add_user,
    count_users,
    count_users_by_role,
    delete_user,
    get_user_by_agent_id,
    get_user_by_email,
    get_user_by_id,
    get_user_by_username,
    get_users,
    update_user
)

from .organizations_repository import (
    OrganizationProvisioningError,
    add_organization,
    get_organization_by_id,
    get_organizations,
    provision_organization
)

from .organization_settings_repository import (
    find_organization_by_registration_code_hash,
    get_organization_settings,
    set_registration_code,
    set_registration_enabled,
    update_organization_settings
)

from .registration_repository import (
    STATUS_APPROVED,
    STATUS_EMAIL_PENDING,
    STATUS_PENDING_APPROVAL,
    STATUS_REJECTED,
    count_pending_registration_requests,
    create_email_verification_token,
    create_registration_request,
    delete_pending_registration_request,
    get_active_verification_token,
    get_email_verification_token,
    get_registration_request,
    get_registration_request_by_email,
    increment_verification_attempt,
    list_registration_requests,
    mark_email_verified,
    mark_registration_approved,
    reject_registration_request
)

from .guest_access_repository import (
    create_guest_access,
    get_guest_access_by_token_hash,
    list_guest_accesses,
    revoke_guest_access,
    touch_guest_access
)

from .vat_documents_repository import (
    DOC_TYPE_AGENT_CLIENT,
    DOC_TYPE_MARTILLERO_CLIENT,
    VALID_DOC_TYPES,
    delete_vat_document,
    get_vat_document,
    get_vat_document_by_type,
    list_vat_documents_for_operation,
    upsert_vat_document
)