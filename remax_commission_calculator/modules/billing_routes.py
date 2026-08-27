"""
Billing / Facturación HTTP routes (multi-side, multi-issuer).
"""

from __future__ import annotations

import io

from flask import (
    abort,
    redirect,
    render_template,
    request,
    send_file,
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
    get_agents,
    get_operation_party,
    get_operation_record,
    get_parties_for_operation,
    list_billing_issuer_profiles,
    list_invoices_for_operation,
    set_operation_party_client_fields,
    upsert_agent_billing_profile,
    upsert_billing_issuer_profile,
    deactivate_billing_issuer_profile,
    set_default_billing_issuer_profile,
    get_billing_issuer_profile,
)
from modules.database.organization_settings_repository import (
    get_organization_settings,
    update_organization_billing_fields,
)
from modules.invoicing import (
    InvoicingError,
    PAYMENT_CONDITIONS,
    SIDE_BUYER,
    SIDE_SELLER,
    TAX_CONDITIONS,
    VALID_SIDES,
    agent_billing_ready,
    billing_kpis,
    build_draft_preview_for_side,
    cancel_invoice,
    confirm_draft,
    create_draft_for_side,
    generate_draft_pdf_bytes,
    get_invoice,
    get_operation_sides_state,
    list_invoices,
    list_pending_operations,
    org_billing_ready,
    set_party_invoice_amount,
    update_draft_options,
    validate_cuit,
)


def register_billing_routes(app, *, helpers):
    require_user_organization = helpers[
        "require_user_organization"
    ]
    require_billing_user = helpers["require_billing_user"]
    flash_i18n = helpers["flash_i18n"]
    ensure_operation_scope = helpers[
        "ensure_operation_scope"
    ]
    flash_invoicing_error = helpers[
        "_flash_invoicing_error"
    ]
    load_billing_invoice = helpers[
        "_load_billing_invoice"
    ]

    def _valid_side(side):
        if side not in VALID_SIDES:
            abort(404)
        return side

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

        filters = {
            "agent_id": request.args.get("agent_id", "").strip(),
            "status": request.args.get("status", "").strip(),
            "operation_id": request.args.get(
                "operation_id", ""
            ).strip(),
            "side": request.args.get("side", "").strip(),
            "payment_condition": request.args.get(
                "payment_condition", ""
            ).strip(),
            "q": request.args.get("q", "").strip(),
        }

        list_kwargs = {"agent_id": agent_scope}
        if is_admin(user) and filters["agent_id"].isdigit():
            list_kwargs["agent_id"] = int(filters["agent_id"])
        if filters["status"]:
            list_kwargs["status"] = filters["status"]
        if filters["operation_id"].isdigit():
            list_kwargs["operation_id"] = int(
                filters["operation_id"]
            )
        if filters["side"] in VALID_SIDES:
            list_kwargs["side"] = filters["side"]
        if filters["payment_condition"]:
            list_kwargs["payment_condition"] = filters[
                "payment_condition"
            ]
        if filters["q"]:
            list_kwargs["q"] = filters["q"]

        return render_template(
            "billing/list.html",
            invoices=list_invoices(
                organization_id,
                **list_kwargs,
            ),
            kpis=billing_kpis(
                organization_id,
                agent_id=agent_scope,
            ),
            pending_operations=list_pending_operations(
                organization_id,
                agent_id=agent_scope,
            ),
            filters=filters,
            agents=(
                get_agents(organization_id)
                if is_admin(user)
                else []
            ),
            payment_conditions=PAYMENT_CONDITIONS,
            is_staff=is_admin(user),
        )

    @app.route(
        "/billing/operations/<int:operation_id>/new",
    )
    @login_required
    def billing_new_from_operation(operation_id):
        """Legacy URL: send user back to operation sides."""
        require_billing_user()
        return redirect(
            url_for(
                "operations_detail",
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
        issuer_profiles = list_billing_issuer_profiles(
            organization_id,
            active_only=True,
        )
        payment_condition = request.form.get(
            "payment_condition"
        ) or request.args.get("payment_condition")
        issue_date = request.form.get(
            "issue_date"
        ) or request.args.get("issue_date")
        issuer_mode = (
            request.form.get("issuer_mode")
            or request.args.get("issuer_mode")
            or (
                "agent"
                if is_agent(user) and not is_admin(user)
                else "agent"
            )
        )
        issuer_profile_id = request.form.get(
            "issuer_profile_id"
        ) or request.args.get("issuer_profile_id")
        if issuer_profile_id and str(
            issuer_profile_id
        ).isdigit():
            issuer_profile_id = int(issuer_profile_id)
        else:
            issuer_profile_id = settings.get(
                "default_issuer_profile_id"
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
                    payment_condition=payment_condition
                    or None,
                    issue_date=issue_date or None,
                )
            except InvoicingError as error:
                flash_invoicing_error(error)
                if error.message_key == (
                    "invoice_err_billing_profile_incomplete"
                ):
                    return redirect(
                        url_for("billing_agent_profile_self")
                    )
                return redirect(
                    url_for(
                        "billing_new_for_side",
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

        try:
            preview = build_draft_preview_for_side(
                organization_id,
                operation_id,
                side,
                user,
                issuer_mode=issuer_mode,
                issuer_profile_id=issuer_profile_id,
                payment_condition=payment_condition,
                issue_date=issue_date,
            )
        except InvoicingError as error:
            flash_invoicing_error(error)
            if error.message_key == (
                "invoice_err_billing_profile_incomplete"
            ):
                return redirect(
                    url_for("billing_agent_profile_self")
                )
            return redirect(
                url_for(
                    "operations_detail",
                    operation_id=operation_id,
                )
            )

        return render_template(
            "billing/form_new.html",
            operation=operation,
            side=side,
            preview=preview,
            payment_conditions=PAYMENT_CONDITIONS,
            issuer_profiles=issuer_profiles,
            issuer_mode=issuer_mode,
            issuer_profile_id=issuer_profile_id,
            settings=settings,
            is_staff=is_admin(user),
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
                    flash_invoicing_error(error)
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
                flash_invoicing_error(error)
                return redirect(
                    url_for(
                        "billing_review",
                        invoice_id=invoice_id,
                    )
                )
            return redirect(
                url_for(
                    "billing_detail",
                    invoice_id=invoice_id,
                )
            )

        return render_template(
            "billing/review.html",
            invoice=invoice,
            payment_conditions=PAYMENT_CONDITIONS,
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
        if invoice.get("operation_id"):
            operation = get_operation_record(
                invoice["operation_id"],
                organization_id,
            )
        return render_template(
            "billing/detail.html",
            invoice=invoice,
            operation=operation,
        )

    @app.route(
        "/billing/<int:invoice_id>/cancel",
        methods=["POST"],
    )
    @login_required
    def billing_cancel(invoice_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        try:
            cancel_invoice(
                organization_id,
                invoice_id,
                user,
                reason=request.form.get("reason", ""),
            )
            flash_i18n("invoice_cancelled", "success")
        except InvoicingError as error:
            flash_invoicing_error(error)
        return redirect(
            url_for("billing_detail", invoice_id=invoice_id)
        )

    @app.route("/billing/<int:invoice_id>/pdf")
    @login_required
    def billing_pdf(invoice_id):
        user = require_billing_user()
        organization_id = require_user_organization()
        try:
            pdf_bytes = generate_draft_pdf_bytes(
                organization_id,
                invoice_id,
                user,
            )
        except InvoicingError as error:
            flash_invoicing_error(error)
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
            flash_invoicing_error(error)
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
            flash_invoicing_error(error)
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
            profiles=list_billing_issuer_profiles(
                organization_id,
                active_only=False,
            ),
            tax_conditions=TAX_CONDITIONS,
        )

    def _profile_form(agent_id, organization_id):
        profile = get_agent_billing_profile(
            organization_id,
            agent_id,
        ) or {}
        return {
            "legal_name": request.form.get(
                "legal_name",
                profile.get("legal_name", ""),
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
                profile.get("email", ""),
            ).strip(),
        }

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
        form_values = _profile_form(
            agent_id,
            organization_id,
        )
        errors = []

        if request.method == "POST":
            if not form_values["legal_name"]:
                errors.append(
                    "billing_missing_agent_legal_name"
                )
            if not validate_cuit(form_values["tax_id"]):
                errors.append(
                    "billing_missing_agent_tax_id"
                )
            if form_values["tax_condition"] not in TAX_CONDITIONS:
                errors.append(
                    "billing_missing_agent_tax_condition"
                )
            if not form_values["fiscal_address"]:
                errors.append(
                    "billing_missing_agent_fiscal_address"
                )

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
                    url_for(
                        "billing_agent_profile",
                        agent_id=agent_id,
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
        )
