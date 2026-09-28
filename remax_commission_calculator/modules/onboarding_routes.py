"""Public /onboarding wizard. It is not linked from the app navigation."""

from __future__ import annotations

from datetime import datetime

from flask import redirect, render_template, request, session, url_for

from modules.auth import login_user
from modules.database.organization_settings_repository import (
    dismiss_onboarding_checklist,
)
from modules.database.users_repository import get_user_by_id
from modules.i18n import translate
from modules.integration_status import describe_integrations
from modules.onboarding import (
    COUNTRY_TIMEZONES,
    build_onboarding_checklist,
    complete_onboarding,
)
from modules.organization_settings import (
    COMMON_TIMEZONES,
    DEFAULT_CURRENCY,
    DEFAULT_TIMEZONE,
    SUPPORTED_CURRENCIES,
)


def register_onboarding_routes(app, helpers):
    login_required = helpers["login_required"]
    admin_required = helpers["admin_required"]
    get_current_language = helpers["get_current_language"]
    get_current_user = helpers["get_current_user"]

    timezones = list(COMMON_TIMEZONES)
    if "America/Mexico_City" not in timezones:
        timezones.append("America/Mexico_City")

    def _save_logo(organization_id, logo_file):
        import web_app

        return web_app.save_organization_logo(organization_id, logo_file)

    def _values(source=None):
        source = source or {}
        return {
            "invite_code": source.get("invite_code", ""),
            "name": source.get("name", ""),
            "country": source.get("country", "Argentina"),
            "region": source.get("region", ""),
            "city": source.get("city", ""),
            "timezone": source.get("timezone", DEFAULT_TIMEZONE),
            "currency": source.get("currency", DEFAULT_CURRENCY),
            "language": source.get("language", "es"),
            "first_name": source.get("first_name", ""),
            "last_name": source.get("last_name", ""),
            "email": source.get("email", ""),
            "commercial_name": source.get("commercial_name", ""),
            "accent_color": source.get("accent_color") or "#0f766e",
            "marketing_phone": source.get("marketing_phone", ""),
            "marketing_email": source.get("marketing_email", ""),
            "marketing_website": source.get("marketing_website", ""),
            "legal_office_name": source.get("legal_office_name", ""),
            "legal_broker_name": source.get("legal_broker_name", ""),
            "legal_broker_license": source.get("legal_broker_license", ""),
            "legal_footer_line": source.get("legal_footer_line", ""),
        }

    def _render(values, errors, status=200):
        language = get_current_language()
        return render_template(
            "onboarding/wizard.html",
            values=values,
            errors=[translate(key, language) for key in errors],
            countries=list(COUNTRY_TIMEZONES),
            country_timezones=COUNTRY_TIMEZONES,
            timezones=timezones,
            currencies=SUPPORTED_CURRENCIES,
        ), status

    @app.route("/onboarding", methods=["GET", "POST"])
    def onboarding():
        if request.method == "GET":
            return _render(_values(), [])

        form = request.form
        result = complete_onboarding(
            form,
            logo_file=request.files.get("logo"),
            save_logo=_save_logo,
        )
        if not result["ok"]:
            if result.get("generic"):
                return _render(_values(), result["errors"], 400)
            values = _values(result.get("values"))
            values["invite_code"] = form.get("invite_code", "")
            return _render(values, result["errors"], 400)

        user = get_user_by_id(result["admin_user_id"])
        login_user(user)
        session["onboarding_registration_code"] = result["registration_code"]
        if result["logo_pending"] and request.files.get("logo") and request.files.get("logo").filename:
            session["onboarding_logo_pending"] = True
        return redirect(url_for("onboarding_ready"))

    @app.route("/onboarding/ready")
    @login_required
    def onboarding_ready():
        user = get_current_user()
        language = get_current_language()
        checklist = build_onboarding_checklist(
            user["organization_id"],
            language,
        )
        return render_template(
            "onboarding/ready.html",
            registration_code=session.get("onboarding_registration_code") or "",
            checklist=checklist,
            integrations=describe_integrations(),
            logo_pending=bool(session.pop("onboarding_logo_pending", False)),
        )

    @app.route("/onboarding/checklist/dismiss", methods=["POST"])
    @admin_required
    def onboarding_checklist_dismiss():
        user = get_current_user()
        checklist = build_onboarding_checklist(
            user["organization_id"],
            get_current_language(),
        )
        if checklist is None or not checklist["can_dismiss"]:
            return redirect(url_for("dashboard"))
        dismiss_onboarding_checklist(
            user["organization_id"],
            datetime.utcnow().isoformat(timespec="seconds"),
        )
        return redirect(url_for("dashboard"))
