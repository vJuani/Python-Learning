"""Agent-only productivity routes. Staff/Admin → 403."""

from __future__ import annotations

from flask import redirect, render_template, request, url_for

from modules.agent_productivity import (
    ProductivityError,
    PERIOD_TYPES,
    SUPPORTED_METRICS,
    build_productivity_view,
    disable_goal,
    require_productivity_agent,
    save_goals,
)
from modules.auth import get_current_user, is_guest_session


def register_productivity_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    flash_i18n = helpers["flash_i18n"]

    def _forbidden():
        return ("Forbidden", 403)

    def _agent_user():
        if is_guest_session():
            return None
        try:
            return require_productivity_agent(get_current_user())
        except ProductivityError:
            return None

    def _handle(error):
        if error.status_code == 403:
            return _forbidden()
        flash_i18n(error.message_key, "error")
        return redirect(url_for("productivity_home"))

    @app.route("/productivity")
    def productivity_home():
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            require_productivity_agent(user, organization_id)
        except ProductivityError:
            return _forbidden()
        period = request.args.get("period") or "daily"
        if period not in PERIOD_TYPES:
            period = "daily"
        view = build_productivity_view(
            organization_id,
            user=user,
            language=get_current_language(),
            period=period,
        )
        return render_template("productivity/dashboard.html", view=view)

    @app.route("/productivity/goals", methods=["GET", "POST"])
    def productivity_goals():
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            require_productivity_agent(user, organization_id)
        except ProductivityError:
            return _forbidden()
        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "deactivate":
                    disable_goal(
                        organization_id,
                        user=user,
                        goal_id=int(request.form.get("goal_id") or 0),
                    )
                    flash_i18n("prod_goal_removed", "success")
                else:
                    save_goals(
                        organization_id,
                        user=user,
                        items=[
                            {
                                "metric_key": request.form.get("metric_key"),
                                "period_type": request.form.get("period_type"),
                                "target_value": request.form.get("target_value"),
                                "currency": request.form.get("currency"),
                            }
                        ],
                    )
                    flash_i18n("prod_goals_saved", "success")
            except ProductivityError as error:
                return _handle(error)
            return redirect(url_for("productivity_home"))
        view = build_productivity_view(
            organization_id,
            user=user,
            language=get_current_language(),
            period="daily",
        )
        return render_template(
            "productivity/goals.html",
            view=view,
            metrics=SUPPORTED_METRICS,
            periods=PERIOD_TYPES,
        )
