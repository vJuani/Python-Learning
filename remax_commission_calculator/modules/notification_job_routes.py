"""HTTP tick for Railway Cron. Never starts a Gunicorn loop."""

from __future__ import annotations

import hmac
import os

from flask import abort, jsonify, request


def register_notification_job_routes(app):
    @app.route("/internal/jobs/notifications/tick", methods=["POST"])
    def notifications_job_tick():
        from modules.notifications.dispatcher import DISPATCHER_HTTP, dispatcher_allows

        secret = (os.environ.get("NOTIFICATION_JOB_SECRET") or "").strip()
        provided = (request.headers.get("X-Job-Secret") or "").strip()
        if not secret or not hmac.compare_digest(provided, secret):
            abort(404)
        if not dispatcher_allows(DISPATCHER_HTTP):
            abort(404)
        from modules.notifications.jobs import run_notification_jobs

        summary = run_notification_jobs(source="http")
        return jsonify(
            {
                "ok": True,
                "now": summary.get("now"),
                "agenda_created": summary.get("agenda_created"),
                "overdue_created": summary.get("overdue_created"),
            }
        )
