"""Admin-only one-shot agenda reminder scan. Never public."""

from __future__ import annotations

from flask import redirect, render_template, request, url_for

from modules.auth import admin_required, get_current_user, is_guest_session
from modules.database.organization_settings_repository import (
    DEFAULT_TIMEZONE,
    get_organization_settings,
)
from modules.visit_reminders import (
    clear_visit_reminder_dedupe,
    probe_visit_reminders,
    scheduler_status,
    scan_visit_reminders,
)


def register_visit_reminder_qa_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    flash_i18n = helpers["flash_i18n"]

    def _page(organization_id, scan=None):
        settings = get_organization_settings(organization_id) or {}
        timezone_name = settings.get("timezone") or DEFAULT_TIMEZONE
        return render_template(
            "settings/agenda_reminders.html",
            scan=scan,
            timezone_name=timezone_name,
            scheduler=scheduler_status(organization_id),
        )

    @app.route("/settings/agenda-reminders", methods=["GET", "POST"])
    @admin_required
    def settings_agenda_reminders():
        user = get_current_user()
        if user is None or is_guest_session():
            return ("Forbidden", 403)
        organization_id = require_user_organization()

        if request.method != "POST":
            return _page(organization_id)

        action = (request.form.get("action") or "probe").strip()
        if action == "reset":
            try:
                task_id = int(request.form.get("task_id") or 0)
            except (TypeError, ValueError):
                task_id = 0
            if task_id <= 0:
                flash_i18n("agenda_reminder_qa_err_task", "error")
            else:
                deleted = clear_visit_reminder_dedupe(
                    organization_id,
                    task_id,
                )
                app.logger.info(
                    "agenda_reminder_qa_reset organization_id=%s "
                    "task_id=%s deleted=%s",
                    organization_id,
                    task_id,
                    deleted,
                )
                flash_i18n("agenda_reminder_qa_reset", "success")
            return redirect(url_for("settings_agenda_reminders"))

        try:
            if action == "scan":
                scan = scan_visit_reminders(
                    organization_id,
                    send=True,
                    source="qa",
                    include_near_misses=True,
                )
            else:
                scan = probe_visit_reminders(
                    organization_id,
                    current_user=user,
                )
        except Exception:
            app.logger.warning(
                "agenda_reminder_qa_scan_failed organization_id=%s "
                "sender_user_id=%s",
                organization_id,
                user["id"],
                exc_info=True,
            )
            flash_i18n("agenda_reminder_qa_err_scan", "error")
            return _page(organization_id)
        flash_i18n("agenda_reminder_qa_ran", "success")
        return _page(organization_id, scan=scan)
