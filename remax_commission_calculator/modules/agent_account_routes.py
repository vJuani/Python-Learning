"""
Agent current account (cuenta corriente) HTTP routes.
"""

from __future__ import annotations

import uuid
from datetime import date

from flask import (
    abort,
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
    create_movement,
    movement_signed_display,
    reverse_movement,
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
    get_agents,
)
from modules.database.agent_account_repository import CURRENCIES


def register_agent_account_routes(app, helpers):
    require_user_organization = helpers[
        "require_user_organization"
    ]
    flash_i18n = helpers["flash_i18n"]

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
        return {
            "currency": (
                currency if currency in CURRENCIES else ""
            ),
            "movement_type": (
                movement_type
                if movement_type in MOVEMENT_TYPES
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
        }

    @app.route("/agent-accounts")
    @admin_required
    def agent_account_index():
        organization_id = _require_admin_organization()
        search_query = request.args.get("q", "").strip()
        panel = build_staff_index_view(
            organization_id,
            search_query=search_query or None,
        )

        return render_template(
            "agent_account/index.html",
            panel=panel,
            agents=get_agents(organization_id),
        )

    @app.route("/agent-accounts/<int:agent_id>")
    @login_required
    def agent_account_detail(agent_id):
        organization_id, agent, current_user = (
            _require_agent_account_access(agent_id)
        )
        if not is_admin(current_user):
            return redirect(url_for("my_agent_account"))

        filters = _parse_detail_filters()
        detail = build_agent_detail_view(
            organization_id,
            agent_id,
            filters=filters,
        )
        filters_active = any(filters.values())

        return render_template(
            "agent_account/detail.html",
            agent=agent,
            detail=detail,
            filters=filters,
            filters_active=filters_active,
            movement_types=MOVEMENT_TYPES,
            currencies=CURRENCIES,
            adjustment_directions=ADJUSTMENT_DIRECTIONS,
            movement_signed_display=movement_signed_display,
            can_manage=True,
            form_idempotency_key=str(uuid.uuid4()),
            today_iso=date.today().isoformat(),
        )

    @app.route(
        "/agent-accounts/<int:agent_id>/movements",
        methods=["POST"],
    )
    @admin_required
    def agent_account_create_movement(agent_id):
        organization_id = _require_admin_organization()
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)

        current_user = get_current_user()
        payload = {
            "movement_type": request.form.get(
                "movement_type"
            ),
            "currency": request.form.get("currency"),
            "amount": request.form.get("amount"),
            "description": request.form.get(
                "description"
            ),
            "movement_date": request.form.get(
                "movement_date"
            ),
            "adjustment_direction": request.form.get(
                "adjustment_direction"
            ),
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
        "/agent-accounts/movements/<int:movement_id>/reverse",
        methods=["POST"],
    )
    @admin_required
    def agent_account_reverse_movement(movement_id):
        organization_id = _require_admin_organization()
        current_user = get_current_user()
        reason = request.form.get("reversal_reason", "")

        movement = get_agent_account_movement(
            movement_id,
            organization_id,
        )
        if movement is None:
            abort(404)

        try:
            reverse_movement(
                organization_id,
                movement_id,
                created_by_user_id=current_user["id"],
                reason=reason,
            )
            flash_i18n(
                "agent_account_flash_reversed",
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

        filters = _parse_detail_filters()
        detail = build_my_account_view(
            organization_id,
            agent_id,
            filters=filters,
        )
        filters_active = any(filters.values())

        return render_template(
            "agent_account/my_account.html",
            agent=agent,
            detail=detail,
            filters=filters,
            filters_active=filters_active,
            movement_types=MOVEMENT_TYPES,
            currencies=CURRENCIES,
            movement_signed_display=movement_signed_display,
        )
