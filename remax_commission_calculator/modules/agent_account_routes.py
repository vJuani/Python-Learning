"""
Agent current account (cuenta corriente) HTTP routes.
"""

from __future__ import annotations

import uuid
from datetime import date

from flask import (
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from modules.agent_account import (
    ADJUSTMENT_DIRECTIONS,
    AgentAccountError,
    MOVEMENT_TYPES,
    build_agent_detail_view,
    build_my_account_view,
    build_staff_index_view,
    cancel_movement,
    create_movement,
)
from modules.agent_account_presentation import (
    PAYMENT_METHODS,
    format_pending_charge_option,
)
from modules.agent_account_charges import (
    CHARGE_CATEGORIES,
    DEFAULT_VAT_RATE,
    RECURRENCE_TYPES,
    VAT_MODES,
    charge_category_label_key,
)
from modules.agent_payment_ai_service import (
    AgentPaymentAiError,
    build_review_context,
    confirm_agent_payment_draft,
    discard_draft,
    retry_agent_payment_analysis,
    start_agent_payment_analysis,
    update_draft_from_form,
)
from modules.cash_receipts import absolute_receipt_path
from modules.auth import (
    admin_required,
    get_current_user,
    is_admin,
    login_required,
)
from modules.database import (
    get_agent_account_movement,
    get_agent_record,
)
from modules.database.agent_account_repository import (
    CURRENCIES,
    STATUS_CONFIRMED,
    STATUS_REVERSED,
    list_pending_charges,
)
from modules.database.agent_payment_ai_drafts_repository import (
    get_agent_payment_ai_draft,
)
from modules.database.operations_repository import (
    search_operations_for_agent_account,
)
from modules.i18n import translate
from modules.recurring_agent_charges import (
    RecurringChargeError,
    build_due_preview,
    create_recurring_charge,
    end_recurring_charge,
    generate_due_recurring_charges,
    list_recurring_charges,
    pause_recurring_charge,
    resume_recurring_charge,
    update_recurring_charge,
)
from modules.database.recurring_charges_repository import (
    get_recurring_charge,
)


def register_agent_account_routes(app, helpers):
    require_user_organization = helpers[
        "require_user_organization"
    ]
    flash_i18n = helpers["flash_i18n"]
    get_current_language = helpers["get_current_language"]

    def _require_admin_organization():
        organization_id = require_user_organization()
        if not is_admin():
            abort(403)
        return organization_id

    def _require_agent_account_access(agent_id):
        organization_id = require_user_organization()
        current_user = get_current_user()
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)

        if is_admin(current_user):
            return organization_id, agent, current_user

        linked_id = current_user.get("agent_id")
        if linked_id != agent_id:
            abort(403)

        return organization_id, agent, current_user

    def _parse_detail_filters():
        currency = request.args.get(
            "currency",
            "",
        ).strip().upper()
        movement_type = request.args.get(
            "movement_type",
            "",
        ).strip()
        status = request.args.get("status", "").strip()
        return {
            "currency": (
                currency if currency in CURRENCIES else ""
            ),
            "movement_type": (
                movement_type
                if movement_type in MOVEMENT_TYPES
                else ""
            ),
            "status": (
                status
                if status
                in (STATUS_CONFIRMED, STATUS_REVERSED)
                else ""
            ),
            "date_from": request.args.get(
                "date_from",
                "",
            ).strip(),
            "date_to": request.args.get(
                "date_to",
                "",
            ).strip(),
            "show_cancelled": request.args.get(
                "show_cancelled"
            )
            == "1",
        }

    @app.route("/agent-accounts")
    @admin_required
    def agent_account_index():
        organization_id = _require_admin_organization()
        search_query = request.args.get("q", "").strip()
        language = get_current_language()
        panel = build_staff_index_view(
            organization_id,
            search_query=search_query or None,
            language=language,
        )

        return render_template(
            "agent_account/index.html",
            panel=panel,
        )

    @app.route("/agent-accounts/<int:agent_id>")
    @login_required
    def agent_account_detail(agent_id):
        organization_id, agent, current_user = (
            _require_agent_account_access(agent_id)
        )
        if not is_admin(current_user):
            return redirect(url_for("my_agent_account"))

        language = get_current_language()
        filters = _parse_detail_filters()
        detail = build_agent_detail_view(
            organization_id,
            agent_id,
            filters=filters,
            language=language,
        )
        filters_active = any(
            value
            for key, value in filters.items()
            if key != "show_cancelled" and value
        ) or filters.get("show_cancelled")

        return render_template(
            "agent_account/detail.html",
            agent=agent,
            detail=detail,
            filters=filters,
            filters_active=filters_active,
            movement_types=MOVEMENT_TYPES,
            currencies=CURRENCIES,
            payment_methods=PAYMENT_METHODS,
            adjustment_directions=ADJUSTMENT_DIRECTIONS,
            can_manage=True,
            form_idempotency_key=str(uuid.uuid4()),
            today_iso=date.today().isoformat(),
            default_movement_type=request.args.get(
                "type",
                "payment",
            ),
            charge_categories=[
                {
                    "id": category,
                    "label_key": charge_category_label_key(
                        category
                    ),
                }
                for category in CHARGE_CATEGORIES
            ],
            vat_modes=VAT_MODES,
            recurrence_types=RECURRENCE_TYPES,
            default_vat_rate_percent=float(
                DEFAULT_VAT_RATE * 100
            ),
            recurring_charges=list_recurring_charges(
                organization_id,
                agent_id=agent_id,
            ),
        )

    def _recurring_form_values(recurring=None):
        recurring = recurring or {}
        vat_rate = float(
            recurring.get("vat_rate") or DEFAULT_VAT_RATE
        ) * 100
        return {
            "charge_category": request.form.get(
                "charge_category",
                recurring.get("charge_category", "fee"),
            ),
            "currency": request.form.get(
                "currency",
                recurring.get("currency", "USD"),
            ),
            "amount": request.form.get(
                "amount",
                recurring.get("input_amount", ""),
            ),
            "vat_mode": request.form.get(
                "vat_mode",
                recurring.get("vat_mode", "add_vat"),
            ),
            "vat_rate": request.form.get("vat_rate", vat_rate),
            "description": request.form.get(
                "description",
                recurring.get("description", ""),
            ),
            "recurrence_type": request.form.get(
                "recurrence_type",
                recurring.get("recurrence_type", "monthly"),
            ),
            "billing_day": request.form.get(
                "billing_day",
                recurring.get("billing_day", 1),
            ),
            "start_date": request.form.get(
                "start_date",
                recurring.get("start_date", date.today().isoformat()),
            ),
            "end_date": request.form.get(
                "end_date",
                recurring.get("end_date", ""),
            ),
        }

    def _render_recurring_form(
        agent,
        form_values,
        *,
        recurring=None,
        errors=None,
    ):
        return render_template(
            "agent_account/recurring_charge_form.html",
            agent=agent,
            recurring=recurring,
            form_values=form_values,
            errors=errors or [],
            charge_categories=[
                {
                    "id": category,
                    "label_key": charge_category_label_key(category),
                }
                for category in CHARGE_CATEGORIES
            ],
            currencies=CURRENCIES,
            vat_modes=VAT_MODES,
            recurrence_types=(
                recurrence
                for recurrence in RECURRENCE_TYPES
                if recurrence in ("monthly", "annual")
            ),
            default_vat_rate_percent=float(
                DEFAULT_VAT_RATE * 100
            ),
        )

    @app.route(
        "/agent-accounts/<int:agent_id>/recurring-charges/new",
        methods=["GET", "POST"],
    )
    @admin_required
    def agent_recurring_charge_new(agent_id):
        organization_id = _require_admin_organization()
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)
        form_values = _recurring_form_values()
        if request.method == "POST":
            try:
                create_recurring_charge(
                    organization_id,
                    agent_id,
                    form_values,
                    actor_user_id=get_current_user()["id"],
                    language=get_current_language(),
                )
                flash_i18n("agent_recurring_created", "success")
                return redirect(
                    url_for(
                        "agent_account_detail",
                        agent_id=agent_id,
                        _anchor="recurring-charges",
                    )
                )
            except RecurringChargeError as error:
                return _render_recurring_form(
                    agent,
                    form_values,
                    errors=[error.message_key],
                )
        return _render_recurring_form(agent, form_values)

    @app.route(
        "/agent-accounts/recurring-charges/<int:recurring_id>/edit",
        methods=["GET", "POST"],
    )
    @admin_required
    def agent_recurring_charge_edit(recurring_id):
        organization_id = _require_admin_organization()
        recurring = get_recurring_charge(
            organization_id,
            recurring_id,
        )
        if recurring is None:
            abort(404)
        agent = get_agent_record(
            recurring["agent_id"],
            organization_id,
        )
        if agent is None:
            abort(404)
        form_values = _recurring_form_values(recurring)
        if request.method == "POST":
            try:
                update_recurring_charge(
                    organization_id,
                    recurring_id,
                    form_values,
                    actor_user_id=get_current_user()["id"],
                    language=get_current_language(),
                )
                flash_i18n("agent_recurring_updated", "success")
                return redirect(
                    url_for(
                        "agent_account_detail",
                        agent_id=recurring["agent_id"],
                        _anchor="recurring-charges",
                    )
                )
            except RecurringChargeError as error:
                return _render_recurring_form(
                    agent,
                    form_values,
                    recurring=recurring,
                    errors=[error.message_key],
                )
        return _render_recurring_form(
            agent,
            form_values,
            recurring=recurring,
        )

    def _recurring_status_action(recurring_id, action):
        organization_id = _require_admin_organization()
        recurring = get_recurring_charge(
            organization_id,
            recurring_id,
        )
        if recurring is None:
            abort(404)
        actor_user_id = get_current_user()["id"]
        if action == "pause":
            pause_recurring_charge(
                organization_id,
                recurring_id,
                actor_user_id=actor_user_id,
            )
            key = "agent_recurring_paused"
        elif action == "resume":
            resume_recurring_charge(
                organization_id,
                recurring_id,
                actor_user_id=actor_user_id,
            )
            key = "agent_recurring_resumed"
        else:
            end_recurring_charge(
                organization_id,
                recurring_id,
                actor_user_id=actor_user_id,
            )
            key = "agent_recurring_ended"
        flash_i18n(key, "success")
        return redirect(
            url_for(
                "agent_account_detail",
                agent_id=recurring["agent_id"],
                _anchor="recurring-charges",
            )
        )

    @app.route(
        "/agent-accounts/recurring-charges/<int:recurring_id>/pause",
        methods=["POST"],
    )
    @admin_required
    def agent_recurring_charge_pause(recurring_id):
        return _recurring_status_action(recurring_id, "pause")

    @app.route(
        "/agent-accounts/recurring-charges/<int:recurring_id>/resume",
        methods=["POST"],
    )
    @admin_required
    def agent_recurring_charge_resume(recurring_id):
        return _recurring_status_action(recurring_id, "resume")

    @app.route(
        "/agent-accounts/recurring-charges/<int:recurring_id>/end",
        methods=["POST"],
    )
    @admin_required
    def agent_recurring_charge_end(recurring_id):
        return _recurring_status_action(recurring_id, "end")

    @app.route(
        "/agent-accounts/recurring-charges/generate",
        methods=["GET", "POST"],
    )
    @admin_required
    def agent_recurring_generate():
        organization_id = _require_admin_organization()
        as_of = request.values.get(
            "as_of",
            date.today().isoformat(),
        )
        try:
            preview = build_due_preview(
                organization_id,
                as_of=as_of,
                language=get_current_language(),
            )
            if request.method == "POST":
                result = generate_due_recurring_charges(
                    organization_id,
                    as_of=as_of,
                    actor_user_id=get_current_user()["id"],
                    language=get_current_language(),
                )
                flash_i18n(
                    "agent_recurring_generated_count",
                    "success",
                )
                return redirect(
                    url_for("agent_recurring_generate", as_of=as_of)
                )
        except RecurringChargeError as error:
            flash_i18n(error.message_key, "error")
            preview = []
        return render_template(
            "agent_account/recurring_generate.html",
            preview=preview,
            as_of=as_of,
        )

    def _create_movement_from_form(agent_id):
        organization_id = _require_admin_organization()
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)

        current_user = get_current_user()
        payload = {
            key: request.form.get(key)
            for key in (
                "movement_type",
                "currency",
                "amount",
                "description",
                "movement_date",
                "adjustment_direction",
                "exchange_rate",
                "exchange_rate_date",
                "exchange_rate_source",
                "payment_method",
                "reference_text",
                "notes",
                "period_label",
                "operation_reference",
                "charge_category",
                "vat_mode",
                "vat_rate",
                "billing_period",
                "recurring",
                "recurrence_type",
                "applied_to_movement_id",
                "operation_id",
                "treasury_account_id",
            )
        }
        idempotency_key = (
            request.form.get("idempotency_key") or ""
        ).strip() or None

        try:
            create_movement(
                organization_id,
                agent_id,
                payload,
                created_by_user_id=current_user["id"],
                idempotency_key=idempotency_key,
                language=get_current_language(),
            )
            flash_i18n(
                "agent_account_flash_created",
                "success",
            )
        except AgentAccountError as error:
            flash_i18n(error.message_key, "error")

        return redirect(
            url_for(
                "agent_account_detail",
                agent_id=agent_id,
            )
        )

    @app.route(
        "/agent-accounts/<int:agent_id>/pending-charges",
        methods=["GET"],
    )
    @admin_required
    def agent_account_pending_charges(agent_id):
        organization_id = _require_admin_organization()
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)

        currency = request.args.get(
            "currency",
            "",
        ).strip().upper()
        if currency not in CURRENCIES:
            abort(400)

        language = get_current_language()
        charges = [
            format_pending_charge_option(
                charge,
                language=language,
            )
            for charge in list_pending_charges(
                organization_id,
                agent_id,
                currency,
            )
        ]
        return jsonify({"charges": charges})

    @app.route(
        "/agent-accounts/treasury-accounts",
        methods=["GET"],
    )
    @admin_required
    def agent_account_treasury_accounts():
        organization_id = _require_admin_organization()
        currency = request.args.get(
            "currency",
            "",
        ).strip().upper()
        from modules.database.treasury_accounts_repository import (
            list_treasury_accounts,
            suggest_treasury_account_for_payment,
        )

        payment_method = request.args.get(
            "payment_method",
            "",
        ).strip()
        accounts = list_treasury_accounts(
            organization_id,
            currency=currency if currency in CURRENCIES else None,
            active_only=True,
        )
        suggested = None
        if currency in CURRENCIES and payment_method:
            suggested = suggest_treasury_account_for_payment(
                organization_id,
                currency,
                payment_method,
            )

        return jsonify(
            {
                "accounts": accounts,
                "suggested_id": (
                    suggested["id"] if suggested else None
                ),
            }
        )

    @app.route(
        "/agent-accounts/<int:agent_id>/operations/search",
        methods=["GET"],
    )
    @admin_required
    def agent_account_search_operations(agent_id):
        organization_id = _require_admin_organization()
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)

        query = request.args.get("q", "").strip()
        operations = search_operations_for_agent_account(
            organization_id,
            agent_id,
            query,
            limit=15,
        )
        return jsonify(
            {
                "operations": [
                    {
                        "id": operation["db_id"],
                        "display_id": operation["id"],
                        "label": (
                            f"{operation['id']} · "
                            f"{operation.get('property') or '—'} · "
                            f"{operation.get('date') or '—'}"
                        ),
                        "agent_payment": operation.get(
                            "agent_payment"
                        ),
                        "currency": operation.get(
                            "currency"
                        )
                        or "USD",
                    }
                    for operation in operations
                ]
            }
        )

    @app.route(
        "/agent-accounts/<int:agent_id>/movements",
        methods=["POST"],
    )
    @admin_required
    def agent_account_create_movement(agent_id):
        return _create_movement_from_form(agent_id)

    def _render_payment_ai(
        organization_id,
        draft=None,
        *,
        preselected_agent_id=None,
        errors=None,
        form_values=None,
    ):
        context = {
            "draft": draft,
            "errors": errors or [],
            "form_values": form_values
            or {"user_context_text": ""},
            "preselected_agent_id": preselected_agent_id,
            "agents": build_staff_index_view(
                organization_id,
                language=get_current_language(),
            )["agents"],
        }

        if draft is not None:
            context.update(
                build_review_context(organization_id, draft)
            )

        return render_template(
            "agent_account/ai_payment.html",
            **context,
        )

    def _load_ai_draft_or_404(organization_id, draft_id):
        draft = get_agent_payment_ai_draft(
            draft_id,
            organization_id,
        )
        if draft is None:
            abort(404)
        return draft

    @app.route(
        "/agent-accounts/ai/payments",
        methods=["GET", "POST"],
    )
    @admin_required
    def agent_payment_ai_new():
        organization_id = _require_admin_organization()
        current_user = get_current_user()
        preselected_agent_id = None
        raw_agent_id = (
            request.values.get("agent_id") or ""
        ).strip()

        if raw_agent_id:
            try:
                preselected_agent_id = int(raw_agent_id)
            except ValueError:
                preselected_agent_id = None

        if (
            preselected_agent_id is not None
            and get_agent_record(
                preselected_agent_id,
                organization_id,
            )
            is None
        ):
            abort(404)

        if request.method == "GET":
            return _render_payment_ai(
                organization_id,
                preselected_agent_id=preselected_agent_id,
            )

        context_text = request.form.get(
            "user_context_text",
            "",
        ).strip()

        try:
            draft = start_agent_payment_analysis(
                organization_id,
                user_id=current_user["id"],
                file_storage=request.files.get("receipt"),
                user_context_text=context_text,
                agent_id=preselected_agent_id,
                language=get_current_language(),
            )
        except AgentPaymentAiError as error:
            flash_i18n(error.message_key, "error")
            return _render_payment_ai(
                organization_id,
                preselected_agent_id=preselected_agent_id,
                errors=[error.message_key],
                form_values={
                    "user_context_text": context_text,
                },
            )

        return redirect(
            url_for(
                "agent_payment_ai_review",
                draft_id=draft["id"],
            )
        )

    def _payment_ai_form_values():
        return {
            key: (request.form.get(key) or "").strip()
            for key in (
                "amount",
                "currency",
                "payment_date",
                "payment_method",
                "bank_name",
                "reference_number",
                "sender_name",
                "description",
                "notes",
                "exchange_rate",
                "agent_id",
                "treasury_account_id",
                "charge_movement_id",
                "apply_mode",
            )
        }

    @app.route(
        "/agent-accounts/ai/payments/<int:draft_id>",
        methods=["GET", "POST"],
    )
    @admin_required
    def agent_payment_ai_review(draft_id):
        organization_id = _require_admin_organization()
        draft = _load_ai_draft_or_404(
            organization_id,
            draft_id,
        )

        if request.method == "GET":
            return _render_payment_ai(organization_id, draft)

        action = request.form.get("action", "save")
        form_values = _payment_ai_form_values()

        if action == "discard":
            try:
                discard_draft(organization_id, draft_id)
                flash_i18n(
                    "agent_payment_ai_discarded",
                    "success",
                )
            except AgentPaymentAiError as error:
                flash_i18n(error.message_key, "error")
            return redirect(
                url_for("agent_payment_ai_new")
            )

        if action == "save":
            try:
                draft = update_draft_from_form(
                    organization_id,
                    draft_id,
                    form_values,
                )
                flash_i18n(
                    "agent_payment_ai_draft_updated",
                    "success",
                )
            except AgentPaymentAiError as error:
                flash_i18n(error.message_key, "error")
            return _render_payment_ai(organization_id, draft)

        if action != "confirm":
            flash_i18n(
                "agent_payment_ai_err_confirm_action",
                "error",
            )
            return _render_payment_ai(organization_id, draft)

        try:
            movement = confirm_agent_payment_draft(
                organization_id,
                draft_id,
                user_id=get_current_user()["id"],
                confirm_token=request.form.get(
                    "confirm_token",
                    "",
                ),
                form_values=form_values,
                language=get_current_language(),
            )
        except (
            AgentPaymentAiError,
            AgentAccountError,
        ) as error:
            flash_i18n(error.message_key, "error")
            draft = _load_ai_draft_or_404(
                organization_id,
                draft_id,
            )
            return _render_payment_ai(
                organization_id,
                draft,
                errors=[error.message_key],
            )

        flash_i18n("agent_payment_ai_confirmed", "success")

        return redirect(
            url_for(
                "agent_account_detail",
                agent_id=movement["agent_id"],
            )
        )

    @app.route(
        "/agent-accounts/ai/payments/<int:draft_id>/retry",
        methods=["POST"],
    )
    @admin_required
    def agent_payment_ai_retry(draft_id):
        organization_id = _require_admin_organization()
        _load_ai_draft_or_404(organization_id, draft_id)

        try:
            retry_agent_payment_analysis(
                organization_id,
                draft_id,
                language=get_current_language(),
            )
        except AgentPaymentAiError as error:
            flash_i18n(error.message_key, "error")

        return redirect(
            url_for(
                "agent_payment_ai_review",
                draft_id=draft_id,
            )
        )

    @app.route(
        "/agent-accounts/ai/payments/<int:draft_id>/receipt",
        methods=["GET"],
    )
    @admin_required
    def agent_payment_ai_receipt(draft_id):
        organization_id = _require_admin_organization()
        draft = _load_ai_draft_or_404(
            organization_id,
            draft_id,
        )

        if not draft.get("attachment_path"):
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
            as_attachment=(
                request.args.get("download") == "1"
            ),
        )

    def _cancel_movement_from_form(movement_id):
        organization_id = _require_admin_organization()
        current_user = get_current_user()
        reason = request.form.get(
            "cancellation_reason",
            request.form.get("reversal_reason", ""),
        )

        movement = get_agent_account_movement(
            movement_id,
            organization_id,
        )
        if movement is None:
            abort(404)

        try:
            cancel_movement(
                organization_id,
                movement_id,
                created_by_user_id=current_user["id"],
                reason=reason,
            )
            flash_i18n(
                "agent_account_flash_cancelled",
                "success",
            )
        except AgentAccountError as error:
            flash_i18n(error.message_key, "error")

        return redirect(
            url_for(
                "agent_account_detail",
                agent_id=movement["agent_id"],
            )
        )

    @app.route(
        "/agent-accounts/movements/<int:movement_id>/cancel",
        methods=["POST"],
    )
    @admin_required
    def agent_account_cancel_movement(movement_id):
        return _cancel_movement_from_form(movement_id)

    @app.route(
        "/agent-accounts/movements/<int:movement_id>/reverse",
        methods=["POST"],
    )
    @admin_required
    def agent_account_reverse_movement(movement_id):
        return _cancel_movement_from_form(movement_id)

    @app.route("/my-account")
    @login_required
    def my_agent_account():
        organization_id = require_user_organization()
        current_user = get_current_user()
        agent_id = current_user.get("agent_id")

        if agent_id is None:
            flash_i18n("agent_account_no_linked_agent", "error")
            return redirect(url_for("dashboard"))

        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)

        language = get_current_language()
        filters = _parse_detail_filters()
        detail = build_my_account_view(
            organization_id,
            agent_id,
            filters=filters,
            language=language,
        )
        filters_active = any(
            value
            for key, value in filters.items()
            if key != "show_cancelled" and value
        ) or filters.get("show_cancelled")

        return render_template(
            "agent_account/my_account.html",
            agent=agent,
            detail=detail,
            filters=filters,
            filters_active=filters_active,
            movement_types=MOVEMENT_TYPES,
            currencies=CURRENCIES,
            recurring_charges=list_recurring_charges(
                organization_id,
                agent_id=agent_id,
                include_ended=False,
            ),
        )
