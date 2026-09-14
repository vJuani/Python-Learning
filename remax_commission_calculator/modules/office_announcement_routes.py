"""Staff/admin UI to send same-org office announcements."""

from __future__ import annotations

from flask import redirect, render_template, request, url_for

from modules.auth import admin_required, get_current_user, is_guest_session
from modules.database.agents_repository import get_agents
from modules.office_announcements import (
    OfficeAnnouncementError,
    send_office_announcement,
)


def register_office_announcement_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    flash_i18n = helpers["flash_i18n"]
    get_current_language = helpers["get_current_language"]

    @app.route("/settings/office-notifications", methods=["GET", "POST"])
    @admin_required
    def settings_office_notifications():
        user = get_current_user()
        if user is None or is_guest_session():
            return ("Forbidden", 403)
        organization_id = require_user_organization()
        agents = get_agents(organization_id)
        language = get_current_language()

        if request.method == "POST":
            audience = (request.form.get("audience") or "all").strip()
            agent_ids = None
            if audience == "selected":
                agent_ids = request.form.getlist("agent_id")
            try:
                send_office_announcement(
                    organization_id,
                    user["id"],
                    request.form.get("title"),
                    request.form.get("body"),
                    url=request.form.get("url"),
                    agent_ids=agent_ids,
                    language=language,
                )
            except OfficeAnnouncementError as error:
                flash_i18n(error.message_key, "error")
                return render_template(
                    "settings/office_notifications.html",
                    agents=agents,
                    form=request.form,
                )
            except Exception:
                app.logger.warning(
                    "office_announcement_send_failed organization_id=%s sender_user_id=%s",
                    organization_id,
                    user["id"],
                    exc_info=True,
                )
                flash_i18n("office_announcement_err_send", "error")
                return render_template(
                    "settings/office_notifications.html",
                    agents=agents,
                    form=request.form,
                )
            flash_i18n("office_announcement_sent", "success")
            return redirect(url_for("settings_office_notifications"))

        return render_template(
            "settings/office_notifications.html",
            agents=agents,
            form={},
        )
