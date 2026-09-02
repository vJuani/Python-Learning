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
from modules.database.operations_repository import (
    search_operations_for_agent_account,
)
from modules.i18n import translate


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
        )
