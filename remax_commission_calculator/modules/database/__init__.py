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

from .agent_account_repository import (
    count_movements_in_month,
    create_agent_account_movement_atomic,
    get_agent_account_metadata,
    get_agent_account_movement,
    get_agent_balance,
    get_agent_balances,
    get_movement_by_idempotency_key,
    list_agent_account_movements,
    list_agents_account_summary,
    reverse_agent_account_movement_atomic,
    sum_organization_balances,
    sum_payments_collected_month,
    sum_receivable_balances,
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

from .agent_payment_ai_drafts_repository import (
    create_agent_payment_ai_draft,
    get_agent_payment_ai_draft,
    list_agent_payment_ai_drafts,
    update_agent_payment_ai_draft,
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
    get_property_ids_used_in_operations,
    get_property_record,
    is_property_available_for_operation,
    list_available_properties_for_operation,
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
    update_arca_config as update_agent_arca_config,
    upsert_profile as upsert_agent_billing_profile,
)

from .billing_issuer_profiles_repository import (
    deactivate as deactivate_billing_issuer_profile,
    get_profile as get_billing_issuer_profile,
    list_profiles as list_billing_issuer_profiles,
    set_default as set_default_billing_issuer_profile,
    update_arca_config as update_issuer_arca_config,
    upsert_profile as upsert_billing_issuer_profile,
)

from .operation_parties_repository import (
    create_parties_for_new_operation,
    ensure_parties_for_operation,
    get_parties_for_operation,
    get_party as get_operation_party,
    set_billing_enabled as set_operation_party_billing_enabled,
    set_client_fields as set_operation_party_client_fields,
    set_invoice_amount as set_operation_party_invoice_amount,
    upsert_party as upsert_operation_party,
)

from .invoices_repository import (
    ACTIVE_STATUSES as INVOICE_ACTIVE_STATUSES,
    count_invoices_by_status,
    count_pending_operations_to_invoice,
    count_pending_parties_to_invoice,
    create_invoice_atomic,
    get_active_invoice_for_operation,
    get_active_invoice_for_side_issuer,
    get_invoice,
    list_invoices,
    list_invoices_for_operation,
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

from .external_listings_repository import (
    UPSERT_CREATED,
    UPSERT_UNCHANGED,
    UPSERT_UPDATED,
    get_external_listing,
    get_external_listing_by_source_id,
    list_active_external_listings,
    list_external_listings,
    mark_external_listing_inactive,
    mark_external_listing_seen,
    upsert_external_listing,
)