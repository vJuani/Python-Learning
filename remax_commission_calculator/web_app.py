from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for
)

import os
import io

from datetime import date

from modules.auth import (
    ROLE_AGENT,
    USER_ROLES,
    admin_required,
    authenticate_user,
    can_approve,
    can_delete,
    can_manage_users,
    can_write,
    get_current_user,
    get_guest_access,
    hash_password,
    is_admin,
    is_agent,
    is_guest_session,
    load_logged_in_user,
    login_guest_access,
    login_required,
    login_user,
    logout_user,
    scoped_agent_id,
    scoped_organization_id,
    write_required
)

from modules.database import (
    IntegrityError,
    TenantError,
    add_agent,
    add_property,
    add_user,
    count_pending_approvals,
    count_pending_registration_requests,
    count_unread_notifications,
    create_property_change_request,
    approve_property_change_request,
    reject_property_change_request,
    get_pending_change_for_property,
    get_property_change_request,
    list_notifications,
    list_pending_approval_items,
    mark_all_notifications_read,
    mark_notification_read,
    update_property_status,
    PROPERTY_STATUS_PENDING,
    PROPERTY_STATUS_APPROVED,
    PROPERTY_STATUS_REJECTED,
    count_users_by_role,
    create_tables,
    delete_agent,
    delete_property,
    delete_user,
    get_agent_record,
    get_agents,
    get_cash_movement,
    get_operation_record,
    get_organization_settings,
    get_properties,
    get_property_record,
    get_registration_request,
    get_user_by_id,
    get_user_by_username,
    get_users,
    list_guest_accesses,
    list_operations_for_property,
    list_registration_requests,
    revoke_guest_access,
    set_registration_enabled,
    update_agent,
    update_organization_settings,
    update_organization_billing_fields,
    update_operation_status,
    update_property,
    update_user,
    get_agent_billing_profile,
    upsert_agent_billing_profile,
)

from modules.formatting import (
    format_money,
    format_number,
    format_short_date,
    convert_from_usd
)

from modules.vat_billing_calculator import (
    build_calculator_result,
    empty_form_values as empty_vat_form_values,
    form_values_from_operation as vat_form_values_from_operation,
    parse_calculator_inputs,
)

from modules.operation_documents import (
    absolute_document_path,
    get_operation_document,
    group_documents_for_ui,
    is_valid_doc_type,
    list_operation_documents,
    remove_operation_document,
    upload_or_replace_operation_document,
)

from modules.operation_summary import (
    build_billing_lines,
    build_commission_lines,
    load_operation_summary,
)
from modules.pdf_operation_summary import build_operation_summary_pdf
from modules.excel_operation_summary import build_operation_summary_xlsx
from modules.organization_reports import load_organization_report
from modules.pdf_organization_report import build_organization_report_pdf
from modules.excel_organization_report import build_organization_report_xlsx
from modules.organization_dashboard import (
    empty_organization_dashboard,
    load_organization_dashboard,
)


from modules.i18n import (
    DEFAULT_LANGUAGE,
    localize_messages,
    normalize_language,
    translate
)

from modules.properties import (
    get_filtered_properties,
    has_active_property_filters,
    validate_property_filters
)

from modules.pagination import (
    ALLOWED_PER_PAGE,
    DEFAULT_PER_PAGE,
    paginate_list,
    parse_per_page,
)

from modules.cash_treasury import (
    CASH_PER_PAGE_OPTIONS,
    CURRENCIES as CASH_CURRENCIES,
    DEFAULT_CASH_PER_PAGE,
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    PAYMENT_METHODS as CASH_PAYMENT_METHODS,
    TYPE_INCOME,
    CashTreasuryError,
    build_cash_kpis,
    categories_for_type,
    confirm_movement,
    filter_movements,
    get_balances,
    preview_movement,
    reverse_movement,
    set_opening_balances,
    validate_movement_payload,
)
from modules.cash_ai_service import (
    AI_PAYMENT_METHODS,
    CashAiError,
    PAYMENT_UNDETERMINED,
    build_review_context,
    confirm_ai_draft,
    log_cash_ai_runtime_config,
    retry_ai_analysis,
    start_ai_analysis,
    update_draft_from_form,
)
from modules.cash_receipts import absolute_receipt_path
from modules.database.cash_ai_drafts_repository import (
    get_cash_ai_draft,
)
from modules.invoicing import (
    InvoicingError,
    PAYMENT_CONDITIONS,
    STATUS_DRAFT,
    STATUS_READY,
    TAX_CONDITIONS,
    billing_kpis,
    cancel_invoice,
    confirm_draft,
    create_draft_for_side,
    generate_draft_pdf_bytes,
    get_invoice,
    get_operation_sides_state,
    list_invoices,
    list_pending_operations,
    set_party_invoice_amount,
    update_draft_options,
)

from modules.property_external_listings import (
    PROVIDER_OTHER,
    PROVIDER_REMAX_WEB,
    STATUS_ACTIVE,
    format_property_display_id,
    get_listing_record,
    load_property_listings,
    load_property_listings_for_property,
    provider_options,
    remove_listing,
    save_existing_listing,
    save_new_listing,
    status_options,
)

from modules.search import (
    global_search,
    search_agents,
    suggest_agents,
)

from modules.operation_prefill import (
    get_property_operation_prefill,
    suggest_available_properties,
)

from modules.operations import (
    change_operation_status,
    get_filtered_operations,
    get_new_operation_form_defaults,
    has_active_operation_filters,
    prepare_new_operation_from_form,
    prepare_operation_from_form,
    remove_operation,
    save_calculated_operation,
    update_calculated_operation,
    validate_operation_filters
)

from modules.validators import (
    AGENT_TYPES,
    JURISDICTIONS,
    parse_positive_float,
    validate_agent_form,
    validate_property_form
)

from modules.operation_readiness import (
    OperationNotReadyError,
    submit_operation_for_approval,
    validate_operation_readiness,
)
from modules.property_types import LISTING_PURPOSES, PROPERTY_TYPES

from modules.workflow import (
    OPERATION_STATUSES,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_REJECTED,
    agent_can_edit_property_directly,
    agent_can_edit_status,
    agent_can_submit_status,
    property_is_official
)

from modules.notifications_service import (
    notify_agent_for_operation,
    notify_agent_for_property,
    notify_agent_for_property_change
)

from modules.organization_settings import (
    COMMON_TIMEZONES,
    LOGO_EXTENSIONS,
    build_branding_css,
    validate_organization_settings_form
)

from modules.integrations import (
    cancel_csv_upload,
    cancel_remax_export,
    confirm_csv_upload,
    confirm_remax_export,
    preview_csv_upload,
    preview_remax_export,
    resolve_remax_export_preview,
)

from modules.config import (
    apply_config,
    get_host,
    get_port,
    get_flask_debug
)

from modules.passwords import validate_password_policy

from modules.registration import (
    approve_registration_request,
    create_organization_guest_link,
    mask_email,
    open_guest_access,
    reject_access_request,
    resend_verification_code,
    resume_email_verification,
    rotate_organization_registration_code,
    submit_agent_registration,
    suggested_agents_for_request,
    validate_registration_form,
    verify_registration_code
)

from modules.access_codes import hash_access_secret

from werkzeug.utils import secure_filename

from datetime import datetime
from pathlib import Path


app = Flask(__name__)
apply_config(app)

PUBLIC_ENDPOINTS = (
    "login",
    "register",
    "register_continue_verification",
    "verify_email",
    "verify_email_resend",
    "guest_access",
    "set_language",
    "static"
)


@app.before_request
def require_authenticated_user():
    load_logged_in_user()

    if request.endpoint is None:
        return None

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    if get_current_user() is not None:
        return None

    if get_guest_access() is not None:
        # Ephemeral helper: no DB writes, guests may recalculate.
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and request.endpoint != "vat_calculator"
        ):
            abort(403)

        return None

    flash_i18n("login_required", "error")

    return redirect(
        url_for(
            "login",
            next=request.path
        )
    )


def get_current_language():
    if "language" in session:
        return normalize_language(
            session["language"]
        )

    user = get_current_user()

    if user is not None:
        settings = get_organization_settings(
            user["organization_id"]
        )

        if settings is not None:
            return normalize_language(
                settings["default_language"]
            )

    return DEFAULT_LANGUAGE


def t(key, **kwargs):
    return translate(
        key,
        get_current_language(),
        **kwargs
    )


def flash_i18n(key, category="message"):
    flash(key, category)


def localize_form_errors(errors):
    return localize_messages(
        errors,
        get_current_language()
    )


@app.context_processor
def inject_i18n_helpers():
    language = get_current_language()

    return {
        "current_language": language,
        "t": lambda key, **kwargs: translate(
            key,
            language,
            **kwargs
        )
    }


@app.context_processor
def inject_billing_flash_cta():
    return {
        "billing_flash_cta": session.pop(
            "billing_flash_cta",
            None,
        ),
    }


@app.context_processor
def inject_auth_helpers():
    user = get_current_user()
    guest = get_guest_access()

    return {
        "current_user": user,
        "guest_access": guest,
        "is_guest_session": is_guest_session,
        "can_write": can_write,
        "can_manage_users": can_manage_users,
        "can_delete": can_delete,
        "can_approve": can_approve,
        "is_admin": is_admin,
        "is_agent": is_agent,
        "pending_access_requests": (
            count_pending_approvals(
                user["organization_id"]
            )
            if user is not None and is_admin(user)
            else 0
        ),
        "unread_notifications": (
            count_unread_notifications(
                user["id"],
                user["organization_id"]
            )
            if user is not None and is_agent(user)
            else 0
        )
    }


@app.context_processor
def inject_organization_branding():
    user = get_current_user()
    guest = get_guest_access()

    organization_id = None

    if user is not None:
        organization_id = user["organization_id"]
    elif guest is not None:
        organization_id = guest["organization_id"]

    if organization_id is None:
        return {
            "organization_display_name": None,
            "organization_logo_url": None,
            "organization_branding_css": None,
            "organization_default_currency": "USD"
        }

    settings = get_organization_settings(
        organization_id
    )

    display_name = None
    logo_url = None
    branding_css = None
    default_currency = "USD"

    if user is not None:
        display_name = user.get("organization_name")

    if settings is not None:
        display_name = settings["display_name"]
        default_currency = settings[
            "default_currency"
        ]

        if settings["logo_path"]:
            logo_url = url_for(
                "static",
                filename=settings["logo_path"]
            )

        if settings["accent_color"]:
            branding_css = build_branding_css(
                settings["accent_color"]
            )

    return {
        "organization_display_name": display_name,
        "organization_logo_url": logo_url,
        "organization_branding_css": branding_css,
        "organization_default_currency": default_currency
    }


@app.template_filter("money")
def money_filter(amount, currency="USD"):
    return format_money(
        amount,
        currency=currency,
        language=get_current_language()
    )


@app.template_filter("number")
def number_filter(amount, decimals=2):
    return format_number(
        amount,
        language=get_current_language(),
        decimals=decimals
    )


@app.template_filter("short_date")
def short_date_filter(value):
    return format_short_date(
        value,
        language=get_current_language()
    )


@app.route("/set-language/<language>")
def set_language(language):
    session["language"] = normalize_language(
        language
    )

    next_url = request.referrer

    if not next_url:
        next_url = url_for("dashboard")

    return redirect(next_url)


def get_safe_redirect_target(target):
    if not target:
        return None

    if not target.startswith("/"):
        return None

    if target.startswith("//"):
        return None

    return target


def get_user_organization_id():
    try:
        return scoped_organization_id()

    except TenantError:
        return None


def require_user_organization():
    organization_id = get_user_organization_id()

    if organization_id is None:
        flash_i18n("access_denied", "error")
        abort(403)

    return organization_id


def get_organization_default_currency(
    organization_id
):
    settings = get_organization_settings(
        organization_id
    )

    if settings is None:
        return "USD"

    return settings["default_currency"]


def settings_to_form_values(settings):
    return {
        "display_name": settings["display_name"],
        "default_language": settings[
            "default_language"
        ],
        "default_currency": settings[
            "default_currency"
        ],
        "timezone": settings["timezone"],
        "accent_color": (
            settings["accent_color"] or ""
        ),
        "legal_name": settings.get("legal_name") or "",
        "tax_id": settings.get("tax_id") or "",
        "tax_condition": settings.get("tax_condition") or "",
        "fiscal_address": settings.get("fiscal_address") or "",
        "trade_name": settings.get("trade_name") or "",
        "billing_email": settings.get("billing_email") or "",
        "default_payment_condition": (
            settings.get("default_payment_condition")
            or "cuenta_corriente"
        ),
        "default_invoice_description": (
            settings.get("default_invoice_description")
            or "Asesoramiento Integral de Gestión"
        ),
        "default_buyer_commission_percent": (
            settings.get("default_buyer_commission_percent")
            if settings.get(
                "default_buyer_commission_percent"
            ) is not None
            else 3
        ),
        "default_seller_commission_percent": (
            settings.get("default_seller_commission_percent")
            if settings.get(
                "default_seller_commission_percent"
            ) is not None
            else 3
        ),
    }


def save_organization_logo(
    organization_id,
    logo_file
):
    filename = secure_filename(
        logo_file.filename
    ).lower()

    extension = None

    for allowed in LOGO_EXTENSIONS:
        if filename.endswith(allowed):
            extension = allowed
            break

    if extension is None:
        return None

    upload_root = Path(
        app.config["UPLOAD_ROOT"]
    )
    upload_dir = (
        upload_root
        / "organizations"
        / str(organization_id)
    )

    os.makedirs(
        upload_dir,
        exist_ok=True
    )

    for existing_name in os.listdir(upload_dir):
        if existing_name.startswith("logo."):
            existing_path = upload_dir / existing_name

            if existing_path.is_file():
                existing_path.unlink()

    logo_filename = f"logo{extension}"
    absolute_path = upload_dir / logo_filename

    logo_file.save(str(absolute_path))

    static_root = Path(app.root_path) / "static"

    try:
        relative_path = absolute_path.relative_to(
            static_root
        )
    except ValueError:
        app.logger.warning(
            "Logo saved outside static directory: %s",
            absolute_path
        )
        return None

    return relative_path.as_posix()


def delete_organization_logo_file(
    organization_id,
    logo_path
):
    if logo_path is None:
        return

    expected_prefix = (
        f"uploads/organizations/"
        f"{organization_id}/"
    )

    if not logo_path.startswith(expected_prefix):
        return

    absolute_path = (
        Path(app.root_path)
        / "static"
        / logo_path
    )

    if absolute_path.is_file():
        absolute_path.unlink()


def get_agent_scope():
    user = get_current_user()
    agent_id = scoped_agent_id(user)

    if agent_id is not None:
        return agent_id, False

    if is_agent(user):
        return None, True

    return None, False


def ensure_agent_scope(agent_id, organization_id):
    if get_agent_record(
        agent_id,
        organization_id
    ) is None:
        abort(404)

    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        abort(403)

    if (
        scoped_id is not None
        and agent_id != scoped_id
    ):
        abort(403)


def ensure_operation_scope(
    operation,
    organization_id
):
    if operation["organization_id"] != organization_id:
        abort(403)

    ensure_agent_scope(
        operation["agent_db_id"],
        organization_id
    )


def ensure_property_scope(
    property_id,
    organization_id
):
    property_data = get_property_record(
        property_id,
        organization_id
    )

    if property_data is None:
        abort(404)

    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        abort(403)

    if (
        scoped_id is not None
        and property_data["agent_id"] != scoped_id
    ):
        abort(403)

    return property_data


def ensure_property_view_scope(
    property_id,
    organization_id
):
    property_data = get_property_record(
        property_id,
        organization_id
    )

    if property_data is None:
        abort(404)

    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        abort(403)

    if (
        scoped_id is not None
        and property_data["agent_id"] != scoped_id
    ):
        abort(403)

    if get_guest_access() is not None:
        if property_data.get(
            "status",
            PROPERTY_STATUS_APPROVED
        ) != PROPERTY_STATUS_APPROVED:
            abort(403)

    return property_data


def _default_listing_form():
    return {
        "provider": PROVIDER_REMAX_WEB,
        "url": "",
        "status": STATUS_ACTIVE,
        "external_id": "",
        "provider_label": "",
    }


def _listing_form_template_context(
    property_data,
    listing_data=None,
    errors=None,
    is_edit=False,
    listing_conflict=None,
):
    language = get_current_language()

    return {
        "property_data": property_data,
        "listing_data": listing_data or _default_listing_form(),
        "provider_options": provider_options(language),
        "status_options": status_options(language),
        "errors": localize_form_errors(errors or []),
        "is_edit": is_edit,
        "provider_other": PROVIDER_OTHER,
        "listing_conflict": listing_conflict,
    }


def _property_form_fields_from_request():
    listing_price_raw = request.form.get(
        "listing_price",
        "",
    ).strip()

    listing_price, _price_error = parse_positive_float(
        listing_price_raw or None,
        "Property listing price",
    )

    return {
        "address": request.form.get("address", "").strip(),
        "jurisdiction": request.form.get(
            "jurisdiction",
            "",
        ).strip(),
        "property_type": request.form.get(
            "property_type",
            "",
        ).strip(),
        "listing_price_raw": listing_price_raw,
        "listing_price": listing_price,
        "listing_purpose": request.form.get(
            "listing_purpose",
            "",
        ).strip(),
        "agent_id": request.form.get(
            "agent_id",
            "",
        ).strip(),
    }


def _property_form_context(property_data):
    return {
        "property_types": PROPERTY_TYPES,
        "listing_purposes": LISTING_PURPOSES,
    }


def _property_form_data_from_fields(fields, owner_agent_id):
    return {
        "address": fields["address"],
        "jurisdiction": fields["jurisdiction"],
        "agent_id": owner_agent_id or "",
        "property_type": fields["property_type"],
        "listing_price": (
            fields["listing_price_raw"]
            if fields["listing_price"] is None
            else fields["listing_price"]
        ),
        "listing_purpose": fields["listing_purpose"],
    }


def ensure_operation_editable(operation):
    if is_admin():
        return

    if is_agent():
        if not agent_can_edit_status(
            operation.get(
                "status",
                STATUS_APPROVED
            )
        ):
            abort(403)
        return

    abort(403)


def resolve_owned_agent_id(form_agent_id):
    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        abort(403)

    if scoped_id is not None:
        return scoped_id, True

    if not form_agent_id:
        return None, False

    try:
        return int(form_agent_id), False
    except (TypeError, ValueError):
        return None, False


def get_empty_dashboard_context():
    return {
        "dashboard": empty_organization_dashboard(
            language=get_current_language()
        ),
        "team_block": None,
    }


def get_dashboard_context(
    organization_id,
    agent_id=None,
    raw_filters=None,
):
    user = get_current_user()

    if is_guest_session():
        role = "guest"
    elif is_agent(user):
        role = "agent"
    else:
        role = "admin"

    dashboard = load_organization_dashboard(
        organization_id,
        raw_filters or {},
        language=get_current_language(),
        scoped_agent_id=agent_id,
        role=role,
        can_write=can_write(user),
        can_manage_approvals=can_approve(user),
        can_create_operations=can_create_operations(
            organization_id
        ),
    )

    language = get_current_language()
    welcome_name = ""

    if user is not None:
        first_name = (user.get("first_name") or "").strip()
        last_name = (user.get("last_name") or "").strip()
        visible_name = f"{first_name} {last_name}".strip()
        welcome_name = (
            visible_name
            or (user.get("agent_name") or "").strip()
            or (user.get("username") or "").strip()
        )
    elif is_guest_session():
        welcome_name = translate("role_guest", language=language)

    dashboard["welcome_name"] = welcome_name

    team_block = None
    if (
        user is not None
        and not is_guest_session()
        and agent_id is not None
    ):
        from modules.team_reports import (
            agent_is_team_leader,
            build_dashboard_team_block,
        )

        if agent_is_team_leader(organization_id, agent_id):
            team_block = build_dashboard_team_block(
                organization_id,
                agent_id,
                language=language,
            )

    return {
        "dashboard": dashboard,
        "team_block": team_block,
    }


def get_agent_by_id(agent_id, organization_id):
    return get_agent_record(
        agent_id,
        organization_id
    )


def can_create_operations(organization_id):
    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        return False

    if scoped_id is not None:
        return (
            len(
                get_properties(
                    organization_id,
                    agent_id=scoped_id
                )
            ) > 0
        )

    return (
        len(get_agents(organization_id)) > 0
        and len(
            get_properties(organization_id)
        ) > 0
    )


def get_property_by_id(
    property_id,
    organization_id
):
    return get_property_record(
        property_id,
        organization_id
    )


def get_user_form_values(form):
    return {
        "username": form.get(
            "username",
            ""
        ).strip(),
        "role": form.get(
            "role",
            ""
        ).strip(),
        "agent_id": form.get(
            "agent_id",
            ""
        ).strip(),
        "is_active": form.get(
            "is_active",
            "no"
        ),
        "password": form.get(
            "password",
            ""
        )
    }


def user_to_form_values(user):
    return {
        "username": user["username"],
        "role": user["role"],
        "agent_id": (
            ""
            if user["agent_id"] is None
            else str(user["agent_id"])
        ),
        "is_active": (
            "yes"
            if user["is_active"]
            else "no"
        ),
        "password": ""
    }


def parse_user_agent_id(raw_agent_id):
    if raw_agent_id == "":
        return None

    try:
        return int(raw_agent_id)

    except ValueError:
        return None


def validate_user_form(
    form_values,
    organization_id,
    is_edit,
    user_id=None
):
    errors = []

    username = form_values["username"]
    role = form_values["role"]
    password = form_values["password"]
    agent_id = parse_user_agent_id(
        form_values["agent_id"]
    )

    if username == "":
        errors.append("err_username_required")

    if role not in USER_ROLES:
        errors.append("err_invalid_role")

    if not is_edit and password == "":
        errors.append("err_password_required")

    elif password != "":
        password_error = validate_password_policy(
            password
        )

        if password_error is not None:
            errors.append(password_error)

    if role == ROLE_AGENT:
        if (
            agent_id is not None
            and get_agent_by_id(
                agent_id,
                organization_id
            ) is None
        ):
            errors.append("err_invalid_agent")

    if username != "":
        existing = get_user_by_username(
            username,
            organization_id=organization_id
        )

        if (
            existing is not None
            and existing["id"] != user_id
        ):
            errors.append("user_exists")

    if role != ROLE_AGENT:
        agent_id = None

    return errors, agent_id


def render_user_form(
    form_values,
    errors,
    organization_id,
    is_edit,
    user_id=None
):
    return render_template(
        "users/form.html",
        form_values=form_values,
        agents=get_agents(organization_id),
        roles=USER_ROLES,
        errors=localize_form_errors(errors),
        is_edit=is_edit,
        user_id=user_id
    )


def get_new_operation_form_values(form):
    return {
        "agent_id": form.get("agent_id", ""),
        "property_id": form.get("property_id", ""),
        "search_mode": form.get("search_mode", "agent"),
        "currency": form.get("currency", "USD"),
        "original_amount": form.get("original_amount", ""),
        "exchange_rate": form.get("exchange_rate", ""),
        "operation_date": form.get("operation_date", ""),
        "seller_side_active": form.get("seller_side_active", ""),
        "buyer_side_active": form.get("buyer_side_active", ""),
        "is_referred": form.get("is_referred", ""),
        "referred_side": form.get("referred_side", ""),
        "seller_commission_rate": form.get(
            "seller_commission_rate",
            "",
        ),
        "buyer_commission_rate": form.get(
            "buyer_commission_rate",
            "",
        ),
        "seller_vat_amount": form.get("seller_vat_amount", "0"),
        "buyer_vat_amount": form.get("buyer_vat_amount", "0"),
    }


def process_new_operation_submission(
    form_values,
    organization_id,
    operation_display_id=None,
):
    return prepare_new_operation_from_form(
        form_values,
        organization_id,
        operation_display_id=operation_display_id,
    )


def get_operation_form_values(form):
    return {
        "agent_id": form.get(
            "agent_id",
            ""
        ),
        "property_id": form.get(
            "property_id",
            ""
        ),
        "currency": form.get(
            "currency",
            "USD"
        ),
        "original_amount": form.get(
            "original_amount",
            form.get("sale_price", "")
        ),
        "exchange_rate": form.get(
            "exchange_rate",
            ""
        ),
        "commission_rate": form.get(
            "commission_rate",
            ""
        ),
        "was_invoiced": form.get(
            "was_invoiced",
            "no"
        ),
        "invoice_full_commission": form.get(
            "invoice_full_commission",
            "no",
        ),
        "vat_amount": form.get(
            "vat_amount",
            "0"
        ),
        "operation_date": form.get(
            "operation_date",
            ""
        )
    }


def operation_to_form_values(operation):
    currency = operation.get(
        "currency",
        "USD"
    )
    exchange_rate = operation.get(
        "exchange_rate",
        1
    )
    original_amount = operation.get(
        "original_amount",
        operation["sale_price"]
    )
    vat_amount = convert_from_usd(
        operation["vat_amount"],
        currency,
        exchange_rate
    )

    return {
        "agent_id": str(
            operation["agent_db_id"]
        ),
        "property_id": str(
            operation["property_db_id"]
        ),
        "currency": currency,
        "original_amount": str(
            original_amount
        ),
        "exchange_rate": (
            ""
            if currency == "USD"
            else str(exchange_rate)
        ),
        "commission_rate": str(
            operation["commission_rate"]
        ),
        "was_invoiced": operation[
            "was_invoiced"
        ],
        "invoice_full_commission": operation.get(
            "invoice_full_commission",
            "no",
        ),
        "vat_amount": str(vat_amount),
        "operation_date": operation["date"]
    }


def render_operation_form(
    form_title,
    submit_label,
    preview_label,
    form_values,
    errors,
    organization_id,
    is_edit,
    operation_id=None,
    show_submit_for_approval=False,
    operation_readiness=None,
):
    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        agents = []
        properties = []
        property_options = []
    elif scoped_id is not None:
        agents = [
            agent
            for agent in get_agents(
                organization_id
            )
            if agent["id"] == scoped_id
        ]
        properties = get_properties(
            organization_id,
            agent_id=scoped_id
        )
        property_options = properties
        form_values = dict(form_values)
        form_values["agent_id"] = str(scoped_id)
    else:
        agents = get_agents(organization_id)
        property_options = get_properties(
            organization_id
        )
        selected_agent_raw = str(
            form_values.get("agent_id") or ""
        ).strip()

        if selected_agent_raw:
            try:
                selected_agent_id = int(
                    selected_agent_raw
                )
            except (TypeError, ValueError):
                selected_agent_id = None

            if selected_agent_id is not None:
                properties = get_properties(
                    organization_id,
                    agent_id=selected_agent_id,
                )
            else:
                properties = []
        else:
            properties = []

    return render_template(
        "operations/form.html",
        form_title=form_title,
        submit_label=submit_label,
        preview_label=preview_label,
        form_values=form_values,
        agents=agents,
        properties=properties,
        property_options=property_options,
        errors=localize_form_errors(errors),
        is_edit=is_edit,
        operation_id=operation_id,
        can_create_operations=can_create_operations(
            organization_id
        ),
        show_submit_for_approval=show_submit_for_approval,
        operation_readiness=operation_readiness,
        property_types=PROPERTY_TYPES,
        lock_agent_selection=(scoped_id is not None),
        org_commission_defaults=get_new_operation_form_defaults(
            organization_id
        ) if not is_edit else None,
    )


def process_operation_submission(
    form_values,
    organization_id,
    operation_display_id=None
):
    return prepare_operation_from_form(
        form_values,
        organization_id,
        operation_display_id=operation_display_id
    )


@app.errorhandler(404)
def handle_not_found(error):
    flash_i18n("record_not_found", "error")

    return redirect(
        url_for("dashboard")
    )


@app.errorhandler(403)
def handle_forbidden(error):
    if (
        get_current_user() is not None
        and get_user_organization_id() is None
    ):
        logout_user()

        return redirect(
            url_for("login")
        )

    flash_i18n("access_denied", "error")

    return redirect(
        url_for("dashboard")
    )


@app.errorhandler(500)
def handle_server_error(error):
    app.logger.exception(
        "Unhandled server error: %s",
        error
    )

    if app.config.get("DEBUG"):
        raise error

    flash_i18n("error_500_message", "error")

    user = get_current_user()

    if user is None:
        return render_template(
            "errors/500.html"
        ), 500

    return render_template(
        "errors/500.html"
    ), 500


@app.route(
    "/login",
    methods=[
        "GET",
        "POST"
    ]
)
def login():
    next_url = get_safe_redirect_target(
        request.args.get("next")
    )

    if (
        get_current_user() is not None
        or get_guest_access() is not None
    ):
        return redirect(
            next_url
            or url_for("dashboard")
        )

    username = ""

    if request.method == "POST":
        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        next_url = get_safe_redirect_target(
            request.form.get("next")
        ) or next_url

        user, login_error = authenticate_user(
            username,
            password
        )

        if user is None:
            flash_i18n(
                login_error or "login_invalid",
                "error"
            )
        else:
            login_user(user)

            return redirect(
                next_url
                or url_for("dashboard")
            )

    return render_template(
        "auth/login.html",
        username=username,
        next_url=next_url or ""
    )


@app.route(
    "/register",
    methods=[
        "GET",
        "POST"
    ]
)
def register():
    form_values = {
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "organization_code": "",
        "password": "",
        "confirm_password": ""
    }

    if request.method == "POST":
        form_values = {
            "first_name": request.form.get(
                "first_name",
                ""
            ),
            "last_name": request.form.get(
                "last_name",
                ""
            ),
            "email": request.form.get(
                "email",
                ""
            ),
            "phone": request.form.get(
                "phone",
                ""
            ),
            "organization_code": request.form.get(
                "organization_code",
                ""
            ),
            "password": request.form.get(
                "password",
                ""
            ),
            "confirm_password": request.form.get(
                "confirm_password",
                ""
            )
        }

        errors, parsed = validate_registration_form(
            form_values
        )

        if len(errors) == 0:
            errors, result = submit_agent_registration(
                parsed,
                language=get_current_language()
            )

            if len(errors) == 0:
                action = result.get("action", "created")

                if action == "created":
                    session["pending_registration_id"] = (
                        result["request_id"]
                    )

                    return redirect(
                        url_for("verify_email")
                    )

                if action == "continue_verification":
                    return render_template(
                        "auth/register.html",
                        form_values=form_values,
                        errors=[],
                        continue_verification=True,
                        awaiting_approval=False,
                        masked_email=result["masked_email"]
                    )

                if action == "awaiting_approval":
                    return render_template(
                        "auth/register.html",
                        form_values=form_values,
                        errors=[],
                        continue_verification=False,
                        awaiting_approval=True,
                        masked_email=result["masked_email"]
                    )

        return render_template(
            "auth/register.html",
            form_values=form_values,
            errors=localize_form_errors(errors),
            continue_verification=False,
            awaiting_approval=False,
            masked_email=None
        )

    return render_template(
        "auth/register.html",
        form_values=form_values,
        errors=[],
        continue_verification=False,
        awaiting_approval=False,
        masked_email=None
    )


@app.route(
    "/register/continue-verification",
    methods=["POST"]
)
def register_continue_verification():
    email = request.form.get("email", "").strip()
    organization_code = request.form.get(
        "organization_code",
        ""
    ).strip()

    result, error_key = resume_email_verification(
        email,
        organization_code
    )

    if error_key is not None:
        flash_i18n(error_key, "error")

        return redirect(
            url_for("register")
        )

    if result["action"] == "awaiting_approval":
        flash_i18n(
            "registration_awaiting_approval",
            "message"
        )

        return redirect(
            url_for("login")
        )

    session["pending_registration_id"] = result[
        "request_id"
    ]

    return redirect(
        url_for("verify_email")
    )


@app.route(
    "/verify-email",
    methods=[
        "GET",
        "POST"
    ]
)
def verify_email():
    request_id = session.get("pending_registration_id")

    if request_id is None:
        flash_i18n("err_verify_session_missing", "error")

        return redirect(
            url_for("register")
        )

    request_data = get_registration_request(request_id)

    if (
        request_data is None
        or request_data["status"] != "email_pending"
    ):
        session.pop("pending_registration_id", None)

        if (
            request_data is not None
            and request_data["status"] == "pending_approval"
        ):
            return render_template(
                "auth/verify_email.html",
                verified=True,
                masked_email=mask_email(
                    request_data["email"]
                ),
                errors=[]
            )

        flash_i18n("err_verify_invalid", "error")

        return redirect(
            url_for("register")
        )

    errors = []

    if request.method == "POST":
        digits = []

        for index in range(1, 7):
            digits.append(
                request.form.get(
                    f"digit{index}",
                    ""
                ).strip()
            )

        raw_code = "".join(digits)

        if raw_code == "":
            raw_code = request.form.get(
                "code",
                ""
            ).strip()

        ok, error_key = verify_registration_code(
            request_id,
            raw_code
        )

        if ok:
            return render_template(
                "auth/verify_email.html",
                verified=True,
                masked_email=mask_email(
                    request_data["email"]
                ),
                errors=[]
            )

        errors = localize_form_errors([error_key])

    return render_template(
        "auth/verify_email.html",
        verified=False,
        masked_email=mask_email(
            request_data["email"]
        ),
        errors=errors
    )


@app.route(
    "/verify-email/resend",
    methods=["POST"]
)
def verify_email_resend():
    request_id = session.get("pending_registration_id")

    if request_id is None:
        flash_i18n("err_verify_session_missing", "error")

        return redirect(
            url_for("register")
        )

    ok, error_key = resend_verification_code(
        request_id,
        language=get_current_language()
    )

    if ok:
        flash_i18n("verify_code_resent", "success")
    else:
        flash_i18n(error_key, "error")

    return redirect(
        url_for("verify_email")
    )


@app.route("/guest/<token>")
def guest_access(token):
    access, error_key = open_guest_access(token)

    if access is None:
        flash_i18n(error_key, "error")

        return redirect(
            url_for("login")
        )

    login_guest_access(
        access,
        hash_access_secret(token)
    )

    flash_i18n("guest_access_granted", "success")

    return redirect(
        url_for("dashboard")
    )


@app.route("/logout")
@login_required
def logout():
    logout_user()

    flash_i18n("logout_success", "success")

    return redirect(
        url_for("login")
    )


@app.route("/access-requests")
@admin_required
def access_requests_list():
    organization_id = require_user_organization()

    requests_list = list_registration_requests(
        organization_id
    )

    return render_template(
        "admin/access_requests.html",
        requests=requests_list,
        request_count=len(requests_list)
    )


@app.route(
    "/access-requests/<int:request_id>",
    methods=[
        "GET",
        "POST"
    ]
)
@admin_required
def access_request_detail(request_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    request_data = get_registration_request(
        request_id,
        organization_id
    )

    if request_data is None:
        abort(404)

    agents = get_agents(organization_id)
    suggestions = suggested_agents_for_request(
        request_data,
        organization_id
    )

    if request.method == "POST":
        action = request.form.get("action")

        if action == "approve":
            agent_mode = request.form.get(
                "agent_mode",
                "create"
            )
            agent_id = request.form.get(
                "agent_id",
                ""
            ).strip()
            agent_type = request.form.get(
                "agent_type",
                "Alto"
            )

            create_agent = agent_mode == "create"
            linked_agent_id = None

            if not create_agent and agent_id != "":
                try:
                    linked_agent_id = int(agent_id)
                except ValueError:
                    linked_agent_id = None

            errors, _result = approve_registration_request(
                request_id,
                organization_id,
                current_user["id"],
                agent_id=linked_agent_id,
                create_agent=create_agent,
                agent_type=agent_type,
                language=get_current_language()
            )

            if len(errors) > 0:
                return render_template(
                    "admin/access_request_detail.html",
                    request_data=request_data,
                    agents=agents,
                    suggestions=suggestions,
                    agent_types=AGENT_TYPES,
                    errors=localize_form_errors(errors)
                )

            flash_i18n("access_request_approved", "success")

            return redirect(
                url_for("access_requests_list")
            )

        if action == "reject":
            reason = request.form.get(
                "rejection_reason",
                ""
            ).strip()

            if reason == "":
                return render_template(
                    "admin/access_request_detail.html",
                    request_data=request_data,
                    agents=agents,
                    suggestions=suggestions,
                    agent_types=AGENT_TYPES,
                    errors=localize_form_errors([
                        "rejection_reason_required"
                    ])
                )

            reject_access_request(
                request_id,
                organization_id,
                current_user["id"],
                reason,
                language=get_current_language()
            )

            flash_i18n("access_request_rejected", "success")

            return redirect(
                url_for("access_requests_list")
            )

    return render_template(
        "admin/access_request_detail.html",
        request_data=request_data,
        agents=agents,
        suggestions=suggestions,
        agent_types=AGENT_TYPES,
        errors=[]
    )


@app.route("/approvals")
@admin_required
def approvals_list():
    organization_id = require_user_organization()

    items = list_pending_approval_items(
        organization_id
    )

    return render_template(
        "approvals/list.html",
        items=items,
        item_count=len(items)
    )


@app.route("/approvals/properties/<int:property_id>")
@admin_required
def approvals_property_detail(property_id):
    organization_id = require_user_organization()

    property_data = get_property_record(
        property_id,
        organization_id
    )

    if (
        property_data is None
        or property_data.get("status") != PROPERTY_STATUS_PENDING
    ):
        abort(404)

    language = get_current_language()

    return render_template(
        "approvals/property_review.html",
        property_data=property_data,
        property_display_id=format_property_display_id(
            property_data["id"]
        ),
        listings=load_property_listings_for_property(
            property_data,
            language=language,
        ),
        errors=[]
    )


@app.route(
    "/approvals/properties/<int:property_id>/approve",
    methods=["POST"]
)
@admin_required
def approvals_property_approve(property_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    property_data = get_property_record(
        property_id,
        organization_id
    )

    if (
        property_data is None
        or property_data.get("status") != PROPERTY_STATUS_PENDING
    ):
        abort(404)

    update_property_status(
        property_id,
        organization_id,
        PROPERTY_STATUS_APPROVED,
        reviewed_by_user_id=current_user["id"],
        reviewed_at=datetime.utcnow().isoformat(
            timespec="seconds"
        ),
        rejection_reason=None
    )

    if property_data.get("agent_id") is not None:
        notify_agent_for_property(
            organization_id,
            property_data["agent_id"],
            "property_approved",
            property_id,
            {
                "address": property_data["address"],
                "status": PROPERTY_STATUS_APPROVED
            },
            actor_user_id=current_user["id"]
        )

    flash_i18n("property_approved", "success")

    return redirect(url_for("approvals_list"))


@app.route(
    "/approvals/properties/<int:property_id>/reject",
    methods=["POST"]
)
@admin_required
def approvals_property_reject(property_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    property_data = get_property_record(
        property_id,
        organization_id
    )

    if (
        property_data is None
        or property_data.get("status") != PROPERTY_STATUS_PENDING
    ):
        abort(404)

    reason = request.form.get(
        "rejection_reason",
        ""
    ).strip()

    if reason == "":
        return render_template(
            "approvals/property_review.html",
            property_data=property_data,
            errors=localize_form_errors([
                "rejection_reason_required"
            ])
        )

    update_property_status(
        property_id,
        organization_id,
        PROPERTY_STATUS_REJECTED,
        reviewed_by_user_id=current_user["id"],
        reviewed_at=datetime.utcnow().isoformat(
            timespec="seconds"
        ),
        rejection_reason=reason
    )

    if property_data.get("agent_id") is not None:
        notify_agent_for_property(
            organization_id,
            property_data["agent_id"],
            "property_rejected",
            property_id,
            {
                "address": property_data["address"],
                "status": PROPERTY_STATUS_REJECTED,
                "reason": reason
            },
            actor_user_id=current_user["id"]
        )

    flash_i18n("property_rejected", "success")

    return redirect(url_for("approvals_list"))


@app.route(
    "/approvals/property-changes/<int:request_id>"
)
@admin_required
def approvals_property_change_detail(request_id):
    organization_id = require_user_organization()

    change_request = get_property_change_request(
        request_id,
        organization_id
    )

    if (
        change_request is None
        or change_request["status"] != "pending"
    ):
        abort(404)

    return render_template(
        "approvals/property_change_review.html",
        change_request=change_request,
        errors=[]
    )


@app.route(
    "/approvals/property-changes/<int:request_id>/approve",
    methods=["POST"]
)
@admin_required
def approvals_property_change_approve(request_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    change_request = get_property_change_request(
        request_id,
        organization_id
    )

    if (
        change_request is None
        or change_request["status"] != "pending"
    ):
        abort(404)

    approve_property_change_request(
        request_id,
        organization_id,
        current_user["id"]
    )

    if change_request.get("current_agent_id") is not None:
        notify_agent_for_property_change(
            organization_id,
            change_request["current_agent_id"],
            "property_change_approved",
            request_id,
            {
                "address": change_request["proposed_address"],
                "status": "approved"
            },
            actor_user_id=current_user["id"]
        )

    flash_i18n("property_change_approved", "success")

    return redirect(url_for("approvals_list"))


@app.route(
    "/approvals/property-changes/<int:request_id>/reject",
    methods=["POST"]
)
@admin_required
def approvals_property_change_reject(request_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    change_request = get_property_change_request(
        request_id,
        organization_id
    )

    if (
        change_request is None
        or change_request["status"] != "pending"
    ):
        abort(404)

    reason = request.form.get(
        "rejection_reason",
        ""
    ).strip()

    if reason == "":
        return render_template(
            "approvals/property_change_review.html",
            change_request=change_request,
            errors=localize_form_errors([
                "rejection_reason_required"
            ])
        )

    reject_property_change_request(
        request_id,
        organization_id,
        current_user["id"],
        reason
    )

    if change_request.get("current_agent_id") is not None:
        notify_agent_for_property_change(
            organization_id,
            change_request["current_agent_id"],
            "property_change_rejected",
            request_id,
            {
                "address": change_request["current_address"],
                "status": "rejected",
                "reason": reason
            },
            actor_user_id=current_user["id"]
        )

    flash_i18n("property_change_rejected", "success")

    return redirect(url_for("approvals_list"))


@app.route("/notifications")
@login_required
def notifications_list():
    user = get_current_user()

    if user is None:
        abort(403)

    organization_id = require_user_organization()

    notifications = list_notifications(
        user["id"],
        organization_id
    )

    return render_template(
        "notifications/list.html",
        notifications=notifications
    )


@app.route(
    "/notifications/<int:notification_id>/read",
    methods=["POST"]
)
@login_required
def notifications_mark_read(notification_id):
    user = get_current_user()

    if user is None:
        abort(403)

    mark_notification_read(
        notification_id,
        user["id"],
        user["organization_id"]
    )

    return redirect(url_for("notifications_list"))


@app.route(
    "/notifications/read-all",
    methods=["POST"]
)
@login_required
def notifications_mark_all_read():
    user = get_current_user()

    if user is None:
        abort(403)

    mark_all_notifications_read(
        user["id"],
        user["organization_id"]
    )

    flash_i18n("notifications_marked_read", "success")

    return redirect(url_for("notifications_list"))


@app.route("/users")
@admin_required
def users_list():
    organization_id = require_user_organization()

    users = get_users(organization_id)

    return render_template(
        "users/list.html",
        users=users,
        user_count=len(users)
    )


@app.route(
    "/users/new",
    methods=[
        "GET",
        "POST"
    ]
)
@admin_required
def users_new():
    organization_id = require_user_organization()

    if request.method == "POST":
        form_values = get_user_form_values(
            request.form
        )

        errors, agent_id = validate_user_form(
            form_values,
            organization_id,
            is_edit=False
        )

        if errors:
            return render_user_form(
                form_values,
                errors,
                organization_id,
                is_edit=False
            )

        add_user(
            form_values["username"],
            hash_password(
                form_values["password"]
            ),
            form_values["role"],
            organization_id,
            agent_id=agent_id,
            is_active=(
                form_values["is_active"] == "yes"
            )
        )

        flash_i18n("user_added", "success")

        return redirect(
            url_for("users_list")
        )

    return render_user_form(
        {
            "username": "",
            "role": "",
            "agent_id": "",
            "is_active": "yes",
            "password": ""
        },
        [],
        organization_id,
        is_edit=False
    )


@app.route(
    "/users/<int:user_id>/edit",
    methods=[
        "GET",
        "POST"
    ]
)
@admin_required
def users_edit(user_id):
    organization_id = require_user_organization()

    user = get_user_by_id(user_id)

    if (
        user is None
        or user["organization_id"] != organization_id
    ):
        abort(404)

    if request.method == "POST":
        form_values = get_user_form_values(
            request.form
        )

        errors, agent_id = validate_user_form(
            form_values,
            organization_id,
            is_edit=True,
            user_id=user_id
        )

        if errors:
            return render_user_form(
                form_values,
                errors,
                organization_id,
                is_edit=True,
                user_id=user_id
            )

        password_hash = None

        if form_values["password"] != "":
            password_hash = hash_password(
                form_values["password"]
            )

        update_user(
            user_id,
            form_values["username"],
            form_values["role"],
            organization_id,
            agent_id=agent_id,
            is_active=(
                form_values["is_active"] == "yes"
            ),
            password_hash=password_hash
        )

        flash_i18n("user_updated", "success")

        return redirect(
            url_for("users_list")
        )

    return render_user_form(
        user_to_form_values(user),
        [],
        organization_id,
        is_edit=True,
        user_id=user_id
    )


@app.route(
    "/users/<int:user_id>/delete",
    methods=["POST"]
)
@admin_required
def users_delete(user_id):
    organization_id = require_user_organization()

    user = get_user_by_id(user_id)

    if (
        user is None
        or user["organization_id"] != organization_id
    ):
        abort(404)

    current_user = get_current_user()

    if current_user["id"] == user_id:
        flash_i18n("cannot_delete_self", "error")

        return redirect(
            url_for(
                "users_edit",
                user_id=user_id
            )
        )

    confirm_delete = request.form.get(
        "confirm_delete"
    )

    if confirm_delete != "yes":
        flash_i18n("deletion_cancelled", "error")

        return redirect(
            url_for(
                "users_edit",
                user_id=user_id
            )
        )

    delete_user(user_id, organization_id)

    flash_i18n("user_deleted", "success")

    return redirect(
        url_for("users_list")
    )


@app.route(
    "/settings/organization",
    methods=[
        "GET",
        "POST"
    ]
)
@admin_required
def organization_settings():
    organization_id = require_user_organization()

    current_settings = get_organization_settings(
        organization_id
    )

    if current_settings is None:
        abort(404)

    if request.method == "POST":
        remove_logo = request.form.get(
            "remove_logo"
        ) == "yes"

        errors, parsed = (
            validate_organization_settings_form(
                request.form,
                logo_file=request.files.get(
                    "logo"
                ),
                remove_logo=remove_logo
            )
        )

        if len(errors) > 0:
            form_values = {
                "display_name": request.form.get(
                    "display_name",
                    ""
                ).strip(),
                "default_language": request.form.get(
                    "default_language",
                    DEFAULT_LANGUAGE
                ),
                "default_currency": request.form.get(
                    "default_currency",
                    "USD"
                ),
                "timezone": request.form.get(
                    "timezone",
                    ""
                ).strip(),
                "accent_color": request.form.get(
                    "accent_color",
                    ""
                ).strip(),
                "legal_name": request.form.get(
                    "legal_name",
                    ""
                ).strip(),
                "tax_id": request.form.get(
                    "tax_id",
                    ""
                ).strip(),
                "tax_condition": request.form.get(
                    "tax_condition",
                    ""
                ).strip(),
                "fiscal_address": request.form.get(
                    "fiscal_address",
                    ""
                ).strip(),
                "trade_name": request.form.get(
                    "trade_name",
                    ""
                ).strip(),
                "billing_email": request.form.get(
                    "billing_email",
                    ""
                ).strip(),
                "default_payment_condition": request.form.get(
                    "default_payment_condition",
                    "cuenta_corriente"
                ).strip(),
            }

            return render_template(
                "settings/organization.html",
                form_values=form_values,
                errors=localize_form_errors(errors),
                current_logo_url=(
                    url_for(
                        "static",
                        filename=current_settings[
                            "logo_path"
                        ]
                    )
                    if current_settings["logo_path"]
                    else None
                ),
                timezones=COMMON_TIMEZONES,
                tax_conditions=TAX_CONDITIONS,
                payment_conditions=PAYMENT_CONDITIONS,
                has_registration_code=current_settings.get(
                    "has_registration_code"
                ),
                registration_enabled=current_settings.get(
                    "registration_enabled",
                    True
                ),
                guest_links=list_guest_accesses(
                    organization_id
                ),
                new_registration_code=None,
                new_guest_url=None
            )

        logo_path = current_settings["logo_path"]
        logo_file = request.files.get("logo")

        if (
            logo_file is not None
            and logo_file.filename
        ):
            saved_path = save_organization_logo(
                organization_id,
                logo_file
            )

            if saved_path is not None:
                delete_organization_logo_file(
                    organization_id,
                    logo_path
                )
                logo_path = saved_path

        elif remove_logo:
            delete_organization_logo_file(
                organization_id,
                logo_path
            )
            logo_path = None

        update_organization_settings(
            organization_id,
            parsed["display_name"],
            parsed["default_language"],
            parsed["default_currency"],
            parsed["timezone"],
            logo_path,
            parsed["accent_color"]
        )

        update_organization_billing_fields(
            organization_id,
            legal_name=parsed.get("legal_name"),
            tax_id=parsed.get("tax_id"),
            tax_condition=parsed.get("tax_condition"),
            fiscal_address=parsed.get("fiscal_address"),
            trade_name=parsed.get("trade_name"),
            billing_email=parsed.get("billing_email"),
            default_payment_condition=parsed.get(
                "default_payment_condition"
            ),
            default_invoice_description=parsed.get(
                "default_invoice_description"
            ),
            default_buyer_commission_percent=parsed.get(
                "default_buyer_commission_percent"
            ),
            default_seller_commission_percent=parsed.get(
                "default_seller_commission_percent"
            ),
        )

        session["language"] = parsed[
            "default_language"
        ]

        flash_i18n("settings_saved", "success")

        return redirect(
            url_for("organization_settings")
        )

    return render_template(
        "settings/organization.html",
        form_values=settings_to_form_values(
            current_settings
        ),
        errors=[],
        current_logo_url=(
            url_for(
                "static",
                filename=current_settings[
                    "logo_path"
                ]
            )
            if current_settings["logo_path"]
            else None
        ),
        timezones=COMMON_TIMEZONES,
        tax_conditions=TAX_CONDITIONS,
        payment_conditions=PAYMENT_CONDITIONS,
        has_registration_code=current_settings.get(
            "has_registration_code"
        ),
        registration_enabled=current_settings.get(
            "registration_enabled",
            True
        ),
        guest_links=list_guest_accesses(
            organization_id
        ),
        new_registration_code=None,
        new_guest_url=None
    )


@app.route(
    "/integrations/csv",
    methods=["GET", "POST"],
)
@admin_required
def csv_import_upload():
    organization_id = require_user_organization()

    if request.method == "GET":
        return render_template(
            "integrations/csv_upload.html"
        )

    upload = request.files.get("csv_file")

    if upload is None or upload.filename == "":
        flash_i18n("csv_import_no_file", "error")
        return redirect(url_for("csv_import_upload"))

    raw = upload.read()
    batch = preview_csv_upload(
        organization_id,
        raw,
        filename=upload.filename,
    )

    session["csv_import_batch_id"] = batch["id"]

    return redirect(
        url_for(
            "csv_import_preview",
            batch_id=batch["id"],
        )
    )


@app.route(
    "/integrations/csv/preview/<batch_id>",
    methods=["GET"],
)
@admin_required
def csv_import_preview(batch_id):
    organization_id = require_user_organization()

    from modules.database.csv_import_batches_repository import (
        get_csv_import_batch,
    )

    batch = get_csv_import_batch(
        batch_id,
        organization_id,
    )

    if batch is None:
        flash_i18n("csv_import_batch_missing", "error")
        return redirect(url_for("csv_import_upload"))

    return render_template(
        "integrations/csv_preview.html",
        batch_id=batch["id"],
        preview=batch["preview"],
    )


@app.route(
    "/integrations/csv/confirm",
    methods=["POST"],
)
@admin_required
def csv_import_confirm():
    organization_id = require_user_organization()
    batch_id = request.form.get("batch_id") or session.get(
        "csv_import_batch_id"
    )

    if not batch_id:
        flash_i18n("csv_import_batch_missing", "error")
        return redirect(url_for("csv_import_upload"))

    try:
        result = confirm_csv_upload(
            organization_id,
            batch_id,
        )
    except ValueError as error:
        code = str(error)
        if code == "csv_batch_has_blockers":
            flash_i18n("csv_import_blocked", "error")
        else:
            flash_i18n("csv_import_batch_missing", "error")
        return redirect(url_for("csv_import_upload"))

    session.pop("csv_import_batch_id", None)

    flash_i18n(
        "csv_import_success",
        "success",
    )
    flash(
        (
            f"agents +{result.agents_created}/"
            f"~{result.agents_updated}, "
            f"properties +{result.properties_created}/"
            f"~{result.properties_updated}, "
            f"listings +{result.listings_created}/"
            f"~{result.listings_updated}, "
            f"deactivated {result.listings_deactivated}"
        ),
        "success",
    )

    return redirect(url_for("properties_list"))


@app.route(
    "/integrations/csv/cancel",
    methods=["POST"],
)
@admin_required
def csv_import_cancel():
    organization_id = require_user_organization()
    batch_id = request.form.get("batch_id") or session.get(
        "csv_import_batch_id"
    )

    if batch_id:
        cancel_csv_upload(organization_id, batch_id)

    session.pop("csv_import_batch_id", None)
    flash_i18n("csv_import_cancelled", "success")

    return redirect(url_for("csv_import_upload"))


@app.route(
    "/integrations/remax",
    methods=["GET", "POST"],
)
@admin_required
def remax_export_upload():
    organization_id = require_user_organization()
    agents = get_agents(organization_id)

    if request.method == "GET":
        return render_template(
            "integrations/remax_upload.html",
            agents=agents,
        )

    agent_id_raw = request.form.get("agent_id", "").strip()
    upload = request.files.get("remax_file")

    try:
        agent_id = int(agent_id_raw)
    except (TypeError, ValueError):
        flash_i18n("remax_export_agent_required", "error")
        return redirect(url_for("remax_export_upload"))

    agent = get_agent_record(agent_id, organization_id)

    if agent is None:
        flash_i18n("remax_export_agent_invalid", "error")
        return redirect(url_for("remax_export_upload"))

    if upload is None or upload.filename == "":
        flash_i18n("remax_export_no_file", "error")
        return redirect(url_for("remax_export_upload"))

    filename = upload.filename or ""
    lower_name = filename.lower()

    if not (
        lower_name.endswith(".csv")
        or lower_name.endswith(".xlsx")
    ):
        flash_i18n("remax_export_bad_extension", "error")
        return redirect(url_for("remax_export_upload"))

    raw = upload.read()

    try:
        batch = preview_remax_export(
            organization_id,
            raw,
            agent_id=agent_id,
            filename=filename,
        )
    except ValueError as error:
        if str(error) == "remax_agent_not_found":
            flash_i18n("remax_export_agent_invalid", "error")
        else:
            flash_i18n("remax_export_parse_failed", "error")
        return redirect(url_for("remax_export_upload"))

    session["remax_export_batch_id"] = batch["id"]

    return redirect(
        url_for(
            "remax_export_preview",
            batch_id=batch["id"],
        )
    )


@app.route(
    "/integrations/remax/preview/<batch_id>",
    methods=["GET"],
)
@admin_required
def remax_export_preview(batch_id):
    organization_id = require_user_organization()

    from modules.database.csv_import_batches_repository import (
        get_csv_import_batch,
    )
    from modules.property_types import PROPERTY_TYPES
    from modules.validators import JURISDICTIONS

    batch = get_csv_import_batch(
        batch_id,
        organization_id,
    )

    if batch is None:
        flash_i18n("remax_export_batch_missing", "error")
        return redirect(url_for("remax_export_upload"))

    return render_template(
        "integrations/remax_preview.html",
        batch_id=batch["id"],
        preview=batch["preview"],
        property_types=PROPERTY_TYPES,
        jurisdictions=JURISDICTIONS,
    )


@app.route(
    "/integrations/remax/preview/<batch_id>/resolve",
    methods=["POST"],
)
@admin_required
def remax_export_resolve(batch_id):
    organization_id = require_user_organization()

    from modules.database.csv_import_batches_repository import (
        get_csv_import_batch,
    )

    batch = get_csv_import_batch(
        batch_id,
        organization_id,
    )

    if batch is None:
        flash_i18n("remax_export_batch_missing", "error")
        return redirect(url_for("remax_export_upload"))

    overrides = {}
    source_rows = (
        (batch["payload"] or {}).get("meta") or {}
    ).get("source_rows") or []

    for row in source_rows:
        mlsid = row.get("mlsid") or ""
        if not mlsid:
            continue

        jurisdiction = request.form.get(
            f"jurisdiction__{mlsid}",
            "",
        ).strip().upper()
        property_type = request.form.get(
            f"property_type__{mlsid}",
            "",
        ).strip().lower()

        entry = {}
        if jurisdiction in ("CABA", "PBA"):
            entry["jurisdiction"] = jurisdiction
        if property_type:
            entry["property_type"] = property_type

        if entry:
            overrides[mlsid] = entry

    try:
        resolve_remax_export_preview(
            organization_id,
            batch_id,
            overrides,
        )
    except ValueError:
        flash_i18n("remax_export_batch_missing", "error")
        return redirect(url_for("remax_export_upload"))

    flash_i18n("remax_export_overrides_applied", "success")

    return redirect(
        url_for(
            "remax_export_preview",
            batch_id=batch_id,
        )
    )


@app.route(
    "/integrations/remax/confirm",
    methods=["POST"],
)
@admin_required
def remax_export_confirm():
    organization_id = require_user_organization()
    batch_id = request.form.get("batch_id") or session.get(
        "remax_export_batch_id"
    )

    if not batch_id:
        flash_i18n("remax_export_batch_missing", "error")
        return redirect(url_for("remax_export_upload"))

    try:
        result = confirm_remax_export(
            organization_id,
            batch_id,
        )
    except ValueError as error:
        code = str(error)
        if code == "csv_batch_has_blockers":
            flash_i18n("remax_export_blocked", "error")
        else:
            flash_i18n("remax_export_batch_missing", "error")
        return redirect(url_for("remax_export_upload"))

    session.pop("remax_export_batch_id", None)

    flash_i18n("remax_export_success", "success")
    flash(
        (
            f"properties +{result.properties_created}/"
            f"~{result.properties_updated}, "
            f"listings +{result.listings_created}/"
            f"~{result.listings_updated}, "
            f"deactivated {result.listings_deactivated}"
        ),
        "success",
    )

    return redirect(url_for("properties_list"))


@app.route(
    "/integrations/remax/cancel",
    methods=["POST"],
)
@admin_required
def remax_export_cancel():
    organization_id = require_user_organization()
    batch_id = request.form.get("batch_id") or session.get(
        "remax_export_batch_id"
    )

    if batch_id:
        cancel_remax_export(organization_id, batch_id)

    session.pop("remax_export_batch_id", None)
    flash_i18n("remax_export_cancelled", "success")

    return redirect(url_for("remax_export_upload"))


@app.route(
    "/settings/registration-code",
    methods=["POST"]
)
@admin_required
def rotate_registration_code():
    organization_id = require_user_organization()

    action = request.form.get("action", "rotate")

    if action == "disable":
        set_registration_enabled(
            organization_id,
            False
        )
        flash_i18n("registration_disabled", "success")

        return redirect(
            url_for("organization_settings")
        )

    if action == "enable":
        set_registration_enabled(
            organization_id,
            True
        )
        flash_i18n("registration_enabled", "success")

        return redirect(
            url_for("organization_settings")
        )

    new_code = rotate_organization_registration_code(
        organization_id
    )

    current_settings = get_organization_settings(
        organization_id
    )

    return render_template(
        "settings/organization.html",
        form_values=settings_to_form_values(
            current_settings
        ),
        errors=[],
        current_logo_url=(
            url_for(
                "static",
                filename=current_settings["logo_path"]
            )
            if current_settings["logo_path"]
            else None
        ),
        timezones=COMMON_TIMEZONES,
        tax_conditions=TAX_CONDITIONS,
        payment_conditions=PAYMENT_CONDITIONS,
        has_registration_code=True,
        registration_enabled=True,
        guest_links=list_guest_accesses(
            organization_id
        ),
        new_registration_code=new_code,
        new_guest_url=None
    )


@app.route(
    "/settings/guest-links",
    methods=["POST"]
)
@admin_required
def manage_guest_links():
    organization_id = require_user_organization()
    current_user = get_current_user()
    action = request.form.get("action", "create")

    if action == "revoke":
        access_id = request.form.get("access_id", "")

        try:
            access_id = int(access_id)
        except ValueError:
            abort(404)

        revoke_guest_access(
            access_id,
            organization_id
        )
        flash_i18n("guest_link_revoked", "success")

        return redirect(
            url_for("organization_settings")
        )

    label = request.form.get("label", "").strip() or None
    created = create_organization_guest_link(
        organization_id,
        current_user["id"],
        label=label
    )

    current_settings = get_organization_settings(
        organization_id
    )

    return render_template(
        "settings/organization.html",
        form_values=settings_to_form_values(
            current_settings
        ),
        errors=[],
        current_logo_url=(
            url_for(
                "static",
                filename=current_settings["logo_path"]
            )
            if current_settings["logo_path"]
            else None
        ),
        timezones=COMMON_TIMEZONES,
        tax_conditions=TAX_CONDITIONS,
        payment_conditions=PAYMENT_CONDITIONS,
        has_registration_code=current_settings.get(
            "has_registration_code"
        ),
        registration_enabled=current_settings.get(
            "registration_enabled",
            True
        ),
        guest_links=list_guest_accesses(
            organization_id
        ),
        new_registration_code=None,
        new_guest_url=created["guest_url"]
    )


@app.route("/search")
@login_required
def search_results():
    organization_id = require_user_organization()

    search_query = request.args.get(
        "q",
        ""
    ).strip()

    agent_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        flash_i18n("agent_scope_missing", "error")

        results = {
            "query": search_query,
            "agents": [],
            "properties": [],
            "operations": [],
            "total_results": 0,
            "has_query": search_query != ""
        }
    else:
        results = global_search(
            search_query,
            organization_id,
            agent_id=agent_id
        )

    return render_template(
        "search/results.html",
        **results
    )


@app.route("/")
@login_required
def dashboard():
    organization_id = require_user_organization()

    agent_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        flash_i18n("agent_scope_missing", "error")

        return render_template(
            "dashboard.html",
            **get_empty_dashboard_context()
        )

    context = get_dashboard_context(
        organization_id,
        agent_id=agent_id,
        raw_filters=request.args,
    )

    return render_template(
        "dashboard.html",
        **context
    )


def _load_organization_report_for_request():
    organization_id = require_user_organization()
    agent_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        flash_i18n("agent_scope_missing", "error")

    report = load_organization_report(
        organization_id,
        request.args,
        language=get_current_language(),
        scoped_agent_id=(
            0 if scope_blocked else agent_id
        ),
    )

    if scope_blocked:
        report["operations"] = []
        report["agent_ranking"] = []
        report["monthly_series"] = []
        zero_metrics = {}

        for key, value in report["metrics"].items():
            zero_metrics[key] = (
                0.0 if isinstance(value, float) else 0
            )

        report["metrics"] = zero_metrics
        report["status_counts"] = {
            "draft": 0,
            "pending": 0,
            "approved": 0,
            "rejected": 0,
        }

    return report


@app.route("/reports")
@login_required
def reports_index():
    report = _load_organization_report_for_request()
    return render_template(
        "reports/index.html",
        report=report,
    )


@app.route("/reports/pdf")
@login_required
def reports_pdf():
    report = _load_organization_report_for_request()
    pdf_bytes = build_organization_report_pdf(report)
    filename = f"{report['download_basename']}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/reports/xlsx")
@login_required
def reports_xlsx():
    report = _load_organization_report_for_request()
    xlsx_bytes = build_organization_report_xlsx(report)
    filename = f"{report['download_basename']}.xlsx"

    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
        as_attachment=True,
        download_name=filename,
    )


def _parse_team_leader_id(raw):
    value = (raw or "").strip()
    if value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@app.route("/agents")
@admin_required
def agents_list():
    organization_id = require_user_organization()

    search_query = request.args.get(
        "q",
        ""
    ).strip()

    if search_query:
        agents = search_agents(
            search_query,
            organization_id
        )
    else:
        agents = get_agents(organization_id)

    return render_template(
        "agents/list.html",
        agents=agents,
        search_query=search_query,
        agent_count=len(agents)
    )


@app.route("/api/agents/suggest")
@admin_required
def agents_suggest():
    organization_id = require_user_organization()
    query = request.args.get("q", "").strip()
    suggestions = suggest_agents(
        query,
        organization_id,
        limit=8,
    )
    return jsonify([
        {
            "id": agent["id"],
            "name": agent["name"],
            "type": agent["type"],
        }
        for agent in suggestions
    ])


@app.route("/api/properties/suggest")
@write_required
def properties_suggest():
    organization_id = require_user_organization()
    query = request.args.get("q", "").strip()
    agent_id_raw = request.args.get("agent_id", "").strip()
    agent_id = None

    scoped_id, scope_blocked = get_agent_scope()
    if scope_blocked:
        abort(403)

    if scoped_id is not None:
        agent_id = scoped_id
    elif agent_id_raw:
        try:
            agent_id = int(agent_id_raw)
        except (TypeError, ValueError):
            agent_id = None

    suggestions = suggest_available_properties(
        query,
        organization_id,
        agent_id=agent_id,
        limit=15,
    )
    return jsonify(suggestions)


@app.route(
    "/api/properties/<int:property_id>/operation-prefill"
)
@write_required
def property_operation_prefill(property_id):
    organization_id = require_user_organization()
    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        abort(403)

    prefill = get_property_operation_prefill(
        property_id,
        organization_id,
    )

    if prefill is None:
        abort(404)

    if scoped_id is not None and prefill.get("agent_id") != scoped_id:
        abort(403)

    return jsonify(prefill)


@app.route(
    "/agents/new",
    methods=[
        "GET",
        "POST"
    ]
)
@admin_required
def agents_new():
    organization_id = require_user_organization()

    if request.method == "POST":
        name = request.form.get(
            "name",
            ""
        ).strip()

        agent_type = request.form.get(
            "agent_type",
            ""
        )
        team_leader_agent_id = _parse_team_leader_id(
            request.form.get("team_leader_agent_id")
        )

        errors = validate_agent_form(
            name,
            agent_type
        )

        if (
            team_leader_agent_id is not None
            and get_agent_by_id(
                team_leader_agent_id,
                organization_id,
            ) is None
        ):
            errors.append("err_team_leader_invalid")

        if errors:
            return render_template(
                "agents/form.html",
                form_title="New Agent",
                submit_label="Create Agent",
                agent={
                    "name": name,
                    "type": agent_type,
                    "team_leader_agent_id": team_leader_agent_id,
                },
                agent_types=AGENT_TYPES,
                team_leader_candidates=get_agents(
                    organization_id
                ),
                errors=localize_form_errors(errors),
                is_edit=False
            )

        try:
            add_agent(
                name,
                agent_type,
                organization_id,
                team_leader_agent_id=team_leader_agent_id,
            )
        except (ValueError, TenantError):
            flash_i18n("err_team_leader_invalid", "error")
            return redirect(url_for("agents_new"))

        flash_i18n("agent_added", "success")

        return redirect(
            url_for("agents_list")
        )

    return render_template(
        "agents/form.html",
        form_title="New Agent",
        submit_label="Create Agent",
        agent={
            "name": "",
            "type": "",
            "team_leader_agent_id": None,
        },
        agent_types=AGENT_TYPES,
        team_leader_candidates=get_agents(organization_id),
        errors=[],
        is_edit=False
    )


@app.route(
    "/agents/<int:agent_id>/edit",
    methods=[
        "GET",
        "POST"
    ]
)
@admin_required
def agents_edit(agent_id):
    organization_id = require_user_organization()

    ensure_agent_scope(
        agent_id,
        organization_id
    )

    agent = get_agent_by_id(
        agent_id,
        organization_id
    )

    if agent is None:
        abort(404)

    if request.method == "POST":
        name = request.form.get(
            "name",
            ""
        ).strip()

        agent_type = request.form.get(
            "agent_type",
            ""
        )
        team_leader_agent_id = _parse_team_leader_id(
            request.form.get("team_leader_agent_id")
        )

        errors = validate_agent_form(
            name,
            agent_type
        )

        if team_leader_agent_id == agent_id:
            errors.append("err_team_leader_self")

        if (
            team_leader_agent_id is not None
            and get_agent_by_id(
                team_leader_agent_id,
                organization_id,
            ) is None
        ):
            errors.append("err_team_leader_invalid")

        if errors:
            agent["name"] = name
            agent["type"] = agent_type
            agent["team_leader_agent_id"] = (
                team_leader_agent_id
            )

            return render_template(
                "agents/form.html",
                form_title="Edit Agent",
                submit_label="Save Changes",
                agent=agent,
                agent_types=AGENT_TYPES,
                team_leader_candidates=[
                    candidate
                    for candidate in get_agents(
                        organization_id
                    )
                    if candidate["id"] != agent_id
                ],
                errors=localize_form_errors(errors),
                is_edit=True
            )

        try:
            update_agent(
                agent_id,
                name,
                agent_type,
                organization_id,
                team_leader_agent_id=team_leader_agent_id,
                update_team_leader=True,
            )
        except (ValueError, TenantError):
            flash_i18n("err_team_leader_invalid", "error")
            return redirect(
                url_for(
                    "agents_edit",
                    agent_id=agent_id,
                )
            )

        flash_i18n("agent_updated", "success")

        return redirect(
            url_for(
                "agents_detail",
                agent_id=agent_id,
            )
        )

    return render_template(
        "agents/form.html",
        form_title="Edit Agent",
        submit_label="Save Changes",
        agent=agent,
        agent_types=AGENT_TYPES,
        team_leader_candidates=[
            candidate
            for candidate in get_agents(organization_id)
            if candidate["id"] != agent_id
        ],
        errors=[],
        is_edit=True
    )


@app.route("/agents/<int:agent_id>")
@login_required
def agents_detail(agent_id):
    if is_guest_session():
        abort(403)

    organization_id = require_user_organization()
    current_user = get_current_user()

    from modules.team_reports import build_agent_profile_view

    target = get_agent_by_id(agent_id, organization_id)
    if target is None:
        abort(404)

    linked_id = (
        current_user.get("agent_id")
        if current_user is not None
        else None
    )
    is_admin_user = is_admin(current_user)
    is_self = linked_id == agent_id
    is_junior_of_viewer = (
        linked_id is not None
        and target.get("team_leader_agent_id") == linked_id
    )

    if not is_admin_user and not is_self and not is_junior_of_viewer:
        abort(403)

    # Guests never reach here; hide wallet from non-owners except admin/self/TL on junior.
    show_wallet = is_admin_user or is_self
    # TL viewing junior: production stats yes, wallet no (avoids leaking other juniors via TL wallet)
    show_junior_stats = (
        target.get("type") in ("Junior", "RAPP")
        and (is_admin_user or is_self or is_junior_of_viewer)
    )
    include_team_stats = is_admin_user or is_self

    view = build_agent_profile_view(
        organization_id,
        agent_id,
        include_wallet=show_wallet,
        include_team_stats=include_team_stats,
    )

    if view is None:
        abort(404)

    can_view_team_report = (
        view["is_team_leader"]
        and (is_admin_user or is_self)
    )

    return render_template(
        "agents/detail.html",
        agent=view["agent"],
        is_team_leader=view["is_team_leader"],
        juniors=view["juniors"],
        junior_rows=view["junior_rows"],
        team_leader=view["team_leader"],
        own_stats=view["own_stats"],
        wallet_totals=view["totals"],
        wallet_movements=view["movements"],
        show_wallet=show_wallet,
        show_junior_stats=show_junior_stats,
        show_team_leader=bool(view["team_leader"]),
        can_open_team_leader=is_admin_user,
        can_open_juniors=is_admin_user or is_self,
        can_view_team_report=can_view_team_report,
        can_edit=is_admin_user,
    )


@app.route("/wallet")
@login_required
def my_wallet():
    if is_guest_session():
        abort(403)

    organization_id = require_user_organization()
    current_user = get_current_user()
    agent_id = current_user.get("agent_id")

    if agent_id is None:
        flash_i18n("wallet_no_linked_agent", "error")
        return redirect(url_for("dashboard"))

    return redirect(
        url_for("agents_detail", agent_id=agent_id)
    )


def _require_team_report_access(team_leader_id):
    if is_guest_session():
        abort(403)

    organization_id = require_user_organization()
    current_user = get_current_user()
    leader = get_agent_by_id(team_leader_id, organization_id)

    if leader is None:
        abort(404)

    if is_admin(current_user):
        return organization_id, leader

    linked_id = current_user.get("agent_id")
    if linked_id == team_leader_id:
        return organization_id, leader

    abort(403)


def _load_team_report_for_request(team_leader_id):
    organization_id, _leader = _require_team_report_access(
        team_leader_id
    )

    # Admin may switch leader via query param
    current_user = get_current_user()
    selected_id = team_leader_id
    if is_admin(current_user):
        raw = request.args.get("team_leader_id")
        if raw:
            try:
                candidate = int(raw)
                if get_agent_by_id(candidate, organization_id):
                    selected_id = candidate
            except (TypeError, ValueError):
                pass

    from modules.team_reports import load_team_report

    report = load_team_report(
        organization_id,
        selected_id,
        request.args,
        language=get_current_language(),
    )

    if report is None:
        abort(404)

    return report


@app.route("/reports/team")
@app.route("/reports/team/<int:team_leader_id>")
@login_required
def team_report(team_leader_id=None):
    if is_guest_session():
        abort(403)

    organization_id = require_user_organization()
    current_user = get_current_user()

    if team_leader_id is None:
        if is_admin(current_user):
            from modules.team_reports import list_team_leaders

            leaders = list_team_leaders(organization_id)
            if not leaders:
                flash_i18n("team_no_leaders", "error")
                return redirect(url_for("agents_list"))
            return redirect(
                url_for(
                    "team_report",
                    team_leader_id=leaders[0]["id"],
                )
            )

        linked = current_user.get("agent_id")
        if linked is None:
            abort(403)
        return redirect(
            url_for("team_report", team_leader_id=linked)
        )

    report = _load_team_report_for_request(team_leader_id)
    return render_template(
        "reports/team.html",
        report=report,
    )


@app.route("/reports/team/<int:team_leader_id>/pdf")
@login_required
def team_report_pdf(team_leader_id):
    report = _load_team_report_for_request(team_leader_id)
    from modules.pdf_team_report import build_team_report_pdf

    pdf_bytes = build_team_report_pdf(report)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{report['download_basename']}.pdf",
    )


@app.route("/reports/team/<int:team_leader_id>/xlsx")
@login_required
def team_report_xlsx(team_leader_id):
    report = _load_team_report_for_request(team_leader_id)
    from modules.excel_team_report import build_team_report_xlsx

    xlsx_bytes = build_team_report_xlsx(report)
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
        as_attachment=True,
        download_name=f"{report['download_basename']}.xlsx",
    )


@app.route(
    "/agents/<int:agent_id>/delete",
    methods=["POST"]
)
@admin_required
def agents_delete(agent_id):
    organization_id = require_user_organization()

    ensure_agent_scope(
        agent_id,
        organization_id
    )

    agent = get_agent_by_id(
        agent_id,
        organization_id
    )

    if agent is None:
        abort(404)

    confirm_delete = request.form.get(
        "confirm_delete"
    )

    if confirm_delete != "yes":
        flash_i18n("deletion_cancelled", "error")

        return redirect(
            url_for(
                "agents_edit",
                agent_id=agent_id
            )
        )

    try:
        delete_agent(
            agent_id,
            organization_id
        )

    except IntegrityError:
        flash_i18n("agent_delete_blocked", "error")

        return redirect(
            url_for(
                "agents_edit",
                agent_id=agent_id
            )
        )

    flash_i18n("agent_deleted", "success")

    return redirect(
        url_for("agents_list")
    )


@app.route("/properties")
@login_required
def properties_list():
    organization_id = require_user_organization()

    filters = {
        "property_id": request.args.get(
            "property_id",
            ""
        ).strip(),
        "address": request.args.get(
            "address",
            ""
        ).strip(),
        "jurisdiction": request.args.get(
            "jurisdiction",
            ""
        ).strip(),
        "agent_id": request.args.get(
            "agent_id",
            ""
        ).strip(),
        "min_price": request.args.get(
            "min_price",
            ""
        ).strip(),
        "max_price": request.args.get(
            "max_price",
            ""
        ).strip()
    }

    agent_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        flash_i18n("agent_scope_missing", "error")

        filter_errors = []
        properties = []
        filter_agents = []
    else:
        include_all_statuses = (
            is_admin()
            or is_agent()
        )
        filter_errors, properties = (
            get_filtered_properties(
                filters,
                organization_id,
                agent_id=agent_id,
                include_all_statuses=include_all_statuses
            )
        )
        all_agents = get_agents(organization_id)

        if agent_id is not None:
            filter_agents = [
                agent
                for agent in all_agents
                if agent["id"] == agent_id
            ]
        else:
            filter_agents = all_agents

    _, parsed_filters = validate_property_filters(
        filters,
        organization_id=organization_id,
    )

    property_count = len(properties)
    per_page = parse_per_page(
        request.args.get("per_page"),
        default=DEFAULT_PER_PAGE,
    )
    pagination = paginate_list(
        properties,
        page=request.args.get("page", 1),
        per_page=per_page,
    )
    pagination_params = {
        key: value
        for key, value in filters.items()
        if value
    }
    if per_page != DEFAULT_PER_PAGE:
        pagination_params["per_page"] = per_page

    return render_template(
        "properties/list.html",
        properties=pagination["items"],
        filters=filters,
        filter_errors=localize_form_errors(filter_errors),
        property_count=property_count,
        jurisdictions=JURISDICTIONS,
        agents=filter_agents if not scope_blocked else [],
        filters_active=has_active_property_filters(
            parsed_filters
        ),
        pagination=pagination,
        pagination_endpoint="properties_list",
        pagination_params=pagination_params,
        pagination_summary_key="pagination_showing_properties",
        pagination_per_page_options=ALLOWED_PER_PAGE,
        show_per_page_selector=True,
    )


@app.route(
    "/properties/new",
    methods=[
        "GET",
        "POST"
    ]
)
@write_required
def properties_new():
    organization_id = require_user_organization()
    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        abort(403)

    agents = get_agents(organization_id)

    if request.method == "POST":
        fields = _property_form_fields_from_request()

        owner_agent_id, forced = resolve_owned_agent_id(
            fields["agent_id"]
        )

        errors = validate_property_form(
            fields["address"],
            fields["jurisdiction"],
            fields["property_type"],
            listing_price=fields["listing_price_raw"],
            listing_purpose=fields["listing_purpose"],
        )

        if owner_agent_id is None:
            errors.append("err_property_agent_required")
        elif (
            not forced
            and get_agent_by_id(
                owner_agent_id,
                organization_id
            ) is None
        ):
            errors.append("err_invalid_agent")

        if errors:
            return render_template(
                "properties/form.html",
                form_title="New Property",
                submit_label="Create Property",
                property_data=_property_form_data_from_fields(
                    fields,
                    owner_agent_id,
                ),
                jurisdictions=JURISDICTIONS,
                agents=agents,
                lock_agent_selection=forced,
                errors=localize_form_errors(errors),
                is_edit=False,
                **_property_form_context({}),
            )

        property_status = (
            PROPERTY_STATUS_APPROVED
            if is_admin()
            else PROPERTY_STATUS_PENDING
        )

        add_property(
            fields["address"],
            fields["jurisdiction"],
            organization_id,
            agent_id=owner_agent_id,
            status=property_status,
            created_by_user_id=get_current_user()["id"],
            property_type=fields["property_type"],
            listing_price=fields["listing_price"],
            listing_purpose=fields["listing_purpose"],
        )

        if property_status == PROPERTY_STATUS_PENDING:
            flash_i18n("property_submitted_for_approval", "success")
        else:
            flash_i18n("property_added", "success")

        return redirect(
            url_for("properties_list")
        )

    default_agent_id = scoped_id or ""

    return render_template(
        "properties/form.html",
        form_title="New Property",
        submit_label="Create Property",
        property_data={
            "address": "",
            "jurisdiction": "",
            "agent_id": default_agent_id,
            "property_type": "",
            "listing_price": "",
            "listing_purpose": "",
        },
        jurisdictions=JURISDICTIONS,
        agents=agents,
        lock_agent_selection=scoped_id is not None,
        errors=[],
        is_edit=False,
        **_property_form_context({}),
    )


@app.route("/properties/<int:property_id>")
@login_required
def properties_detail(property_id):
    organization_id = require_user_organization()

    property_data = ensure_property_view_scope(
        property_id,
        organization_id
    )

    language = get_current_language()
    agent_id, _scope_blocked = get_agent_scope()

    listings = load_property_listings_for_property(
        property_data,
        language=language,
    )

    related_operations = list_operations_for_property(
        property_data["id"],
        property_data["organization_id"],
        agent_id=agent_id,
    )

    pending_change = get_pending_change_for_property(
        property_data["id"],
        property_data["organization_id"],
    )

    can_manage_listings = (
        get_guest_access() is None
        and can_write(get_current_user())
    )

    return render_template(
        "properties/detail.html",
        property_data=property_data,
        property_display_id=format_property_display_id(
            property_data["id"]
        ),
        listings=listings,
        related_operations=related_operations,
        pending_change=pending_change,
        can_manage_listings=can_manage_listings,
        can_edit_property=(
            get_guest_access() is None
            and can_write(get_current_user())
        ),
    )


@app.route(
    "/properties/<int:property_id>/listings/new",
    methods=[
        "GET",
        "POST",
    ],
)
@write_required
def property_listings_new(property_id):
    organization_id = require_user_organization()

    property_data = ensure_property_scope(
        property_id,
        organization_id
    )

    if request.method == "POST":
        form_data = {
            "provider": request.form.get("provider", ""),
            "url": request.form.get("url", ""),
            "status": request.form.get("status", ""),
            "external_id": request.form.get(
                "external_id",
                "",
            ),
            "provider_label": request.form.get(
                "provider_label",
                "",
            ),
        }

        errors, _listing, parsed, listing_conflict = (
            save_new_listing(
                organization_id,
                property_id,
                form_data,
                user_id=get_current_user()["id"],
                language=get_current_language(),
            )
        )

        if errors:
            return render_template(
                "properties/listing_form.html",
                **_listing_form_template_context(
                    property_data,
                    listing_data=parsed,
                    errors=errors,
                    is_edit=False,
                    listing_conflict=listing_conflict,
                )
            )

        flash_i18n("listing_created", "success")

        return redirect(
            url_for(
                "properties_detail",
                property_id=property_id,
            )
        )

    return render_template(
        "properties/listing_form.html",
        **_listing_form_template_context(
            property_data,
            is_edit=False,
        )
    )


@app.route(
    "/properties/<int:property_id>/listings/"
    "<int:listing_id>/edit",
    methods=[
        "GET",
        "POST",
    ],
)
@write_required
def property_listings_edit(property_id, listing_id):
    organization_id = require_user_organization()

    property_data = ensure_property_scope(
        property_id,
        organization_id
    )

    listing = get_listing_record(
        listing_id,
        organization_id
    )

    if (
        listing is None
        or listing["property_id"] != property_id
    ):
        abort(404)

    if request.method == "POST":
        form_data = {
            "provider": request.form.get("provider", ""),
            "url": request.form.get("url", ""),
            "status": request.form.get("status", ""),
            "external_id": request.form.get(
                "external_id",
                "",
            ),
            "provider_label": request.form.get(
                "provider_label",
                "",
            ),
        }

        errors, _listing, parsed, listing_conflict = (
            save_existing_listing(
                listing_id,
                organization_id,
                form_data,
                user_id=get_current_user()["id"],
                language=get_current_language(),
            )
        )

        if errors:
            return render_template(
                "properties/listing_form.html",
                **_listing_form_template_context(
                    property_data,
                    listing_data=parsed,
                    errors=errors,
                    is_edit=True,
                    listing_conflict=listing_conflict,
                )
            )

        flash_i18n("listing_updated", "success")

        return redirect(
            url_for(
                "properties_detail",
                property_id=property_id,
            )
        )

    listing_form = {
        "provider": listing["provider"],
        "url": listing["url"],
        "status": listing["status"],
        "external_id": listing.get("external_id") or "",
        "provider_label": listing.get("provider_label") or "",
    }

    return render_template(
        "properties/listing_form.html",
        **_listing_form_template_context(
            property_data,
            listing_data=listing_form,
            is_edit=True,
        ),
        listing_id=listing_id,
    )


@app.route(
    "/properties/<int:property_id>/listings/"
    "<int:listing_id>/delete",
    methods=["POST"],
)
@write_required
def property_listings_delete(property_id, listing_id):
    organization_id = require_user_organization()

    ensure_property_scope(
        property_id,
        organization_id
    )

    listing = get_listing_record(
        listing_id,
        organization_id
    )

    if (
        listing is None
        or listing["property_id"] != property_id
    ):
        abort(404)

    if remove_listing(listing_id, organization_id):
        flash_i18n("listing_deleted", "success")
    else:
        flash_i18n("listing_not_found", "error")

    return redirect(
        url_for(
            "properties_detail",
            property_id=property_id,
        )
    )


@app.route(
    "/properties/<int:property_id>/edit",
    methods=[
        "GET",
        "POST"
    ]
)
@write_required
def properties_edit(property_id):
    organization_id = require_user_organization()

    property_data = ensure_property_scope(
        property_id,
        organization_id
    )

    scoped_id, _scope_blocked = get_agent_scope()
    agents = get_agents(organization_id)
    forced_owner = scoped_id is not None

    if request.method == "POST":
        fields = _property_form_fields_from_request()

        if forced_owner:
            owner_agent_id = scoped_id
        else:
            owner_agent_id, _forced = (
                resolve_owned_agent_id(
                    fields["agent_id"]
                )
            )

        errors = validate_property_form(
            fields["address"],
            fields["jurisdiction"],
            fields["property_type"],
            listing_price=fields["listing_price_raw"],
            listing_purpose=fields["listing_purpose"],
        )

        if owner_agent_id is None:
            errors.append("err_property_agent_required")
        elif (
            not forced_owner
            and get_agent_by_id(
                owner_agent_id,
                organization_id
            ) is None
        ):
            errors.append("err_invalid_agent")

        if errors:
            property_data.update(
                _property_form_data_from_fields(
                    fields,
                    owner_agent_id,
                )
            )

            return render_template(
                "properties/form.html",
                form_title="Edit Property",
                submit_label="Save Changes",
                property_data=property_data,
                jurisdictions=JURISDICTIONS,
                agents=agents,
                lock_agent_selection=forced_owner,
                errors=localize_form_errors(errors),
                is_edit=True,
                **_property_form_context(property_data),
            )

        current_user = get_current_user()

        if is_admin(current_user):
            update_property(
                property_id,
                fields["address"],
                fields["jurisdiction"],
                organization_id,
                agent_id=owner_agent_id,
                property_type=fields["property_type"],
                listing_price=fields["listing_price"],
                listing_purpose=fields["listing_purpose"],
            )
            flash_i18n("property_updated", "success")
        elif property_is_official(
            property_data.get("status")
        ):
            if get_pending_change_for_property(
                property_id,
                organization_id
            ) is not None:
                errors.append("err_property_change_pending")

            if errors:
                property_data.update(
                    _property_form_data_from_fields(
                        fields,
                        owner_agent_id,
                    )
                )

                return render_template(
                    "properties/form.html",
                    form_title="Edit Property",
                    submit_label="Save Changes",
                    property_data=property_data,
                    jurisdictions=JURISDICTIONS,
                    agents=agents,
                    lock_agent_selection=forced_owner,
                    errors=localize_form_errors(errors),
                    is_edit=True,
                    **_property_form_context(property_data),
                )

            create_property_change_request(
                property_id,
                organization_id,
                current_user["id"],
                fields["address"],
                fields["jurisdiction"],
                owner_agent_id,
                proposed_property_type=fields["property_type"],
                proposed_listing_price=fields["listing_price"],
                proposed_listing_purpose=fields["listing_purpose"],
            )
            flash_i18n("property_change_submitted", "success")
        else:
            update_property(
                property_id,
                fields["address"],
                fields["jurisdiction"],
                organization_id,
                agent_id=owner_agent_id,
                property_type=fields["property_type"],
                listing_price=fields["listing_price"],
                listing_purpose=fields["listing_purpose"],
            )

            if property_data.get("status") == PROPERTY_STATUS_REJECTED:
                update_property_status(
                    property_id,
                    organization_id,
                    PROPERTY_STATUS_PENDING,
                    rejection_reason=None
                )

            flash_i18n("property_updated", "success")

        return redirect(
            url_for("properties_list")
        )

    return render_template(
        "properties/form.html",
        form_title="Edit Property",
        submit_label="Save Changes",
        property_data=property_data,
        jurisdictions=JURISDICTIONS,
        agents=agents,
        lock_agent_selection=forced_owner,
        errors=[],
        is_edit=True,
        **_property_form_context(property_data),
    )


@app.route(
    "/properties/<int:property_id>/delete",
    methods=["POST"]
)
@admin_required
def properties_delete(property_id):
    organization_id = require_user_organization()

    property_data = ensure_property_scope(
        property_id,
        organization_id
    )

    if property_data is None:
        abort(404)

    confirm_delete = request.form.get(
        "confirm_delete"
    )

    if confirm_delete != "yes":
        flash_i18n("deletion_cancelled", "error")

        return redirect(
            url_for(
                "properties_edit",
                property_id=property_id
            )
        )

    try:
        delete_property(
            property_id,
            organization_id
        )

    except IntegrityError:
        flash_i18n("property_delete_blocked", "error")

        return redirect(
            url_for(
                "properties_edit",
                property_id=property_id
            )
        )

    flash_i18n("property_deleted", "success")

    return redirect(
        url_for("properties_list")
    )


@app.route("/operations")
@login_required
def operations_list():
    organization_id = require_user_organization()

    filters = {
        "operation_id": request.args.get(
            "operation_id",
            ""
        ).strip(),
        "agent_id": request.args.get(
            "agent_id",
            ""
        ).strip(),
        "property": request.args.get(
            "property",
            ""
        ).strip(),
        "min_amount": request.args.get(
            "min_amount",
            ""
        ).strip(),
        "max_amount": request.args.get(
            "max_amount",
            ""
        ).strip(),
        "date_from": request.args.get(
            "date_from",
            ""
        ).strip(),
        "date_to": request.args.get(
            "date_to",
            ""
        ).strip(),
        "was_invoiced": request.args.get(
            "was_invoiced",
            ""
        ).strip(),
        "jurisdiction": request.args.get(
            "jurisdiction",
            ""
        ).strip(),
        "status": request.args.get(
            "status",
            ""
        ).strip()
    }

    agent_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        flash_i18n("agent_scope_missing", "error")

        filter_errors = []
        operations = []
        filter_agents = []
    else:
        filter_errors, operations = (
            get_filtered_operations(
                filters,
                organization_id,
                agent_id=agent_id
            )
        )
        all_agents = get_agents(organization_id)

        if agent_id is not None:
            filter_agents = [
                agent
                for agent in all_agents
                if agent["id"] == agent_id
            ]
        else:
            filter_agents = all_agents

    _, parsed_filters = validate_operation_filters(
        filters,
        organization_id=organization_id,
    )

    operation_count = len(operations)
    per_page = parse_per_page(
        request.args.get("per_page"),
        default=DEFAULT_PER_PAGE,
    )
    pagination = paginate_list(
        operations,
        page=request.args.get("page", 1),
        per_page=per_page,
    )
    pagination_params = {
        key: value
        for key, value in filters.items()
        if value
    }
    if per_page != DEFAULT_PER_PAGE:
        pagination_params["per_page"] = per_page

    return render_template(
        "operations/list.html",
        operations=pagination["items"],
        filters=filters,
        filter_errors=localize_form_errors(filter_errors),
        operation_count=operation_count,
        can_create_operations=can_create_operations(
            organization_id
        ),
        jurisdictions=JURISDICTIONS,
        operation_statuses=OPERATION_STATUSES,
        agents=filter_agents if not scope_blocked else [],
        filters_active=has_active_operation_filters(
            parsed_filters
        ),
        pagination=pagination,
        pagination_endpoint="operations_list",
        pagination_params=pagination_params,
        pagination_summary_key="pagination_showing_operations",
        pagination_per_page_options=ALLOWED_PER_PAGE,
        show_per_page_selector=True,
    )


@app.route(
    "/operations/<int:operation_id>"
)
@login_required
def operations_detail(operation_id):
    organization_id = require_user_organization()

    if get_guest_access() is not None:
        # Guests can view operations read-only but not docs UI actions.
        pass

    operation = get_operation_record(
        operation_id,
        organization_id
    )

    if operation is None:
        abort(404)

    ensure_operation_scope(
        operation,
        organization_id
    )

    documents = []
    document_categories = []
    can_manage_documents = (
        get_guest_access() is None
        and (
            is_admin()
            or is_agent()
        )
    )

    if can_manage_documents:
        documents = list_operation_documents(
            organization_id,
            operation_id
        )
        uploader_cache = {}

        for document in documents:
            user_id = document.get("uploaded_by_user_id")

            if user_id is None:
                document["uploaded_by_name"] = None
                continue

            if user_id not in uploader_cache:
                uploader_cache[user_id] = get_user_by_id(
                    user_id
                )

            user = uploader_cache[user_id]
            document["uploaded_by_name"] = (
                (
                    f"{(user.get('first_name') or '').strip()} "
                    f"{(user.get('last_name') or '').strip()}"
                ).strip()
                or user.get("username")
                or user.get("email")
            ) if user else None

        document_categories = group_documents_for_ui(
            documents
        )

    language = get_current_language()
    commission_lines = build_commission_lines(
        operation,
        language,
    )
    billing_lines = build_billing_lines(
        operation,
        language,
    )
    sides_state = get_operation_sides_state(
        operation,
        organization_id,
        user=get_current_user(),
    )
    total_commission = 0.0
    for side_info in (sides_state or {}).values():
        party = (side_info or {}).get("party") or {}
        if party.get("is_participating") and party.get(
            "commission_amount"
        ) is not None:
            try:
                total_commission += float(
                    party["commission_amount"]
                )
            except (TypeError, ValueError):
                pass
    can_set_invoice_amount = (
        get_guest_access() is None
        and is_admin()
    )
    can_create_invoice = (
        get_guest_access() is None
        and (
            is_admin()
            or is_agent()
        )
    )

    show_readiness = (
        is_agent()
        and agent_can_submit_status(
            operation.get("status")
        )
    )
    operation_readiness = (
        validate_operation_readiness(
            operation_id,
            organization_id,
        )
        if show_readiness
        else None
    )

    return render_template(
        "operations/detail.html",
        operation=operation,
        can_edit_operation=(
            is_admin()
            or (
                is_agent()
                and agent_can_edit_status(
                    operation.get("status")
                )
            )
        ),
        can_submit_operation=(
            show_readiness
            and operation_readiness is not None
            and operation_readiness["is_ready"]
        ),
        show_readiness_checklist=show_readiness,
        operation_readiness=operation_readiness,
        can_review_operation=(
            can_approve()
            and operation.get("status") == STATUS_PENDING
        ),
        can_manage_documents=can_manage_documents,
        document_categories=document_categories,
        commission_lines=commission_lines,
        billing_lines=billing_lines,
        sides_state=sides_state,
        total_commission=total_commission,
        can_set_invoice_amount=can_set_invoice_amount,
        can_create_invoice=can_create_invoice,
        tax_conditions=TAX_CONDITIONS,
    )


def _require_documents_user():
    if get_guest_access() is not None:
        abort(403)

    user = get_current_user()

    if user is None:
        abort(403)

    if not (is_admin(user) or is_agent(user)):
        abort(403)

    return user


def require_billing_user():
    if get_guest_access() is not None:
        abort(403)

    user = get_current_user()

    if user is None:
        abort(403)

    return user


def _flash_invoicing_error(
    error,
    *,
    operation_id=None,
    side=None,
    user=None,
):
    from modules.billing_errors import (
        resolve_billing_error_cta,
        store_billing_error_cta,
    )

    flash_i18n(error.message_key, "error")
    for missing_key in error.missing or []:
        if (
            isinstance(missing_key, str)
            and missing_key.startswith("billing_missing_")
        ):
            flash_i18n(missing_key, "error")
    store_billing_error_cta(
        resolve_billing_error_cta(
            error,
            user=user,
            operation_id=operation_id,
            side=side,
        )
    )


def _load_billing_invoice(organization_id, invoice_id, user):
    try:
        invoice = get_invoice(organization_id, invoice_id)
        if invoice is None:
            abort(404)
        if is_admin(user):
            return invoice
        if is_agent(user) and user.get("agent_id") == invoice.get(
            "agent_id"
        ):
            return invoice
        abort(403)
    except InvoicingError as error:
        if error.message_key == "invoice_err_not_found":
            abort(404)
        if error.message_key == "invoice_err_forbidden":
            abort(403)
        _flash_invoicing_error(error)
        abort(403)


def _load_scoped_operation(operation_id, organization_id):
    operation = get_operation_record(
        operation_id,
        organization_id
    )

    if operation is None:
        abort(404)

    ensure_operation_scope(
        operation,
        organization_id
    )

    return operation


def _can_see_summary_documents():
    if get_guest_access() is not None:
        return False

    user = get_current_user()

    if user is None:
        return False

    return is_admin(user) or is_agent(user)


def _load_operation_summary_for_request(operation_id):
    organization_id = require_user_organization()
    operation = _load_scoped_operation(
        operation_id,
        organization_id
    )
    summary = load_operation_summary(
        operation,
        language=get_current_language(),
        can_see_documents=_can_see_summary_documents(),
    )
    return operation, summary


@app.route("/operations/<int:operation_id>/summary")
@login_required
def operations_summary(operation_id):
    operation, summary = _load_operation_summary_for_request(
        operation_id
    )

    return render_template(
        "operations/summary.html",
        operation=operation,
        summary=summary,
    )


@app.route("/operations/<int:operation_id>/summary/pdf")
@login_required
def operations_summary_pdf(operation_id):
    operation, summary = _load_operation_summary_for_request(
        operation_id
    )
    pdf_bytes = build_operation_summary_pdf(summary)
    filename = f"{summary['download_basename']}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/operations/<int:operation_id>/summary/xlsx")
@login_required
def operations_summary_xlsx(operation_id):
    operation, summary = _load_operation_summary_for_request(
        operation_id
    )
    xlsx_bytes = build_operation_summary_xlsx(summary)
    filename = f"{summary['download_basename']}.xlsx"

    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
        as_attachment=True,
        download_name=filename,
    )


@app.route(
    "/operations/<int:operation_id>/documents/<doc_type>",
    methods=["POST"]
)
@app.route(
    "/operations/<int:operation_id>/vat-documents/<doc_type>",
    methods=["POST"]
)
@login_required
@write_required
def operations_document_upload(operation_id, doc_type):
    user = _require_documents_user()
    organization_id = require_user_organization()
    _load_scoped_operation(operation_id, organization_id)

    if not is_valid_doc_type(doc_type):
        flash_i18n("err_vat_doc_type_invalid", "error")
        return redirect(
            url_for(
                "operations_detail",
                operation_id=operation_id
            )
        )

    document, error_key = upload_or_replace_operation_document(
        organization_id=organization_id,
        operation_id=operation_id,
        doc_type=doc_type,
        file_storage=request.files.get("document"),
        uploaded_by_user_id=user["id"]
    )

    if error_key is not None:
        flash_i18n(error_key, "error")
    else:
        flash_i18n("vat_doc_saved", "success")

    next_url = request.form.get("next", "").strip()

    if next_url.startswith("/"):
        return redirect(next_url)

    return redirect(
        url_for(
            "operations_detail",
            operation_id=operation_id
        )
    )


@app.route("/documents/<int:document_id>")
@app.route("/vat-documents/<int:document_id>")
@login_required
def operation_document_download(document_id):
    _require_documents_user()
    organization_id = require_user_organization()

    document = get_operation_document(
        document_id,
        organization_id
    )

    if document is None:
        abort(404)

    _load_scoped_operation(
        document["operation_id"],
        organization_id
    )

    absolute_path = absolute_document_path(document)

    if not absolute_path.is_file():
        abort(404)

    as_attachment = request.args.get("download") == "1"
    file_bytes = absolute_path.read_bytes()

    return send_file(
        io.BytesIO(file_bytes),
        mimetype=document["content_type"],
        as_attachment=as_attachment,
        download_name=document["original_filename"]
    )


@app.route(
    "/documents/<int:document_id>/delete",
    methods=["POST"]
)
@app.route(
    "/vat-documents/<int:document_id>/delete",
    methods=["POST"]
)
@login_required
@write_required
def operation_document_delete(document_id):
    _require_documents_user()
    organization_id = require_user_organization()

    document = get_operation_document(
        document_id,
        organization_id
    )

    if document is None:
        abort(404)

    operation_id = document["operation_id"]
    _load_scoped_operation(operation_id, organization_id)

    removed = remove_operation_document(
        document_id,
        organization_id
    )

    if removed is None:
        flash_i18n("err_vat_doc_not_found", "error")
    else:
        flash_i18n("vat_doc_deleted", "success")

    next_url = request.form.get("next", "").strip()

    if next_url.startswith("/"):
        return redirect(next_url)

    return redirect(
        url_for(
            "operations_detail",
            operation_id=operation_id
        )
    )


@app.route(
    "/vat-calculator",
    methods=[
        "GET",
        "POST"
    ]
)
@login_required
def vat_calculator():
    organization_id = require_user_organization()
    form_values = empty_vat_form_values()
    result = None
    errors = []
    source_operation = None

    operation_id_raw = request.values.get(
        "operation_id",
        ""
    ).strip()

    if operation_id_raw != "":
        try:
            operation_id = int(operation_id_raw)
        except ValueError:
            operation_id = None

        if operation_id is not None:
            operation = get_operation_record(
                operation_id,
                organization_id
            )

            if operation is not None:
                if (
                    operation["organization_id"]
                    == organization_id
                ):
                    scoped_id, scope_blocked = (
                        get_agent_scope()
                    )
                    agent_ok = (
                        not scope_blocked
                        and (
                            scoped_id is None
                            or operation["agent_db_id"]
                            == scoped_id
                        )
                    )

                    if agent_ok:
                        source_operation = operation

                        if request.method == "GET":
                            form_values = (
                                vat_form_values_from_operation(
                                    operation
                                )
                            )

    if request.method == "POST":
        form_values = {
            "operation_amount": request.form.get(
                "operation_amount",
                ""
            ),
            "buyer_rate": request.form.get(
                "buyer_rate",
                ""
            ),
            "seller_rate": request.form.get(
                "seller_rate",
                ""
            ),
            "tip": request.form.get(
                "tip",
                "buyer"
            ),
            "mode": request.form.get(
                "mode",
                "minimum_vat"
            ),
            "exchange_rate": request.form.get(
                "exchange_rate",
                ""
            ),
            "vat_usd": request.form.get(
                "vat_usd",
                ""
            ),
            "operation_id": request.form.get(
                "operation_id",
                operation_id_raw
            ),
        }

        parsed, error_keys = parse_calculator_inputs(
            form_values
        )

        if error_keys:
            errors = localize_form_errors(error_keys)
        else:
            result = build_calculator_result(parsed)

            if str(form_values.get("vat_usd", "")).strip() == "":
                form_values["vat_usd"] = str(
                    result["vat_usd"]
                )

    document_categories = []
    can_manage_documents = False

    if (
        source_operation is not None
        and get_guest_access() is None
        and (is_admin() or is_agent())
    ):
        can_manage_documents = True
        docs = list_operation_documents(
            organization_id,
            source_operation["db_id"]
        )
        document_categories = group_documents_for_ui(docs)

    return render_template(
        "tools/vat_calculator.html",
        form_values=form_values,
        result=result,
        errors=errors,
        source_operation=source_operation,
        can_manage_documents=can_manage_documents,
        document_categories=document_categories,
    )


@app.route(
    "/operations/new",
    methods=[
        "GET",
        "POST"
    ]
)
@write_required
def operations_new():
    organization_id = require_user_organization()
    current_user = get_current_user()

    if not can_create_operations(organization_id):
        flash_i18n("need_agent_property_ops", "error")
        return redirect(
            url_for("operations_list")
        )

    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        abort(403)

    if request.method == "POST":
        form_values = get_new_operation_form_values(
            request.form
        )

        if scoped_id is not None:
            form_values["agent_id"] = str(scoped_id)

        action = request.form.get(
            "action",
            "preview"
        )

        errors, operation, parsed = (
            process_new_operation_submission(
                form_values,
                organization_id
            )
        )

        if (
            scoped_id is not None
            and parsed is not None
            and parsed.get("agent_id") != scoped_id
        ):
            errors.append("access_denied")

        ownership_denied = (
            "Property does not belong to the selected agent."
            in errors
        )

        if ownership_denied:
            abort(403)

        if len(errors) > 0:
            return render_operation_form(
                "New Operation",
                "Save Draft",
                "Preview Calculation",
                form_values,
                errors,
                organization_id,
                is_edit=False
            )

        if action == "preview":
            language = get_current_language()
            return render_template(
                "operations/preview.html",
                operation=operation,
                form_values=form_values,
                parsed=parsed,
                is_edit=False,
                operation_id=None,
                commission_lines=build_commission_lines(
                    operation,
                    language,
                ),
                billing_lines=build_billing_lines(
                    operation,
                    language,
                ),
                back_url=url_for(
                    "operations_new"
                ),
                save_url=url_for(
                    "operations_new"
                )
            )

        if is_agent(current_user):
            status = STATUS_DRAFT
            require_owner = True
        else:
            status = STATUS_APPROVED
            require_owner = False

        try:
            save_calculated_operation(
                parsed["agent_id"],
                parsed["property_id"],
                organization_id,
                operation,
                status=status,
                created_by_user_id=current_user["id"],
                require_property_owner=require_owner
            )
        except TenantError:
            abort(403)
        except ValueError as error:
            if str(error) == "property_already_used_in_operation":
                return render_operation_form(
                    "New Operation",
                    "Save Draft" if scoped_id else "Save Operation",
                    "Preview Calculation",
                    form_values,
                    ["property_already_used_in_operation"],
                    organization_id,
                    is_edit=False
                )
            raise

        if status == STATUS_DRAFT:
            flash_i18n("operation_draft_saved", "success")
        else:
            flash_i18n("operation_saved", "success")

        return redirect(
            url_for("operations_list")
        )

    default_agent = str(scoped_id) if scoped_id else ""
    org_defaults = get_new_operation_form_defaults(
        organization_id
    )

    return render_operation_form(
        "New Operation",
        "Save Draft" if scoped_id else "Save Operation",
        "Preview Calculation",
        {
            "agent_id": default_agent,
            "property_id": "",
            "search_mode": "agent",
            "currency": org_defaults["currency"],
            "original_amount": "",
            "exchange_rate": "",
            "operation_date": date.today().strftime(
                "%d/%m/%Y"
            ),
            "seller_side_active": "1",
            "buyer_side_active": "1",
            "is_referred": "",
            "referred_side": "",
            "seller_commission_rate": org_defaults[
                "seller_commission_rate"
            ],
            "buyer_commission_rate": org_defaults[
                "buyer_commission_rate"
            ],
            "seller_vat_amount": "0",
            "buyer_vat_amount": "0",
        },
        [],
        organization_id,
        is_edit=False
    )


@app.route(
    "/operations/<int:operation_id>/edit",
    methods=[
        "GET",
        "POST"
    ]
)
@write_required
def operations_edit(operation_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    operation = get_operation_record(
        operation_id,
        organization_id
    )

    if operation is None:
        abort(404)

    ensure_operation_scope(
        operation,
        organization_id
    )
    ensure_operation_editable(operation)

    scoped_id, scope_blocked = get_agent_scope()

    if scope_blocked:
        abort(403)

    show_submit = (
        is_agent(current_user)
        and agent_can_submit_status(
            operation.get("status")
        )
    )

    if request.method == "POST":
        form_values = get_operation_form_values(
            request.form
        )

        if scoped_id is not None:
            form_values["agent_id"] = str(scoped_id)

        action = request.form.get(
            "action",
            "preview"
        )

        errors, calculated, parsed = (
            process_operation_submission(
                form_values,
                organization_id,
                operation_display_id=operation["id"]
            )
        )

        if (
            scoped_id is not None
            and parsed is not None
            and parsed.get("agent_id") != scoped_id
        ):
            errors.append("access_denied")

        ownership_denied = (
            "Property does not belong to the selected agent."
            in errors
        )

        if ownership_denied:
            abort(403)

        if len(errors) > 0:
            return render_operation_form(
                "Edit Operation",
                "Save Changes",
                "Preview Calculation",
                form_values,
                errors,
                organization_id,
                is_edit=True,
                operation_id=operation_id,
                show_submit_for_approval=show_submit
            )

        if action == "preview":
            language = get_current_language()
            return render_template(
                "operations/preview.html",
                operation=calculated,
                form_values=form_values,
                parsed=parsed,
                is_edit=True,
                operation_id=operation_id,
                commission_lines=build_commission_lines(
                    calculated,
                    language,
                ),
                billing_lines=build_billing_lines(
                    calculated,
                    language,
                ),
                back_url=url_for(
                    "operations_edit",
                    operation_id=operation_id
                ),
                save_url=url_for(
                    "operations_edit",
                    operation_id=operation_id
                )
            )

        next_status = operation.get("status")
        rejection_reason = operation.get(
            "rejection_reason"
        )
        submitting = (
            action == "submit"
            and is_agent(current_user)
            and agent_can_submit_status(next_status)
        )

        if submitting:
            next_status = STATUS_DRAFT
            rejection_reason = None
        elif is_agent(current_user):
            if next_status == STATUS_REJECTED:
                next_status = STATUS_DRAFT
            rejection_reason = None

        require_owner = is_agent(current_user)

        try:
            update_calculated_operation(
                operation_id,
                parsed["agent_id"],
                parsed["property_id"],
                organization_id,
                calculated,
                status=next_status,
                rejection_reason=rejection_reason,
                require_property_owner=require_owner
            )
        except TenantError:
            abort(403)

        if submitting:
            try:
                submit_operation_for_approval(
                    operation_id,
                    organization_id,
                    user=current_user,
                )
                flash_i18n("operation_submitted", "success")
            except OperationNotReadyError:
                flash_i18n(
                    "operation_not_ready_for_approval",
                    "error",
                )
            return redirect(
                url_for(
                    "operations_detail",
                    operation_id=operation_id,
                )
            )

        flash_i18n("operation_updated", "success")

        return redirect(
            url_for("operations_list")
        )

    form_values = operation_to_form_values(
        operation
    )

    operation_readiness = validate_operation_readiness(
        operation_id,
        organization_id,
    ) if show_submit else None

    return render_operation_form(
        "Edit Operation",
        "Save Changes",
        "Preview Calculation",
        form_values,
        [],
        organization_id,
        is_edit=True,
        operation_id=operation_id,
        show_submit_for_approval=(
            show_submit
            and operation_readiness is not None
            and operation_readiness["is_ready"]
        ),
        operation_readiness=operation_readiness,
    )


@app.route(
    "/operations/<int:operation_id>/submit",
    methods=["POST"]
)
@write_required
def operations_submit(operation_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    if not is_agent(current_user):
        abort(403)

    operation = get_operation_record(
        operation_id,
        organization_id
    )

    if operation is None:
        abort(404)

    ensure_operation_scope(
        operation,
        organization_id
    )

    if not agent_can_submit_status(
        operation.get("status")
    ):
        abort(403)

    try:
        submit_operation_for_approval(
            operation_id,
            organization_id,
            user=current_user,
        )
    except OperationNotReadyError:
        flash_i18n(
            "operation_not_ready_for_approval",
            "error",
        )
        return redirect(
            url_for(
                "operations_detail",
                operation_id=operation_id,
            )
        )

    flash_i18n("operation_submitted", "success")

    return redirect(
        url_for(
            "operations_detail",
            operation_id=operation_id
        )
    )


@app.route(
    "/operations/<int:operation_id>/approve",
    methods=["POST"]
)
@admin_required
def operations_approve(operation_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    operation = get_operation_record(
        operation_id,
        organization_id
    )

    if operation is None:
        abort(404)

    if operation["organization_id"] != organization_id:
        abort(403)

    if operation.get("status") != STATUS_PENDING:
        flash_i18n("operation_not_pending", "error")
        return redirect(
            url_for(
                "operations_detail",
                operation_id=operation_id
            )
        )

    change_operation_status(
        operation_id,
        organization_id,
        STATUS_APPROVED,
        reviewed_by_user_id=current_user["id"],
        reviewed_at=datetime.utcnow().isoformat(
            timespec="seconds"
        ),
        rejection_reason=None
    )

    notify_agent_for_operation(
        organization_id,
        operation["agent_db_id"],
        "operation_approved",
        operation_id,
        {
            "total_commission": operation.get("total_commission"),
            "currency": operation.get("currency", "USD"),
            "status": STATUS_APPROVED
        },
        actor_user_id=current_user["id"]
    )

    flash_i18n("operation_approved", "success")

    next_url = request.form.get("next")

    if next_url == "approvals":
        return redirect(url_for("approvals_list"))

    return redirect(
        url_for(
            "operations_detail",
            operation_id=operation_id
        )
    )


@app.route(
    "/operations/<int:operation_id>/reject",
    methods=["POST"]
)
@admin_required
def operations_reject(operation_id):
    organization_id = require_user_organization()
    current_user = get_current_user()

    operation = get_operation_record(
        operation_id,
        organization_id
    )

    if operation is None:
        abort(404)

    if operation["organization_id"] != organization_id:
        abort(403)

    if operation.get("status") != STATUS_PENDING:
        flash_i18n("operation_not_pending", "error")
        return redirect(
            url_for(
                "operations_detail",
                operation_id=operation_id
            )
        )

    reason = request.form.get(
        "rejection_reason",
        ""
    ).strip()

    if reason == "":
        flash_i18n("rejection_reason_required", "error")
        return redirect(
            url_for(
                "operations_detail",
                operation_id=operation_id
            )
        )

    change_operation_status(
        operation_id,
        organization_id,
        STATUS_REJECTED,
        reviewed_by_user_id=current_user["id"],
        reviewed_at=datetime.utcnow().isoformat(
            timespec="seconds"
        ),
        rejection_reason=reason
    )

    notify_agent_for_operation(
        organization_id,
        operation["agent_db_id"],
        "operation_rejected",
        operation_id,
        {
            "total_commission": operation.get("total_commission"),
            "currency": operation.get("currency", "USD"),
            "status": STATUS_REJECTED,
            "reason": reason
        },
        actor_user_id=current_user["id"]
    )

    flash_i18n("operation_rejected", "success")

    next_url = request.form.get("next")

    if next_url == "approvals":
        return redirect(url_for("approvals_list"))

    return redirect(
        url_for(
            "operations_detail",
            operation_id=operation_id
        )
    )


@app.route(
    "/operations/<int:operation_id>/delete",
    methods=["POST"]
)
@admin_required
def operations_delete(operation_id):
    organization_id = require_user_organization()

    operation = get_operation_record(
        operation_id,
        organization_id
    )

    if operation is None:
        abort(404)

    if operation["organization_id"] != organization_id:
        abort(403)

    confirm_delete = request.form.get(
        "confirm_delete"
    )

    if confirm_delete != "yes":
        flash_i18n("deletion_cancelled", "error")

        return redirect(
            url_for(
                "operations_edit",
                operation_id=operation_id
            )
        )

    remove_operation(
        operation_id,
        organization_id
    )

    flash_i18n("operation_deleted", "success")

    return redirect(
        url_for("operations_list")
    )


def _cash_form_context(form_values=None, errors=None, preview=None):
    form_values = form_values or {
        "movement_type": TYPE_INCOME,
        "currency": "ARS",
        "amount": "",
        "category": "",
        "description": "",
        "payment_method": "cash",
        "movement_date": date.today().isoformat(),
        "notes": "",
    }

    return {
        "form_values": form_values,
        "errors": errors or [],
        "preview": preview,
        "currencies": CASH_CURRENCIES,
        "payment_methods": CASH_PAYMENT_METHODS,
        "income_categories": INCOME_CATEGORIES,
        "expense_categories": EXPENSE_CATEGORIES,
        "categories": categories_for_type(
            form_values.get("movement_type") or TYPE_INCOME
        ),
    }


@app.route("/cash")
@admin_required
def cash_list():
    organization_id = require_user_organization()
    filters = {
        "q": request.args.get("q", "").strip(),
        "currency": request.args.get(
            "currency",
            "",
        ).strip().upper(),
        "movement_type": request.args.get(
            "movement_type",
            "",
        ).strip(),
        "category": request.args.get(
            "category",
            "",
        ).strip(),
        "payment_method": request.args.get(
            "payment_method",
            "",
        ).strip(),
        "user_id": request.args.get(
            "user_id",
            "",
        ).strip(),
        "date_from": request.args.get(
            "date_from",
            "",
        ).strip(),
        "date_to": request.args.get(
            "date_to",
            "",
        ).strip(),
        "status": request.args.get(
            "status",
            "",
        ).strip(),
    }

    filter_args = dict(filters)

    if filter_args.get("user_id"):
        try:
            filter_args["user_id"] = int(
                filter_args["user_id"]
            )
        except ValueError:
            filter_args["user_id"] = None
    else:
        filter_args["user_id"] = None

    if filter_args.get("currency") not in CASH_CURRENCIES:
        filter_args["currency"] = None

    movements = filter_movements(
        organization_id,
        filter_args,
    )
    kpis = build_cash_kpis(organization_id)
    per_page = parse_per_page(
        request.args.get("per_page"),
        default=DEFAULT_CASH_PER_PAGE,
        allowed=CASH_PER_PAGE_OPTIONS,
    )
    pagination = paginate_list(
        movements,
        page=request.args.get("page", 1),
        per_page=per_page,
    )
    pagination_params = {
        key: value
        for key, value in filters.items()
        if value
    }

    if per_page != DEFAULT_CASH_PER_PAGE:
        pagination_params["per_page"] = per_page

    filters_active = any(filters.values())

    return render_template(
        "cash/list.html",
        movements=pagination["items"],
        filters=filters,
        kpis=kpis,
        movement_count=len(movements),
        currencies=CASH_CURRENCIES,
        payment_methods=CASH_PAYMENT_METHODS,
        income_categories=INCOME_CATEGORIES,
        expense_categories=EXPENSE_CATEGORIES,
        users=get_users(organization_id),
        filters_active=filters_active,
        pagination=pagination,
        pagination_endpoint="cash_list",
        pagination_params=pagination_params,
        pagination_summary_key="pagination_showing_cash",
        pagination_per_page_options=CASH_PER_PAGE_OPTIONS,
        show_per_page_selector=True,
    )


@app.route(
    "/cash/new",
    methods=["GET", "POST"],
)
@admin_required
def cash_new():
    organization_id = require_user_organization()
    current_user = get_current_user()

    if request.method == "GET":
        return render_template(
            "cash/form.html",
            **_cash_form_context(),
        )

    form_values = {
        "movement_type": request.form.get(
            "movement_type",
            "",
        ).strip(),
        "currency": request.form.get(
            "currency",
            "",
        ).strip().upper(),
        "amount": request.form.get("amount", "").strip(),
        "category": request.form.get(
            "category",
            "",
        ).strip(),
        "description": request.form.get(
            "description",
            "",
        ).strip(),
        "payment_method": request.form.get(
            "payment_method",
            "",
        ).strip(),
        "movement_date": request.form.get(
            "movement_date",
            "",
        ).strip(),
        "notes": request.form.get("notes", "").strip(),
    }
    action = request.form.get("action", "preview")
    errors, values = validate_movement_payload(form_values)

    if errors:
        return render_template(
            "cash/form.html",
            **_cash_form_context(
                form_values,
                localize_form_errors(errors),
            ),
        )

    try:
        preview = preview_movement(
            organization_id,
            values,
        )
    except CashTreasuryError as error:
        return render_template(
            "cash/form.html",
            **_cash_form_context(
                form_values,
                localize_form_errors(
                    [error.message_key]
                ),
            ),
        )

    if action == "preview":
        return render_template(
            "cash/form.html",
            **_cash_form_context(
                form_values,
                preview=preview,
            ),
        )

    try:
        movement = confirm_movement(
            organization_id,
            values,
            user_id=current_user["id"],
        )
    except CashTreasuryError as error:
        return render_template(
            "cash/form.html",
            **_cash_form_context(
                form_values,
                localize_form_errors(
                    [error.message_key]
                ),
                preview=preview,
            ),
        )

    flash_i18n("cash_saved", "success")

    return redirect(
        url_for(
            "cash_detail",
            movement_id=movement["id"],
        )
    )


@app.route("/cash/<int:movement_id>")
@admin_required
def cash_detail(movement_id):
    organization_id = require_user_organization()
    movement = get_cash_movement(
        movement_id,
        organization_id,
    )

    if movement is None:
        abort(404)

    original = None

    if movement.get("reversal_of_movement_id"):
        original = get_cash_movement(
            movement["reversal_of_movement_id"],
            organization_id,
        )

    return render_template(
        "cash/detail.html",
        movement=movement,
        original=original,
        balances=get_balances(organization_id),
    )


@app.route(
    "/cash/<int:movement_id>/reverse",
    methods=["POST"],
)
@admin_required
def cash_reverse(movement_id):
    organization_id = require_user_organization()
    current_user = get_current_user()
    reason = request.form.get(
        "reversal_reason",
        "",
    ).strip()

    try:
        reverse_movement(
            organization_id,
            movement_id,
            user_id=current_user["id"],
            reason=reason,
        )
    except CashTreasuryError as error:
        flash_i18n(error.message_key, "error")
        return redirect(
            url_for(
                "cash_detail",
                movement_id=movement_id,
            )
        )

    flash_i18n("cash_reversed", "success")

    return redirect(
        url_for(
            "cash_detail",
            movement_id=movement_id,
        )
    )


@app.route(
    "/cash/opening-balance",
    methods=["GET", "POST"],
)
@admin_required
def cash_opening_balance():
    organization_id = require_user_organization()
    current_user = get_current_user()
    balances = get_balances(organization_id)
    form_values = {
        "amount_ars": "",
        "amount_usd": "",
        "movement_date": date.today().isoformat(),
    }

    if request.method == "POST":
        form_values = {
            "amount_ars": request.form.get(
                "amount_ars",
                "",
            ).strip(),
            "amount_usd": request.form.get(
                "amount_usd",
                "",
            ).strip(),
            "movement_date": request.form.get(
                "movement_date",
                "",
            ).strip() or date.today().isoformat(),
        }

        try:
            from modules.cash_treasury import (
                parse_cash_date,
            )

            movement_date = parse_cash_date(
                form_values["movement_date"]
            ) or date.today()
            set_opening_balances(
                organization_id,
                amounts_by_currency={
                    "ARS": form_values["amount_ars"],
                    "USD": form_values["amount_usd"],
                },
                user_id=current_user["id"],
                movement_date=movement_date,
            )
        except CashTreasuryError as error:
            return render_template(
                "cash/opening.html",
                form_values=form_values,
                balances=balances,
                errors=localize_form_errors(
                    [error.message_key]
                ),
            )

        flash_i18n("cash_opening_saved", "success")

        return redirect(url_for("cash_list"))

    return render_template(
        "cash/opening.html",
        form_values=form_values,
        balances=balances,
        errors=[],
    )


def _cash_ai_review_template(organization_id, draft, **extra):
    context = build_review_context(organization_id, draft)
    force_duplicates = extra.pop("force_duplicates", None)
    errors = extra.pop("errors", None) or []
    context.update(extra)

    if force_duplicates is not None:
        context["duplicates"] = force_duplicates

    context.update(
        {
            "currencies": CASH_CURRENCIES,
            "payment_methods": AI_PAYMENT_METHODS,
            "income_categories": INCOME_CATEGORIES,
            "expense_categories": EXPENSE_CATEGORIES,
            "payment_undetermined": PAYMENT_UNDETERMINED,
            "errors": errors,
        }
    )
    return render_template("cash/ai_review.html", **context)


@app.route(
    "/cash/ai",
    methods=["GET", "POST"],
)
@admin_required
def cash_ai_new():
    organization_id = require_user_organization()
    current_user = get_current_user()

    if request.method == "GET":
        return render_template(
            "cash/ai_upload.html",
            form_values={"user_context_text": ""},
            errors=[],
        )

    context_text = request.form.get(
        "user_context_text",
        "",
    ).strip()
    file_storage = request.files.get("receipt")
    log_cash_ai_runtime_config()
    app.logger.info(
        "cash_ai stage=upload_post has_file=%s "
        "filename=%r context_len=%s",
        bool(file_storage and file_storage.filename),
        getattr(file_storage, "filename", None),
        len(context_text),
    )

    try:
        draft = start_ai_analysis(
            organization_id,
            user_id=current_user["id"],
            user_context_text=context_text,
            file_storage=file_storage,
            language=get_current_language(),
        )
    except CashAiError as error:
        app.logger.warning(
            "cash_ai stage=upload_failed key=%s stage_detail=%s",
            error.message_key,
            error.kwargs.get("stage"),
        )
        return render_template(
            "cash/ai_upload.html",
            form_values={
                "user_context_text": context_text,
            },
            errors=localize_form_errors(
                [error.message_key]
            ),
        )

    return redirect(
        url_for(
            "cash_ai_review",
            draft_id=draft["id"],
        )
    )


@app.route(
    "/cash/ai/<int:draft_id>",
    methods=["GET", "POST"],
)
@admin_required
def cash_ai_review(draft_id):
    organization_id = require_user_organization()
    draft = get_cash_ai_draft(draft_id, organization_id)

    if draft is None:
        abort(404)

    if request.method == "GET":
        return _cash_ai_review_template(
            organization_id,
            draft,
        )

    action = request.form.get("action", "save")
    app.logger.info(
        "cash_ai.confirm_received draft_id=%s action=%r "
        "token_present=%s acknowledge=%s",
        draft_id,
        action,
        bool(request.form.get("confirm_token")),
        request.form.get("acknowledge_duplicates"),
    )
    form_values = {
        "movement_type": request.form.get(
            "movement_type",
            "",
        ).strip(),
        "currency": request.form.get(
            "currency",
            "",
        ).strip().upper(),
        "amount": request.form.get("amount", "").strip(),
        "category": request.form.get(
            "category",
            "",
        ).strip(),
        "description": request.form.get(
            "description",
            "",
        ).strip(),
        "merchant": request.form.get(
            "merchant",
            "",
        ).strip(),
        "payment_method": request.form.get(
            "payment_method",
            "",
        ).strip(),
        "receipt_number": request.form.get(
            "receipt_number",
            "",
        ).strip(),
        "movement_date": request.form.get(
            "movement_date",
            "",
        ).strip(),
        "notes": request.form.get("notes", "").strip(),
    }

    if action == "save":
        draft = update_draft_from_form(
            organization_id,
            draft_id,
            form_values,
        )
        flash_i18n("cash_ai_draft_updated", "success")
        return _cash_ai_review_template(
            organization_id,
            draft,
            editing=True,
        )

    if action == "edit":
        return _cash_ai_review_template(
            organization_id,
            draft,
            editing=True,
        )

    if action != "confirm":
        app.logger.warning(
            "cash_ai.confirm_failed draft_id=%s "
            "reason=unknown_action action=%r",
            draft_id,
            action,
        )
        flash_i18n("cash_ai_err_confirm_action", "error")
        return _cash_ai_review_template(
            organization_id,
            draft,
            errors=localize_form_errors(
                ["cash_ai_err_confirm_action"]
            ),
        )

    acknowledge = (
        request.form.get("acknowledge_duplicates") == "1"
    )

    try:
        movement = confirm_ai_draft(
            organization_id,
            draft_id,
            user_id=get_current_user()["id"],
            confirm_token=request.form.get(
                "confirm_token",
                "",
            ),
            form_values=form_values,
            acknowledge_duplicates=acknowledge,
        )
    except CashAiError as error:
        if error.message_key == "cash_ai_err_possible_duplicate":
            draft = update_draft_from_form(
                organization_id,
                draft_id,
                form_values,
            )
            flash_i18n(
                "cash_ai_err_possible_duplicate",
                "error",
            )
            return _cash_ai_review_template(
                organization_id,
                draft,
                force_duplicates=error.kwargs.get(
                    "duplicates"
                )
                or [],
                errors=localize_form_errors(
                    ["cash_ai_err_possible_duplicate"]
                ),
            )

        app.logger.warning(
            "cash_ai.confirm_failed draft_id=%s key=%s "
            "stage=%s",
            draft_id,
            error.message_key,
            error.kwargs.get("stage"),
        )
        flash_i18n(error.message_key, "error")
        draft = get_cash_ai_draft(
            draft_id,
            organization_id,
        )
        return _cash_ai_review_template(
            organization_id,
            draft,
            editing=True,
            errors=localize_form_errors(
                error.kwargs.get("validation_errors")
                or [error.message_key]
            ),
        )
    except CashTreasuryError as error:
        app.logger.warning(
            "cash_ai.confirm_failed draft_id=%s "
            "treasury_key=%s",
            draft_id,
            error.message_key,
        )
        flash_i18n(error.message_key, "error")
        draft = get_cash_ai_draft(
            draft_id,
            organization_id,
        )
        return _cash_ai_review_template(
            organization_id,
            draft,
            editing=True,
            errors=localize_form_errors(
                [error.message_key]
            ),
        )

    app.logger.info(
        "cash_ai.redirect_success draft_id=%s "
        "movement_id=%s",
        draft_id,
        movement["id"],
    )
    flash_i18n("cash_ai_confirmed", "success")
    return redirect(
        url_for(
            "cash_detail",
            movement_id=movement["id"],
        )
    )


@app.route(
    "/cash/ai/<int:draft_id>/retry",
    methods=["POST"],
)
@admin_required
def cash_ai_retry(draft_id):
    organization_id = require_user_organization()

    try:
        draft = retry_ai_analysis(
            organization_id,
            draft_id,
            language=get_current_language(),
        )
    except CashAiError as error:
        flash_i18n(error.message_key, "error")
        draft = get_cash_ai_draft(
            draft_id,
            organization_id,
        )
        if draft is None:
            return redirect(url_for("cash_ai_new"))
        return _cash_ai_review_template(
            organization_id,
            draft,
        )

    return redirect(
        url_for(
            "cash_ai_review",
            draft_id=draft["id"],
        )
    )


@app.route("/cash/<int:movement_id>/receipt")
@admin_required
def cash_receipt(movement_id):
    organization_id = require_user_organization()
    movement = get_cash_movement(
        movement_id,
        organization_id,
    )

    if movement is None or not movement.get(
        "attachment_path"
    ):
        abort(404)

    path = absolute_receipt_path(
        movement["attachment_path"],
        organization_id,
    )

    if not path.is_file():
        abort(404)

    return send_file(
        path,
        mimetype=(
            movement.get("attachment_content_type")
            or "application/octet-stream"
        ),
        download_name=(
            movement.get("attachment_original_name")
            or path.name
        ),
        as_attachment=request.args.get("download") == "1",
    )


@app.route("/cash/ai/<int:draft_id>/receipt")
@admin_required
def cash_ai_draft_receipt(draft_id):
    organization_id = require_user_organization()
    draft = get_cash_ai_draft(draft_id, organization_id)

    if draft is None or not draft.get("attachment_path"):
        abort(404)

    path = absolute_receipt_path(
        draft["attachment_path"],
        organization_id,
    )

    if not path.is_file():
        abort(404)

    return send_file(
        path,
        mimetype=(
            draft.get("attachment_content_type")
            or "application/octet-stream"
        ),
        download_name=(
            draft.get("attachment_original_name")
            or path.name
        ),
        as_attachment=request.args.get("download") == "1",
    )


from modules.billing_routes import register_billing_routes

register_billing_routes(
    app,
    helpers={
        "require_user_organization": require_user_organization,
        "require_billing_user": require_billing_user,
        "flash_i18n": flash_i18n,
        "ensure_operation_scope": ensure_operation_scope,
        "_flash_invoicing_error": _flash_invoicing_error,
        "_load_billing_invoice": _load_billing_invoice,
    },
)


if __name__ == "__main__":
    from modules.database import create_tables

    create_tables()

    app.run(
        host=get_host(),
        port=get_port(),
        debug=get_flask_debug()
    )
