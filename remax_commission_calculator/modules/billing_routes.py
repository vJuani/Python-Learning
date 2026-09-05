"""
Billing / Facturación HTTP routes (multi-side, multi-issuer).
"""

from __future__ import annotations

import io
import logging

from flask import (
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from modules.auth import (
    admin_required,
    get_current_user,
    get_guest_access,
    is_admin,
    is_agent,
    login_required,
)
from modules.database import (
    ensure_parties_for_operation,
    get_agent_billing_profile,
    get_agent_record,
    get_agents,
    get_operation_party,
    get_operation_record,
    get_parties_for_operation,
    list_billing_issuer_profiles,
    list_invoices_for_operation,
    set_operation_party_client_fields,
    update_agent_arca_config,
    update_issuer_arca_config,
    upsert_agent_billing_profile,
    upsert_billing_issuer_profile,
    deactivate_billing_issuer_profile,
    set_default_billing_issuer_profile,
    get_billing_issuer_profile,
    get_user_by_agent_id,
)
from modules.database.organization_settings_repository import (
    get_organization_settings,
    update_organization_billing_fields,
)
from modules.arca.config import is_arca_fiscal_enabled
from modules.arca.issuer_config import (
    build_arca_display,
    test_arca_connection,
)
from modules.billing_issuer_validation import (
    validate_agent_billing_profile,
    validate_billing_issuer_profile,
)
from modules.billing_ai_workspace import (
    build_billing_ai_workspace,
    format_preview_amounts,
    preview_to_workspace,
)
from modules.i18n import translate
from modules.invoice_ai_service import (
    DisambiguationResult,
    INTENT_LIST_PENDING,
    MissingSideResult,
    ParsedInvoiceIntent,
    ResolvedInvoiceIntent,
    parse_invoice_intent,
    resolve_invoice_intent,
)
from modules.invoicing import (
    InvoicingError,
    ISSUER_MODE_OFFICE,
    PAYMENT_CONDITIONS,
    SIDE_BUYER,
    SIDE_SELLER,
    TAX_CONDITIONS,
    VALID_SIDES,
    agent_billing_ready,
    billing_kpis,
    build_draft_preview_for_side,
    build_draft_preview_for_charge,
    cancel_invoice,
    confirm_draft,
    issue_fiscal_invoice,
    create_draft_for_side,
    create_draft_for_charge,
    default_issuer_mode_for_user,
    generate_draft_pdf_bytes,
    get_invoice,
    get_charge_invoice_context,
    get_next_missing_client_field,
    get_operation_sides_state,
    list_invoices,
    list_pending_operations,
    list_billable_agent_charges,
    org_billing_ready,
    set_party_invoice_amount,
    update_draft_options,
    validate_cuit,
)

logger = logging.getLogger(__name__)


def register_billing_routes(app, *, helpers):
    require_user_organization = helpers[
        "require_user_organization"
    ]
    require_billing_user = helpers["require_billing_user"]
    flash_i18n = helpers["flash_i18n"]
    get_current_language = helpers["get_current_language"]
    get_safe_redirect_target = helpers[
        "get_safe_redirect_target"
    ]
    ensure_operation_scope = helpers[
        "ensure_operation_scope"
    ]
    flash_invoicing_error = helpers[
        "_flash_invoicing_error"
    ]
    load_billing_invoice = helpers[
        "_load_billing_invoice"
    ]

    def _flash_billing_error(
        error,
        *,
        operation_id=None,
        side=None,
        user=None,
        agent_id=None,
        next_url=None,
    ):
        flash_invoicing_error(
            error,
            operation_id=operation_id,
            side=side,
            user=user or get_current_user(),
            agent_id=agent_id,
            next_url=next_url,
        )

    def _valid_side(side):
        if side not in VALID_SIDES:
            abort(404)
        return side

    def _billing_ai_context():
        return session.get("billing_ai_context") or {}

    def _set_billing_ai_context(**kwargs):
        ctx = _billing_ai_context()
        ctx.update(kwargs)
        session["billing_ai_context"] = ctx

    def _clear_billing_ai_context():
        session.pop("billing_ai_context", None)
        session.pop("billing_ai_operation", None)

    def _issuer_defaults(user, settings, organization_id):
        from modules.billing_issuer_validation import (
            resolve_office_issuer_profile_id,
        )

        issuer_mode = default_issuer_mode_for_user(
            user,
            settings,
            organization_id,
        )
        issuer_profile_id = None
        if issuer_mode == ISSUER_MODE_OFFICE:
            try:
                issuer_profile_id = (
                    resolve_office_issuer_profile_id(
                        organization_id,
                        settings=settings,
                    )
                )
            except InvoicingError:
                issuer_profile_id = settings.get(
                    "default_issuer_profile_id"
                )
        return issuer_mode, issuer_profile_id

    def _prepare_side_view(
        operation_id,
        side,
        user,
        organization_id,
    ):
        side = _valid_side(side)
        operation = get_operation_record(
            operation_id,
            organization_id,
        )
        if operation is None:
            abort(404)
        ensure_operation_scope(operation, organization_id)

        if is_agent(user) and not is_admin(user):
            if user.get("agent_id") != operation[
                "agent_db_id"
            ]:
                abort(403)

        ensure_parties_for_operation(
            organization_id,
            operation_id,
        )
        party = get_operation_party(
            organization_id,
            operation_id,
            side,
        )
        settings = get_organization_settings(
            organization_id
        )
        issuer_mode, issuer_profile_id = _issuer_defaults(
            user,
            settings,
            organization_id,
        )

        missing_field = get_next_missing_client_field(
            party
        )
        if missing_field:
            logger.info(
                "invoice_ai_missing_fields field=%s operation=%s side=%s",
                missing_field,
                operation_id,
                side,
            )
            return render_template(
                "billing/missing_client.html",
                operation=operation,
                side=side,
                party=party,
                missing_field=missing_field,
                tax_conditions=TAX_CONDITIONS,
                client_name=(
                    party.get("client_legal_name")
                    or operation.get("property")
                ),
            )

        try:
            preview = build_draft_preview_for_side(
                organization_id,
                operation_id,
                side,
                user,
                issuer_mode=issuer_mode,
                issuer_profile_id=issuer_profile_id,
            )
        except InvoicingError as error:
            _flash_billing_error(
                error,
                operation_id=operation_id,
                side=side,
                user=user,
            )
            if error.message_key == (
                "invoice_err_billing_profile_incomplete"
            ):
                if is_agent(user) and not is_admin(user):
                    return redirect(
                        url_for(
                            "billing_agent_profile_self"
                        )
                    )
                return redirect(
                    url_for("billing_issuers")
                )
            if error.message_key in (
                "invoice_err_client_incomplete",
                "invoice_err_party_client_incomplete",
            ):
                missing_field = get_next_missing_client_field(
                    party
                )
                if missing_field:
                    return render_template(
                        "billing/missing_client.html",
                        operation=operation,
                        side=side,
                        party=party,
                        missing_field=missing_field,
                        tax_conditions=TAX_CONDITIONS,
                        client_name=(
                            party.get("client_legal_name")
                            or operation.get("property")
                        ),
                    )
            return redirect(
                url_for(
                    "billing_list",
                    tab="pending",
                )
            )

        issuer_profiles = list_billing_issuer_profiles(
            organization_id,
            active_only=True,
        )
        return render_template(
            "billing/prepare.html",
            operation=operation,
            side=side,
            preview=preview,
            settings=settings,
            issuer_profiles=issuer_profiles,
            issuer_mode=issuer_mode,
            issuer_profile_id=issuer_profile_id,
            is_staff=is_admin(user),
        )

    def _append_billing_chat(role, text, *, text_key=None):
        messages = session.get("billing_ai_chat") or []
        entry = {"role": role, "text": text}
        if text_key:
            entry["text_key"] = text_key
        messages.append(entry)
        session["billing_ai_chat"] = messages[-30:]

    def _billing_ai_workspace_session():
        return session.get("billing_ai_workspace")

    def _set_billing_ai_workspace(workspace):
        session["billing_ai_workspace"] = workspace

    def _clear_billing_ai_workspace():
        session.pop("billing_ai_workspace", None)

    def _resolve_ai_workspace_preview(
        result,
        user,
        organization_id,
    ):
        settings = get_organization_settings(organization_id)
        issuer_mode, issuer_profile_id = _issuer_defaults(
            user,
            settings,
            organization_id,
        )
        preview = build_draft_preview_for_side(
            organization_id,
            result.operation_id,
            result.side,
            user,
            issuer_mode=issuer_mode,
            issuer_profile_id=issuer_profile_id,
        )
        from modules.arca.connections import arca_chip_for

        chip = arca_chip_for(organization_id, user)
        if chip.get("point_of_sale"):
            preview["point_of_sale"] = chip["point_of_sale"]
        language = get_current_language()
        enriched = format_preview_amounts(
            preview,
            language=language,
        )
        workspace = preview_to_workspace(
            enriched,
            operation_id=result.operation_id,
            side=result.side,
            language=language,
        )
        _set_billing_ai_workspace(workspace)
        _append_billing_chat(
            "assistant",
            translate("billing_ai_preview_ready", language=language),
            text_key="billing_ai_preview_ready",
        )
        return redirect(url_for("billing_list"))

    @app.route("/billing")
    @login_required
    def billing_list():
        user = require_billing_user()
        organization_id = require_user_organization()

        agent_scope = None
        if is_agent(user) and not is_admin(user):
            agent_scope = user.get("agent_id")
            if agent_scope is None:
                flash_i18n("agent_scope_missing", "error")
                abort(403)

        view = request.args.get("view", "workspace").strip()
        tab = request.args.get("tab", "pending").strip()
        if tab not in (
            "pending",
            "draft",
            "ready",
            "issued",
        ):
            tab = "pending"

        filters = {
            "agent_id": request.args.get("agent_id", "").strip(),
            "status": request.args.get("status", "").strip(),
            "operation_id": request.args.get(
                "operation_id",
                ""
            ).strip(),
            "side": request.args.get("side", "").strip(),
            "payment_condition": request.args.get(
                "payment_condition",
                ""
            ).strip(),
            "q": request.args.get("q", "").strip(),
        }

        list_kwargs = {"agent_id": agent_scope}
        if is_admin(user) and filters["agent_id"].isdigit():
            list_kwargs["agent_id"] = int(filters["agent_id"])
        if filters["q"]:
            list_kwargs["q"] = filters["q"]
        if filters["operation_id"].isdigit():
            list_kwargs["operation_id"] = int(
                filters["operation_id"]
            )
        if filters["side"] in VALID_SIDES:
            list_kwargs["side"] = filters["side"]

        if tab == "draft":
            list_kwargs["status"] = "draft"
        elif tab == "ready":
            list_kwargs["status"] = "ready_to_issue"
        elif tab == "issued":
            list_kwargs["status"] = "issued"
        elif filters["status"]:
            list_kwargs["status"] = filters["status"]

        pending_items = list_pending_operations(
            organization_id,
            agent_id=agent_scope,
        )

        ai_message = session.pop("billing_ai_message", None)
        ai_options = session.pop("billing_ai_options", None)
        ai_operation = session.pop(
            "billing_ai_operation",
            None,
        )
        if not ai_message:
            ai_operation = None

        language = get_current_language()
        chat_messages = session.get("billing_ai_chat")
        ai_workspace = build_billing_ai_workspace(
            language=language,
            chat_messages=chat_messages,
            workspace=_billing_ai_workspace_session(),
            ai_message=ai_message,
            ai_options=ai_options,
            ai_operation=ai_operation,
        )
        if chat_messages is not None:
            session["billing_ai_chat"] = ai_workspace["chat_messages"]

        from modules.arca.connections import arca_chip_for

        return render_template(
            "billing/list.html",
            view=view,
            arca=arca_chip_for(organization_id, user),
            invoices=(
                list_invoices(
                    organization_id,
                    **list_kwargs,
                )
                if tab != "pending"
                else []
            ),
            kpis=billing_kpis(
                organization_id,
                agent_id=agent_scope,
            ),
            pending_items=pending_items,
            tab=tab,
            filters=filters,
            agents=(
                get_agents(organization_id)
                if is_admin(user)
                else []
            ),
            payment_conditions=PAYMENT_CONDITIONS,
            is_staff=is_admin(user),
            ai_workspace=ai_workspace,
        )

    @app.route("/billing/ai/prepare", methods=["GET", "POST"])
    @login_required
    def billing_ai_prepare():
        user = require_billing_user()
        organization_id = require_user_organization()
        text = (
            request.form.get("prompt", "").strip()
            or request.args.get("prompt", "").strip()
        )
        if not text:
            flash_i18n("billing_ai_empty_prompt", "error")
            return redirect(url_for("billing_list"))

        _append_billing_chat("user", text)

        context = _billing_ai_context()
        parsed = parse_invoice_intent(
            text,
            context=context,
        )

        if (
            context.get("operation_id")
            and parsed.side is None
        ):
            side_from_text = parse_invoice_intent(
                text,
                context={},
            ).side
            if side_from_text:
                parsed.side = side_from_text
                parsed.intent = parsed.intent

        try:
            result = resolve_invoice_intent(
                parsed,
                organization_id,
                user,
            )
        except PermissionError:
            abort(403)

        if isinstance(result, ParsedInvoiceIntent):
            if result.intent == INTENT_LIST_PENDING:
                _clear_billing_ai_context()
                return redirect(
                    url_for("billing_list", tab="pending")
                )

        if isinstance(result, DisambiguationResult):
            session["billing_ai_message"] = result.message_key
            session["billing_ai_options"] = result.options
            return redirect(url_for("billing_list"))

        if isinstance(result, MissingSideResult):
            _set_billing_ai_context(
                operation_id=result.operation_id,
            )
            session["billing_ai_message"] = (
                result.message_key
            )
            session["billing_ai_operation"] = (
                result.operation_label
            )
            return redirect(url_for("billing_list"))

        if isinstance(result, ResolvedInvoiceIntent):
            logger.info(
                "invoice_ai_intent_resolved operation=%s side=%s",
                result.operation_id,
                result.side,
            )
            _clear_billing_ai_context()
            try:
                return _resolve_ai_workspace_preview(
                    result,
                    user,
                    organization_id,
                )
            except InvoicingError as error:
                _flash_billing_error(
                    error,
                    operation_id=result.operation_id,
                    side=result.side,
                    user=user,
                )
                return redirect(url_for("billing_list"))

        flash_i18n("billing_ai_not_understood", "error")
        return redirect(url_for("billing_list"))

    @app.route(
        "/billing/ai/select-operation/<int:operation_id>",
        methods=["GET"],
    )
    @login_required
    def billing_ai_select_operation(operation_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        operation = get_operation_record(
            operation_id,
            organization_id,
        )
        if operation is None:
            abort(404)
        ensure_operation_scope(operation, organization_id)

        if is_agent(user) and not is_admin(user):
            if user.get("agent_id") != operation[
                "agent_db_id"
            ]:
                abort(403)

        prop = operation.get("property") or ""
        op_code = operation.get("id") or ""
        _set_billing_ai_context(operation_id=operation_id)
        session["billing_ai_message"] = "billing_ai_ask_side"
        session["billing_ai_operation"] = (
            f"{prop} · {op_code}"
        )
        return redirect(url_for("billing_list"))

    @app.route(
        "/billing/operations/<int:operation_id>/<side>/prepare",
        methods=["GET"],
    )
    @login_required
    def billing_prepare(operation_id, side):
        user = require_billing_user()
        organization_id = require_user_organization()
        return _prepare_side_view(
            operation_id,
            side,
            user,
            organization_id,
        )

    @app.route(
        "/billing/operations/<int:operation_id>/<side>/client-field",
        methods=["POST"],
    )
    @login_required
    def billing_set_client_field(operation_id, side):
        side = _valid_side(side)
        user = require_billing_user()
        organization_id = require_user_organization()
        operation = get_operation_record(
            operation_id,
            organization_id,
        )
        if operation is None:
            abort(404)
        ensure_operation_scope(operation, organization_id)

        field_name = request.form.get("field_name", "")
        field_value = request.form.get(
            "field_value",
            ""
        ).strip()

        allowed = {
            "client_tax_id",
            "client_tax_condition",
            "client_legal_name",
            "client_fiscal_address",
        }
        if field_name not in allowed:
            abort(400)

        kwargs = {field_name: field_value}
        set_operation_party_client_fields(
            organization_id,
            operation_id,
            side,
            **kwargs,
        )

        return redirect(
            url_for(
                "billing_prepare",
                operation_id=operation_id,
                side=side,
            )
        )

    @app.route(
        "/billing/operations/<int:operation_id>/new",
    )
    @login_required
    def billing_new_from_operation(operation_id):
        """Keep users in the billing composer with the operation context."""
        require_billing_user()
        return redirect(
            url_for(
                "billing_ai_select_operation",
                operation_id=operation_id,
            )
        )

    @app.route(
        "/billing/operations/<int:operation_id>/<side>/new",
        methods=["GET", "POST"],
    )
    @login_required
    def billing_new_for_side(operation_id, side):
        side = _valid_side(side)
        user = require_billing_user()
        organization_id = require_user_organization()
        operation = get_operation_record(
            operation_id,
            organization_id,
        )
        if operation is None:
            abort(404)
        ensure_operation_scope(operation, organization_id)

        if is_agent(user) and not is_admin(user):
            if user.get("agent_id") != operation[
                "agent_db_id"
            ]:
                abort(403)

        settings = get_organization_settings(organization_id)
        issuer_mode = (
            request.form.get("issuer_mode")
            or request.args.get("issuer_mode")
            or default_issuer_mode_for_user(
                user,
                settings,
                organization_id,
            )
        )
        issuer_profile_id = request.form.get(
            "issuer_profile_id"
        ) or request.args.get("issuer_profile_id")
        if issuer_profile_id and str(
            issuer_profile_id
        ).isdigit():
            issuer_profile_id = int(issuer_profile_id)
        elif issuer_mode == ISSUER_MODE_OFFICE:
            from modules.billing_issuer_validation import (
                resolve_office_issuer_profile_id,
            )

            try:
                issuer_profile_id = (
                    resolve_office_issuer_profile_id(
                        organization_id,
                        settings=settings,
                    )
                )
            except InvoicingError:
                issuer_profile_id = settings.get(
                    "default_issuer_profile_id"
                )
        else:
            issuer_profile_id = settings.get(
                "default_issuer_profile_id"
            )

        if request.method == "GET":
            return redirect(
                url_for(
                    "billing_prepare",
                    operation_id=operation_id,
                    side=side,
                )
            )

        if request.method == "POST":
            try:
                invoice = create_draft_for_side(
                    organization_id,
                    operation_id,
                    side,
                    user,
                    issuer_mode=issuer_mode,
                    issuer_profile_id=issuer_profile_id,
                )
            except InvoicingError as error:
                _flash_billing_error(
                    error,
                    operation_id=operation_id,
                    side=side,
                    user=user,
                )
                if error.message_key == (
                    "invoice_err_billing_profile_incomplete"
                ):
                    return redirect(
                        url_for("billing_agent_profile_self")
                    )
                return redirect(
                    url_for(
                        "billing_prepare",
                        operation_id=operation_id,
                        side=side,
                    )
                )

            flash_i18n("invoice_draft_created", "success")
            return redirect(
                url_for(
                    "billing_review",
                    invoice_id=invoice["id"],
                )
            )

        return redirect(
            url_for(
                "billing_prepare",
                operation_id=operation_id,
                side=side,
            )
        )

    @app.route(
        "/billing/agent-account-charges/<int:charge_id>/prepare"
    )
    @admin_required
    def billing_prepare_charge(charge_id):
        user = get_current_user()
        organization_id = require_user_organization()
        settings = get_organization_settings(organization_id)
        issuer_mode = ISSUER_MODE_OFFICE
        issuer_profiles = list_billing_issuer_profiles(
            organization_id,
            active_only=True,
        )
        issuer_profile_id = request.args.get("issuer_profile_id")
        if issuer_profile_id and str(issuer_profile_id).isdigit():
            issuer_profile_id = int(issuer_profile_id)
        else:
            _mode, issuer_profile_id = _issuer_defaults(
                user,
                settings,
                organization_id,
            )
        if issuer_profile_id is None and issuer_profiles:
            issuer_profile_id = issuer_profiles[0]["id"]
        try:
            preview = build_draft_preview_for_charge(
                organization_id,
                charge_id,
                user,
                issuer_mode=issuer_mode,
                issuer_profile_id=issuer_profile_id,
            )
        except InvoicingError as error:
            agent_id = None
            try:
                agent_id = get_charge_invoice_context(
                    organization_id,
                    charge_id,
                    user=user,
                )["movement"]["agent_id"]
            except InvoicingError:
                pass
            _flash_billing_error(
                error,
                user=user,
                agent_id=agent_id,
                next_url=url_for(
                    "billing_prepare_charge",
                    charge_id=charge_id,
                ),
            )
            if (
                error.message_key
                == "invoice_err_recipient_profile_incomplete"
                and agent_id
            ):
                return redirect(
                    url_for(
                        "agents_detail",
                        agent_id=agent_id,
                        _anchor="datos-fiscales",
                    )
                )
            return redirect(url_for("agent_account_index"))
        return render_template(
            "billing/prepare_charge.html",
            preview=preview,
            settings=settings,
            issuer_profiles=issuer_profiles,
            issuer_profile_id=issuer_profile_id,
        )

    @app.route(
        "/billing/agent-account-charges/<int:charge_id>/new",
        methods=["POST"],
    )
    @admin_required
    def billing_new_for_charge(charge_id):
        user = get_current_user()
        organization_id = require_user_organization()
        issuer_profile_id = request.form.get("issuer_profile_id")
        if issuer_profile_id and str(issuer_profile_id).isdigit():
            issuer_profile_id = int(issuer_profile_id)
        else:
            issuer_profile_id = None
        try:
            invoice = create_draft_for_charge(
                organization_id,
                charge_id,
                user,
                issuer_mode=ISSUER_MODE_OFFICE,
                issuer_profile_id=issuer_profile_id,
                payment_condition=request.form.get(
                    "payment_condition"
                ),
                issue_date=request.form.get("issue_date"),
            )
        except InvoicingError as error:
            agent_id = None
            try:
                agent_id = get_charge_invoice_context(
                    organization_id,
                    charge_id,
                    user=user,
                )["movement"]["agent_id"]
            except InvoicingError:
                pass
            _flash_billing_error(
                error,
                user=user,
                agent_id=agent_id,
                next_url=url_for(
                    "billing_prepare_charge",
                    charge_id=charge_id,
                ),
            )
            return redirect(
                url_for(
                    "billing_prepare_charge",
                    charge_id=charge_id,
                    issuer_profile_id=issuer_profile_id,
                )
            )
        flash_i18n("invoice_draft_created", "success")
        return redirect(
            url_for("billing_review", invoice_id=invoice["id"])
        )

    @app.route("/billing/agent-account-charges/search")
    @admin_required
    def billing_charge_search():
        organization_id = require_user_organization()
        agent_id = request.args.get("agent_id", type=int)
        if not agent_id:
            return jsonify({"charges": []})
        charges = list_billable_agent_charges(
            organization_id,
            agent_id=agent_id,
        )
        return jsonify(
            {
                "charges": [
                    {
                        "id": charge["id"],
                        "description": charge.get("description"),
                        "billing_period": charge.get(
                            "billing_period"
                        ),
                        "currency": charge.get("currency"),
                        "amount": charge.get("gross_amount"),
                    }
                    for charge in charges
                ]
            }
        )

    @app.route(
        "/billing/<int:invoice_id>/review",
        methods=["GET", "POST"],
    )
    @login_required
    def billing_review(invoice_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        invoice = load_billing_invoice(
            organization_id,
            invoice_id,
            user,
        )

        if request.method == "POST":
            action = request.form.get("action", "confirm")
            if action == "edit":
                try:
                    update_draft_options(
                        organization_id,
                        invoice_id,
                        user,
                        payment_condition=request.form.get(
                            "payment_condition"
                        ),
                        issue_date=request.form.get(
                            "issue_date"
                        ),
                    )
                except InvoicingError as error:
                    _flash_billing_error(
                        error,
                        operation_id=invoice.get(
                            "operation_id"
                        ),
                        side=invoice.get("side"),
                        user=user,
                    )
                return redirect(
                    url_for(
                        "billing_review",
                        invoice_id=invoice_id,
                    )
                )

            try:
                confirm_draft(
                    organization_id,
                    invoice_id,
                    user,
                )
                flash_i18n(
                    "invoice_draft_confirmed",
                    "success",
                )
            except InvoicingError as error:
                _flash_billing_error(
                    error,
                    operation_id=invoice.get(
                        "operation_id"
                    ),
                    side=invoice.get("side"),
                    user=user,
                )
                return redirect(
                    url_for(
                        "billing_review",
                        invoice_id=invoice_id,
                    )
                )
            return redirect(
                url_for(
                    "billing_review",
                    invoice_id=invoice_id,
                )
            )

        operation = None
        charge_context = None
        if invoice.get("operation_id"):
            operation = get_operation_record(
                invoice["operation_id"],
                organization_id,
            )
        elif invoice.get("agent_account_movement_id"):
            charge_context = get_charge_invoice_context(
                organization_id,
                invoice["agent_account_movement_id"],
                user=user,
            )

        return render_template(
            "billing/review.html",
            invoice=invoice,
            operation=operation,
            charge_context=charge_context,
            payment_conditions=PAYMENT_CONDITIONS,
            arca_enabled=is_arca_fiscal_enabled(),
            is_staff=is_admin(user),
            can_change_issuer=True,
        )

    @app.route("/billing/<int:invoice_id>")
    @login_required
    def billing_detail(invoice_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        invoice = load_billing_invoice(
            organization_id,
            invoice_id,
            user,
        )
        operation = None
        charge_context = None
        if invoice.get("operation_id"):
            operation = get_operation_record(
                invoice["operation_id"],
                organization_id,
            )
        elif invoice.get("agent_account_movement_id"):
            charge_context = get_charge_invoice_context(
                organization_id,
                invoice["agent_account_movement_id"],
                user=user,
            )
        return render_template(
            "billing/detail.html",
            invoice=invoice,
            operation=operation,
            charge_context=charge_context,
            arca_enabled=is_arca_fiscal_enabled(),
            is_staff=is_admin(user),
        )

    @app.route(
        "/billing/<int:invoice_id>/issue-arca",
        methods=["POST"],
    )
    @login_required
    def billing_issue_arca(invoice_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        invoice = load_billing_invoice(
            organization_id,
            invoice_id,
            user,
        )
        try:
            issue_fiscal_invoice(
                organization_id,
                invoice_id,
                user,
            )
            flash_i18n("invoice_arca_issued", "success")
        except InvoicingError as error:
            _flash_billing_error(
                error,
                operation_id=invoice.get("operation_id"),
                side=invoice.get("side"),
                user=user,
            )
        return redirect(
            url_for("billing_detail", invoice_id=invoice_id)
        )

    @app.route(
        "/billing/<int:invoice_id>/retry",
        methods=["POST"],
    )
    @login_required
    def billing_retry_invoice(invoice_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        invoice = load_billing_invoice(
            organization_id,
            invoice_id,
            user,
        )
        try:
            from modules.invoicing import retry_error_invoice

            retry_error_invoice(
                organization_id,
                invoice_id,
                user,
            )
            flash_i18n("invoice_retry_ready", "success")
        except InvoicingError as error:
            _flash_billing_error(
                error,
                operation_id=invoice.get("operation_id"),
                side=invoice.get("side"),
                user=user,
            )
        return redirect(
            url_for("billing_review", invoice_id=invoice_id)
        )

    @app.route(
        "/billing/<int:invoice_id>/cancel",
        methods=["POST"],
    )
    @login_required
    def billing_cancel(invoice_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        invoice = load_billing_invoice(
            organization_id,
            invoice_id,
            user,
        )
        try:
            cancel_invoice(
                organization_id,
                invoice_id,
                user,
                reason=request.form.get("reason", ""),
            )
            flash_i18n("invoice_cancelled", "success")
        except InvoicingError as error:
            _flash_billing_error(
                error,
                operation_id=invoice.get("operation_id"),
                side=invoice.get("side"),
                user=user,
            )
        return redirect(
            url_for("billing_detail", invoice_id=invoice_id)
        )

    @app.route("/billing/<int:invoice_id>/pdf")
    @login_required
    def billing_pdf(invoice_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        invoice = load_billing_invoice(
            organization_id,
            invoice_id,
            user,
        )
        try:
            pdf_bytes = generate_draft_pdf_bytes(
                organization_id,
                invoice_id,
                user,
            )
        except InvoicingError as error:
            _flash_billing_error(
                error,
                operation_id=invoice.get("operation_id"),
                side=invoice.get("side"),
                user=user,
            )
            return redirect(
                url_for(
                    "billing_detail",
                    invoice_id=invoice_id,
                )
            )

        invoice = get_invoice(organization_id, invoice_id)
        name = (
            (invoice or {}).get("invoice_number_internal")
            or f"invoice-{invoice_id}"
        )
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            download_name=f"{name}-draft.pdf",
            as_attachment=True,
        )

    @app.route(
        "/operations/<int:operation_id>/parties/<side>/invoice-amount",
        methods=["POST"],
    )
    @admin_required
    def operations_set_party_invoice_amount(
        operation_id,
        side,
    ):
        side = _valid_side(side)
        if get_guest_access() is not None:
            abort(403)
        user = get_current_user()
        organization_id = require_user_organization()
        operation = get_operation_record(
            operation_id,
            organization_id,
        )
        if operation is None:
            abort(404)
        try:
            set_party_invoice_amount(
                organization_id,
                operation_id,
                side,
                request.form.get("invoice_amount"),
                request.form.get("invoice_currency")
                or "ARS",
                request.form.get("invoice_exchange_rate"),
                user["id"],
                enable_billing=request.form.get(
                    "enable_billing",
                    "1",
                )
                == "1",
            )
            flash_i18n("invoice_amount_saved", "success")
        except InvoicingError as error:
            _flash_billing_error(
                error,
                operation_id=operation_id,
                side=side,
                user=user,
            )
        return redirect(
            url_for(
                "operations_detail",
                operation_id=operation_id,
            )
        )

    @app.route(
        "/operations/<int:operation_id>/parties/<side>/client",
        methods=["POST"],
    )
    @admin_required
    def operations_set_party_client(operation_id, side):
        side = _valid_side(side)
        organization_id = require_user_organization()
        ensure_parties_for_operation(
            organization_id,
            operation_id,
        )
        set_operation_party_client_fields(
            organization_id,
            operation_id,
            side,
            client_legal_name=request.form.get(
                "client_legal_name", ""
            ),
            client_tax_id=request.form.get(
                "client_tax_id", ""
            ),
            client_tax_condition=request.form.get(
                "client_tax_condition", ""
            ),
            client_fiscal_address=request.form.get(
                "client_fiscal_address", ""
            ),
            client_email=request.form.get(
                "client_email", ""
            ),
            client_phone=request.form.get(
                "client_phone", ""
            ),
        )
        flash_i18n("invoice_profile_saved", "success")
        return redirect(
            url_for(
                "operations_detail",
                operation_id=operation_id,
            )
        )

    # Legacy global amount endpoint → buyer side
    @app.route(
        "/operations/<int:operation_id>/invoice-amount",
        methods=["POST"],
    )
    @admin_required
    def operations_set_invoice_amount(operation_id):
        if get_guest_access() is not None:
            abort(403)
        user = get_current_user()
        organization_id = require_user_organization()
        try:
            set_party_invoice_amount(
                organization_id,
                operation_id,
                SIDE_BUYER,
                request.form.get("invoice_amount"),
                request.form.get("invoice_currency")
                or "ARS",
                request.form.get("invoice_exchange_rate"),
                user["id"],
            )
            flash_i18n("invoice_amount_saved", "success")
        except InvoicingError as error:
            _flash_billing_error(
                error,
                operation_id=operation_id,
                side=side,
                user=user,
            )
        return redirect(
            url_for(
                "operations_detail",
                operation_id=operation_id,
            )
        )

    @app.route(
        "/billing/issuers",
        methods=["GET", "POST"],
    )
    @admin_required
    def billing_issuers():
        organization_id = require_user_organization()
        if request.method == "POST":
            action = request.form.get("action", "save")
            if action in ("arca_save", "arca_test") and is_arca_fiscal_enabled():
                return redirect(url_for("settings_arca"))
            if action == "deactivate":
                deactivate_billing_issuer_profile(
                    organization_id,
                    int(request.form["profile_id"]),
                )
                flash_i18n("invoice_profile_saved", "success")
            elif action == "set_default":
                set_default_billing_issuer_profile(
                    organization_id,
                    int(request.form["profile_id"]),
                )
                flash_i18n("invoice_profile_saved", "success")
            else:
                upsert_billing_issuer_profile(
                    organization_id,
                    profile_id=(
                        int(request.form["profile_id"])
                        if request.form.get("profile_id")
                        else None
                    ),
                    issuer_type=request.form.get(
                        "issuer_type",
                        "organization",
                    ),
                    display_name=request.form.get(
                        "display_name", ""
                    ),
                    legal_name=request.form.get(
                        "legal_name", ""
                    ),
                    tax_id=request.form.get("tax_id", ""),
                    tax_condition=request.form.get(
                        "tax_condition", ""
                    ),
                    fiscal_address=request.form.get(
                        "fiscal_address", ""
                    ),
                    email=request.form.get("email", ""),
                    is_default=request.form.get(
                        "is_default"
                    )
                    == "1",
                )
                flash_i18n("invoice_profile_saved", "success")
            return redirect(url_for("billing_issuers"))

        return render_template(
            "billing/issuers.html",
            profiles=[
                {
                    **profile,
                    "validation": validate_billing_issuer_profile(
                        profile,
                        require_active=False,
                    ),
                    "arca_display": build_arca_display(
                        profile,
                        user=get_current_user(),
                        organization_id=organization_id,
                    ),
                    "arca_form": {
                        "point_of_sale": profile.get(
                            "arca_point_of_sale", ""
                        ),
                        "certificate_ref": profile.get(
                            "arca_certificate_ref"
                        )
                        or f"issuer:{profile['id']}",
                    },
                }
                for profile in list_billing_issuer_profiles(
                    organization_id,
                    active_only=False,
                )
            ],
            tax_conditions=TAX_CONDITIONS,
            arca_enabled=is_arca_fiscal_enabled(),
        )

    def _profile_form(agent_id, organization_id):
        profile = get_agent_billing_profile(
            organization_id,
            agent_id,
        ) or {}
        agent = get_agent_record(agent_id, organization_id) or {}
        linked_user = get_user_by_agent_id(
            agent_id,
            organization_id,
        ) or {}
        return {
            "legal_name": request.form.get(
                "legal_name",
                profile.get("legal_name")
                or agent.get("name", ""),
            ).strip(),
            "tax_id": request.form.get(
                "tax_id",
                profile.get("tax_id", ""),
            ).strip(),
            "tax_condition": request.form.get(
                "tax_condition",
                profile.get("tax_condition", ""),
            ).strip(),
            "fiscal_address": request.form.get(
                "fiscal_address",
                profile.get("fiscal_address", ""),
            ).strip(),
            "email": request.form.get(
                "email",
                profile.get("email")
                or linked_user.get("email", ""),
            ).strip(),
        }

    def _arca_form_values(profile, *, default_ref):
        profile = profile or {}
        return {
            "point_of_sale": request.form.get(
                "arca_point_of_sale",
                profile.get("arca_point_of_sale") or "",
            ).strip(),
            "certificate_ref": request.form.get(
                "arca_certificate_ref",
                profile.get("arca_certificate_ref")
                or default_ref,
            ).strip(),
        }

    def _save_agent_arca_config(
        organization_id,
        agent_id,
        profile,
        *,
        test_only=False,
    ):
        from modules.arca.issuer_config import _now_iso

        form = _arca_form_values(
            profile,
            default_ref=f"agent:{agent_id}",
        )
        if not form["point_of_sale"].isdigit():
            flash_i18n(
                "billing_missing_arca_point_of_sale",
                "error",
            )
            return get_agent_billing_profile(
                organization_id,
                agent_id,
            )

        merged = dict(profile or {})
        merged.update(
            {
                "tax_id": merged.get("tax_id"),
                "arca_point_of_sale": form["point_of_sale"],
                "arca_certificate_ref": form[
                    "certificate_ref"
                ],
                "issuer_key": f"agent:{agent_id}",
                "agent_id": agent_id,
            }
        )

        if test_only:
            status, error_key = test_arca_connection(merged)
            update_agent_arca_config(
                organization_id,
                agent_id,
                arca_connection_status=status,
                arca_point_of_sale=form["point_of_sale"],
                arca_certificate_ref=form[
                    "certificate_ref"
                ],
                arca_environment="homologation",
                arca_last_validated_at=_now_iso(),
            )
            if status == "connected":
                flash_i18n(
                    "billing_arca_test_success",
                    "success",
                )
            else:
                flash_i18n(
                    error_key or "billing_arca_test_failed",
                    "error",
                )
            return get_agent_billing_profile(
                organization_id,
                agent_id,
            )

        update_agent_arca_config(
            organization_id,
            agent_id,
            arca_connection_status="configuring",
            arca_point_of_sale=form["point_of_sale"],
            arca_certificate_ref=form["certificate_ref"],
            arca_environment="homologation",
        )
        flash_i18n("invoice_profile_saved", "success")
        return get_agent_billing_profile(
            organization_id,
            agent_id,
        )

    def _save_issuer_arca_config(
        organization_id,
        profile_id,
        profile,
        *,
        test_only=False,
    ):
        from modules.arca.issuer_config import _now_iso

        form = _arca_form_values(
            profile,
            default_ref=f"issuer:{profile_id}",
        )
        if not form["point_of_sale"].isdigit():
            flash_i18n(
                "billing_missing_arca_point_of_sale",
                "error",
            )
            return get_billing_issuer_profile(
                organization_id,
                profile_id,
            )

        merged = dict(profile or {})
        merged.update(
            {
                "tax_id": merged.get("tax_id"),
                "arca_point_of_sale": form["point_of_sale"],
                "arca_certificate_ref": form[
                    "certificate_ref"
                ],
                "issuer_key": f"issuer:{profile_id}",
                "id": profile_id,
            }
        )

        if test_only:
            status, error_key = test_arca_connection(merged)
            update_issuer_arca_config(
                organization_id,
                profile_id,
                arca_connection_status=status,
                arca_point_of_sale=form["point_of_sale"],
                arca_certificate_ref=form[
                    "certificate_ref"
                ],
                arca_environment="homologation",
                arca_last_validated_at=_now_iso(),
            )
            if status == "connected":
                flash_i18n(
                    "billing_arca_test_success",
                    "success",
                )
            else:
                flash_i18n(
                    error_key or "billing_arca_test_failed",
                    "error",
                )
            return get_billing_issuer_profile(
                organization_id,
                profile_id,
            )

        update_issuer_arca_config(
            organization_id,
            profile_id,
            arca_connection_status="configuring",
            arca_point_of_sale=form["point_of_sale"],
            arca_certificate_ref=form["certificate_ref"],
            arca_environment="homologation",
        )
        flash_i18n("invoice_profile_saved", "success")
        return get_billing_issuer_profile(
            organization_id,
            profile_id,
        )

    @app.route(
        "/billing/agent-profile",
        methods=["GET", "POST"],
    )
    @login_required
    def billing_agent_profile_self():
        user = require_billing_user()
        if not is_agent(user) or not user.get("agent_id"):
            abort(403)
        return _agent_profile_view(
            user["agent_id"],
            user,
        )

    @app.route(
        "/billing/agent-profile/<int:agent_id>",
        methods=["GET", "POST"],
    )
    @login_required
    def billing_agent_profile(agent_id):
        user = require_billing_user()
        if is_admin(user):
            return _agent_profile_view(agent_id, user)
        if is_agent(user) and user.get("agent_id") == agent_id:
            return _agent_profile_view(agent_id, user)
        abort(403)

    def _agent_profile_view(agent_id, user):
        organization_id = require_user_organization()
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)
        can_edit = is_admin(user)
        if request.method == "POST" and not can_edit:
            abort(403)
        next_url = get_safe_redirect_target(
            request.values.get("next")
        )
        form_values = _profile_form(
            agent_id,
            organization_id,
        )
        errors = []

        if request.method == "POST":
            action = request.form.get("action", "save")
            if action in ("arca_save", "arca_test") and is_arca_fiscal_enabled():
                return redirect(url_for("settings_arca"))

            validation = validate_agent_billing_profile(
                form_values,
                require_email=True,
            )
            errors.extend(validation["missing_i18n_keys"])

            if not errors:
                upsert_agent_billing_profile(
                    organization_id,
                    agent_id,
                    legal_name=form_values["legal_name"],
                    tax_id=form_values["tax_id"],
                    tax_condition=form_values[
                        "tax_condition"
                    ],
                    fiscal_address=form_values[
                        "fiscal_address"
                    ],
                    email=form_values["email"],
                )
                flash_i18n(
                    "invoice_profile_saved",
                    "success",
                )
                return redirect(
                    next_url
                    or url_for(
                        "billing_agent_profile",
                        agent_id=agent_id,
                        _anchor="datos-fiscales",
                    )
                )

        profile = get_agent_billing_profile(
            organization_id,
            agent_id,
        )
        ready, missing = agent_billing_ready(profile)
        settings = get_organization_settings(
            organization_id
        )
        org_ready, org_missing = org_billing_ready(settings)
        arca_form = _arca_form_values(
            profile,
            default_ref=f"agent:{agent_id}",
        )

        return render_template(
            "billing/agent_profile.html",
            agent_id=agent_id,
            form_values=form_values,
            errors=errors,
            tax_conditions=TAX_CONDITIONS,
            profile_ready=ready,
            missing=missing if not ready else [],
            org_ready=org_ready,
            org_missing=org_missing,
            arca_display=build_arca_display(
                profile,
                user=user,
                organization_id=organization_id,
            ),
            arca_form=arca_form,
            agent=agent,
            can_edit=can_edit,
            next_url=next_url,
            back_url=(
                next_url
                or url_for(
                    "agents_detail",
                    agent_id=agent_id,
                    _anchor="datos-fiscales",
                )
            ),
        )
