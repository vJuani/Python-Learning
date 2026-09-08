"""Agent-only productivity routes. Staff/Admin → 403."""

from __future__ import annotations

from datetime import date

from flask import redirect, render_template, request, session, url_for

from modules.agent_productivity import (
    ProductivityError,
    PERIOD_TYPES,
    SUPPORTED_METRICS,
    build_productivity_view,
    confirm_logged_activity,
    disable_goal,
    edit_logged_activity,
    enrich_logged_contacts,
    propose_logged_activity,
    require_productivity_agent,
    save_goals,
)
from modules.auth import get_current_user, is_guest_session

DRAFT_KEY = "prod_log_draft"


def _empty_draft():
    return {
        "note": "",
        "channels": [],
        "purposes": [],
        "proposals": [],
        "edit_index": None,
    }


def _draft():
    data = session.get(DRAFT_KEY)
    if not isinstance(data, dict):
        return _empty_draft()
    merged = _empty_draft()
    merged.update(data)
    return merged


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
        on_date = None
        try:
            if request.args.get("day"):
                on_date = date.fromisoformat(request.args.get("day"))
        except ValueError:
            on_date = None
        draft = _draft()
        view = build_productivity_view(
            organization_id,
            user=user,
            language=get_current_language(),
            period=period,
            on_date=on_date,
            proposals=draft.get("proposals") or [],
        )
        view["draft"] = draft
        return render_template("productivity/dashboard.html", view=view)

    @app.route("/productivity/log", methods=["POST"])
    def productivity_log():
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            require_productivity_agent(user, organization_id)
        except ProductivityError:
            return _forbidden()
        language = get_current_language()
        action = request.form.get("action")
        draft = _draft()
        if action == "parse":
            draft["note"] = request.form.get("note") or ""
            draft["channels"] = request.form.getlist("channel")
            draft["purposes"] = request.form.getlist("purpose")
            draft["proposals"] = enrich_logged_contacts(
                organization_id,
                user["agent_id"],
                propose_logged_activity(
                    draft["note"],
                    channels=draft["channels"],
                    purposes=draft["purposes"],
                    language=language,
                ),
                language=language,
            )
            draft["edit_index"] = None
            session[DRAFT_KEY] = draft
            if not draft["proposals"]:
                flash_i18n("prod_identified_empty", "error")
        elif action == "edit":
            try:
                draft["edit_index"] = int(request.form.get("index") or -1)
            except ValueError:
                draft["edit_index"] = None
            session[DRAFT_KEY] = draft
        elif action == "save_edit":
            try:
                index = int(request.form.get("index") or -1)
            except ValueError:
                index = -1
            draft["proposals"] = enrich_logged_contacts(
                organization_id,
                user["agent_id"],
                edit_logged_activity(
                    draft.get("proposals") or [],
                    index,
                    channel=request.form.get("channel"),
                    contact_name=request.form.get("contact_name"),
                    purpose=request.form.get("purpose"),
                    language=language,
                ),
                language=language,
            )
            draft["edit_index"] = None
            session[DRAFT_KEY] = draft
        elif action == "create_contact":
            try:
                index = int(request.form.get("index") or -1)
            except ValueError:
                index = -1
            proposals = list(draft.get("proposals") or [])
            if 0 <= index < len(proposals):
                proposals[index] = dict(proposals[index])
                proposals[index]["create_contact"] = True
            draft["proposals"] = proposals
            session[DRAFT_KEY] = draft
        elif action == "review":
            draft["edit_index"] = None
            session[DRAFT_KEY] = draft
        elif action == "confirm":
            created = confirm_logged_activity(
                organization_id,
                user=user,
                proposals=draft.get("proposals") or [],
                language=language,
            )
            session.pop(DRAFT_KEY, None)
            if created:
                flash_i18n("prod_activity_saved", "success")
        else:
            session.pop(DRAFT_KEY, None)
        period = request.form.get("period") or "daily"
        day = request.form.get("day") or None
        return redirect(url_for("productivity_home", period=period, day=day))

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
