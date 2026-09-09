"""Agent-only daily visit route. Staff/Admin → 403. Read-only over Agenda."""

from __future__ import annotations

from datetime import date

from flask import redirect, render_template, request, url_for

from modules.auth import get_current_user, is_guest_session
from modules.route_planning import (
    RoutePlanningError,
    build_daily_route,
    parse_order_ids,
    require_route_agent,
)


def register_route_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    flash_i18n = helpers["flash_i18n"]

    def _forbidden():
        return ("Forbidden", 403)

    def _agent_user():
        if is_guest_session():
            return None
        try:
            return require_route_agent(get_current_user())
        except RoutePlanningError:
            return None

    def _parse_date(raw):
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    @app.route("/agenda/route")
    def agenda_route():
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            require_route_agent(user, organization_id)
        except RoutePlanningError:
            return _forbidden()
        language = get_current_language()
        local_date = _parse_date(request.args.get("date"))
        order_ids = parse_order_ids(request.args.get("order"))
        view = build_daily_route(
            organization_id,
            user.get("agent_id"),
            local_date=local_date,
            order_ids=order_ids,
            language=language,
        )
        return render_template("route/day.html", view=view)

    @app.route("/agenda/route/reorder", methods=["POST"])
    def agenda_route_reorder():
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            require_route_agent(user, organization_id)
        except RoutePlanningError:
            return _forbidden()
        local_date = (request.form.get("date") or "").strip()
        order = (request.form.get("order") or "").strip()
        # Order is a display plan only. Agenda due_at is never written here.
        return redirect(url_for("agenda_route", date=local_date or None, order=order or None))
