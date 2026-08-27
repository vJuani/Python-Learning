from .connection import (
    IntegrityError,
    adapt_sql,
    execute_insert,
    get_connection,
    get_database_backend,
    get_database_path,
    get_database_url,
)

from .schema import (
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_ORGANIZATION_NAME,
    create_tables,
    ensure_properties_external_id,
    migrate_schema,
)

from .schema_postgres import (
    POSTGRES_SCHEMA_VERSION,
    POSTGRES_TABLES,
    create_postgres_schema,
)

from .tenant import TenantError

from .agents_repository import (
    add_agent,
    delete_agent,
    find_agent_by_external_id,
    get_agent_record,
    get_agents,
    list_team_juniors,
    update_agent,
    update_agent_from_sync,
)

from .agent_wallet_repository import (
    MOVEMENT_OWN_COMMISSION,
    MOVEMENT_TEAM_LEADER_INCOME,
    get_wallet_movement_by_idempotency_key,
    insert_wallet_movement,
    list_wallet_movements_for_agent,
    list_wallet_movements_for_operation,
    sum_wallet_by_type,
)

from .cash_treasury_repository import (
    create_cash_movement_atomic,
    find_duplicate_cash_movements,
    get_cash_account,
    get_cash_movement,
    list_cash_accounts,
    list_cash_movements,
    reverse_cash_movement_atomic,
    sum_movements_by_type,
)

from .cash_ai_drafts_repository import (
    create_cash_ai_draft,
    get_cash_ai_draft,
    update_cash_ai_draft,
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
    update_property_from_sync,
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
    list_operations_for_property,
    update_operation,
    update_operation_invoice_amount,
    update_operation_status
)

from .agent_billing_profiles_repository import (
    get_by_agent as get_agent_billing_profile,
    upsert_profile as upsert_agent_billing_profile,
)

from .invoices_repository import (
    ACTIVE_STATUSES as INVOICE_ACTIVE_STATUSES,
    count_invoices_by_status,
    count_pending_operations_to_invoice,
    create_invoice_atomic,
    get_active_invoice_for_operation,
    get_invoice,
    list_invoices,
    next_invoice_seq,
    sum_invoiced_amount,
    update_invoice_fields,
    update_invoice_status,
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
    update_organization_billing_fields,
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

from .operation_documents_repository import (
    DOC_CATEGORIES,
    DOC_TYPE_AGENT_CLIENT,
    DOC_TYPE_DEED_CONTRACT,
    DOC_TYPE_LABEL_KEYS,
    DOC_TYPE_MARTILLERO_CLIENT,
    DOC_TYPE_OTHER,
    DOC_TYPE_RESERVATION,
    DOC_TYPE_TRANSFER,
    DOC_TYPE_UIF_ADDITIONAL,
    DOC_TYPE_UIF_FORM,
    STRUCTURED_DOC_TYPES,
    VALID_DOC_TYPES,
    allows_multiple_documents,
    delete_operation_document,
    delete_vat_document,
    get_operation_document,
    get_operation_document_by_type,
    get_vat_document,
    get_vat_document_by_type,
    list_operation_documents,
    list_vat_documents_for_operation,
    upsert_operation_document,
    upsert_vat_document,
)

from .property_external_listings_repository import (
    ListingPersistenceError,
    create_property_external_listing,
    delete_property_external_listing,
    find_listing_by_external_id,
    find_listing_by_provider_for_property,
    get_property_external_listing,
    list_property_external_listings,
    list_synced_listings_for_provider,
    mark_listing_inactive,
    update_property_external_listing,
)

from .organization_integrations_repository import (
    PROVIDER_CSV_UPLOAD,
    PROVIDER_STUB_FIXTURE,
    SCOPE_AGENT,
    SCOPE_ORGANIZATION,
    create_organization_integration,
    find_organization_integration_by_provider,
    get_integration_sync_run,
    get_organization_integration,
    list_organization_integrations,
)

from .csv_import_batches_repository import (
    create_csv_import_batch,
    delete_csv_import_batch,
    get_csv_import_batch,
)