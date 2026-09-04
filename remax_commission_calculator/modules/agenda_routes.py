"""
Agenda routes (Phase 4B + intelligent shell).

Permissions stay the same: agents own their tasks, staff is read-only.
The compose / voice / screenshot flows only create a draft; saving
still goes through ``create_task``.
"""

from __future__ import annotations

import logging

from flask import abort, redirect, render_template, request, session, url_for

from modules.agenda_ai import (
    AgendaAiError,
    compose_from_image,
    compose_from_prompt,
    summarize_visit_outcome,
)
from modules.agent_tasks import (
    AGENDA_FILTERS,
    DURATION_CHOICES,
    PRIORITIES,
    REMINDER_CHOICES,
    TASK_TYPES,
    AgentTaskError,
    build_agenda_view,
    cancel_task,
    complete_task,
    confirm_attendance,
    create_task,
    default_form_values,
    greeting_for_user,
    load_editable_task,
    reschedule_task,
    save_visit_outcome,
    update_task,
)
from modules.auth import (
    get_current_user,
    is_agent,
    is_guest_session,
    login_required,
)
from modules.database.agents_repository import get_agents
from modules.database.operations_repository import filter_operations
from modules.database.properties_repository import get_properties
from modules.google_calendar import (
    GoogleCalendarError,
    attach_google_overlay,
    begin_oauth,
    calendar_chip_for,
    disconnect_calendar,
    finish_oauth,
    sync_now,
)
from modules.organization_time import (
    format_local_date_iso,
    format_local_time,
    now_utc,
    organization_timezone,
)


logger = logging.getLogger(__name__)


def register_agenda_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    flash_i18n = helpers["flash_i18n"]
    get_safe_redirect_target = helpers["get_safe_redirect_target"]

    def _require_user():
        if is_guest_session():
            abort(403)

        user = get_current_user()

        if user is None:
            abort(403)

        return user

    def _require_agent_user():
        user = _require_user()

        if not is_agent(user):
            abort(403)

        agent_id = user.get("agent_id")

        if agent_id is None:
            abort(403)

        return user, agent_id

    def _redirect_back(default_endpoint="agenda_index", **kwargs):
        target = get_safe_redirect_target(
            request.form.get("next") or request.args.get("next")
        )

        if target:
            return redirect(target)

        return redirect(url_for(default_endpoint, **kwargs))

    def _render_form(
        *,
        form_values,
        errors=None,
        task=None,
        organization_id,
        agent_id,
    ):
        return render_template(
            "agenda/form.html",
            form_values=form_values,
            errors=errors or [],
            task=task,
            task_types=TASK_TYPES,
            priorities=PRIORITIES,
            duration_choices=DURATION_CHOICES,
            reminder_choices=REMINDER_CHOICES,
            agenda_next=get_safe_redirect_target(
                request.form.get("next") or request.args.get("next")
            ),
            linkable_properties=get_properties(
                organization_id,
                agent_id=agent_id,
            ),
            linkable_operations=filter_operations(
                organization_id,
                agent_id=agent_id,
            ),
        )

    @app.route("/agenda")
    @login_required
    def agenda_index():
        user = _require_user()
        organization_id = require_user_organization()
        language = get_current_language()
        viewer_is_agent = is_agent(user)
        agent_id = None
        agents = []

        if viewer_is_agent:
            agent_id = user.get("agent_id")

            if agent_id is None:
                abort(403)
        else:
            agents = get_agents(organization_id)
            requested = (request.args.get("agent_id") or "").strip()

            if requested:
                allowed = {str(agent["id"]) for agent in agents}
                if requested in allowed:
                    agent_id = int(requested)

        agenda_filter = (request.args.get("filter") or "").strip()

        if agenda_filter not in AGENDA_FILTERS:
            agenda_filter = "upcoming"

        agenda = build_agenda_view(
            organization_id,
            agent_id=agent_id,
            agenda_filter=agenda_filter,
            search=request.args.get("q"),
            task_type=(request.args.get("type") or "").strip() or None,
            due_date=(request.args.get("date") or "").strip() or None,
            language=language,
        )

        if agent_id:
            try:
                agenda = attach_google_overlay(
                    agenda,
                    organization_id,
                    agent_id=agent_id,
                    language=language,
                )
            except Exception:
                logger.exception("agenda_google_overlay_failed")

        tz = organization_timezone(organization_id)
        calendar = calendar_chip_for(
            organization_id,
            user,
            agent_id=agent_id,
            can_manage=viewer_is_agent,
        )

        return render_template(
            "agenda/index.html",
            agenda=agenda,
            agenda_filters=AGENDA_FILTERS,
            task_types=TASK_TYPES,
            can_manage=viewer_is_agent,
            agents=agents,
            selected_agent_id=agent_id,
            calendar=calendar,
            agenda_greeting=greeting_for_user(
                user,
                now_local=now_utc().astimezone(tz),
                language=language,
            ),
        )

    @app.route("/agenda/new", methods=["GET", "POST"])
    @login_required
    def agenda_new():
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()

        if request.method == "POST":
            form_values = _form_values_from_request()

            try:
                create_task(
                    organization_id,
                    agent_id,
                    form_values,
                    created_by_user_id=user["id"],
                )
            except AgentTaskError as error:
                return _render_form(
                    form_values=form_values,
                    errors=[error.message_key],
                    organization_id=organization_id,
                    agent_id=agent_id,
                )

            flash_i18n("agent_task_flash_created", "success")

            return _redirect_back()

        form_values = default_form_values(
            organization_id,
            property_id=(request.args.get("property_id") or "").strip()
            or None,
            operation_id=(
                request.args.get("operation_id") or ""
            ).strip()
            or None,
            task_type=(request.args.get("type") or "").strip() or None,
            contact_name=(request.args.get("contact_name") or "").strip()
            or None,
            title=(request.args.get("title") or "").strip() or None,
            due_date=(request.args.get("due_date") or "").strip() or None,
            due_time=(request.args.get("due_time") or "").strip() or None,
            description=(request.args.get("description") or "").strip()
            or None,
            duration_minutes=request.args.get("duration_minutes") or None,
            reminder_minutes=request.args.get("reminder_minutes") or None,
            ai_suggestion=(request.args.get("ai_suggestion") or "").strip()
            or None,
        )

        return _render_form(
            form_values=form_values,
            organization_id=organization_id,
            agent_id=agent_id,
        )

    @app.route("/agenda/compose", methods=["GET", "POST"])
    @login_required
    def agenda_compose():
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()
        draft = None
        errors = []
        prompt = (
            request.form.get("prompt")
            or request.form.get("whatsapp_text")
            or request.args.get("prompt")
            or ""
        ).strip()

        if request.method == "POST" and request.form.get("confirm"):
            choice = (request.form.get("property_choice") or "").strip()
            needs_choice = request.form.get("needs_property_choice") == "1"

            if needs_choice and not choice:
                errors = ["agenda_ai_err_choose_property"]
                try:
                    draft = compose_from_prompt(
                        prompt or request.form.get("description") or "",
                        organization_id,
                        agent_id,
                    )
                except AgendaAiError as error:
                    errors.append(error.message_key)
                else:
                    if request.form.get("due_date"):
                        draft["due_date"] = request.form.get("due_date")
                        draft["date_found"] = True
                    if request.form.get("due_time"):
                        draft["due_time"] = request.form.get("due_time")
                        draft["time_found"] = True
            else:
                form_values = _form_values_from_request()

                if choice == "none":
                    form_values["property_id"] = None
                elif choice:
                    form_values["property_id"] = choice

                try:
                    create_task(
                        organization_id,
                        agent_id,
                        form_values,
                        created_by_user_id=user["id"],
                    )
                except AgentTaskError as error:
                    errors = [error.message_key]
                    draft = form_values
                    draft["ui_status"] = "error"
                else:
                    flash_i18n("agent_task_flash_created", "success")

                    return redirect(url_for("agenda_index"))

        elif request.method == "POST":
            try:
                draft = compose_from_prompt(
                    prompt,
                    organization_id,
                    agent_id,
                )
            except AgendaAiError as error:
                errors = [error.message_key]

        return render_template(
            "agenda/compose.html",
            prompt=prompt,
            draft=draft,
            errors=errors,
            voice_mode=request.args.get("mode") == "voice",
        )

    @app.route("/agenda/capture", methods=["GET", "POST"])
    @login_required
    def agenda_capture():
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()
        draft = None
        errors = []

        whatsapp_text = (
            request.form.get("whatsapp_text")
            or request.form.get("prompt")
            or ""
        ).strip()
        screenshot = request.files.get("screenshot")
        has_image = bool(screenshot and getattr(screenshot, "filename", ""))

        if request.method == "POST":
            try:
                if has_image:
                    draft = compose_from_image(
                        screenshot,
                        organization_id,
                        agent_id,
                        extra_prompt=whatsapp_text,
                    )
                elif whatsapp_text:
                    draft = compose_from_prompt(
                        whatsapp_text,
                        organization_id,
                        agent_id,
                    )
                else:
                    raise AgendaAiError("agenda_ai_err_empty_prompt")
            except AgendaAiError as error:
                errors = [error.message_key]

        return render_template(
            "agenda/capture.html",
            draft=draft,
            prompt=whatsapp_text,
            errors=errors,
        )

    @app.route(
        "/agenda/<int:task_id>/edit",
        methods=["GET", "POST"],
    )
    @login_required
    def agenda_edit(task_id):
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()

        try:
            task = load_editable_task(
                organization_id,
                task_id,
                agent_id=agent_id,
            )
        except AgentTaskError:
            abort(404)

        if request.method == "POST":
            form_values = _form_values_from_request()

            try:
                update_task(
                    organization_id,
                    task_id,
                    form_values,
                    agent_id=agent_id,
                    actor_user_id=user["id"],
                )
            except AgentTaskError as error:
                return _render_form(
                    form_values=form_values,
                    errors=[error.message_key],
                    task=task,
                    organization_id=organization_id,
                    agent_id=agent_id,
                )

            flash_i18n("agent_task_flash_updated", "success")

            return _redirect_back()

        return _render_form(
            form_values={
                "title": task["title"],
                "task_type": task["task_type"],
                "priority": task["priority"],
                "due_date": _local_date(organization_id, task),
                "due_time": _local_time(organization_id, task),
                "property_id": task["property_id"] or "",
                "operation_id": task["operation_id"] or "",
                "contact_name": task.get("contact_name") or "",
                "duration_minutes": task.get("duration_minutes") or 60,
                "reminder_minutes": task.get("reminder_minutes") or "",
                "description": task["description"],
                "ai_suggestion": "",
            },
            task=task,
            organization_id=organization_id,
            agent_id=agent_id,
        )

    @app.route("/agenda/<int:task_id>/complete", methods=["POST"])
    @login_required
    def agenda_complete(task_id):
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()

        try:
            task = complete_task(
                organization_id,
                task_id,
                agent_id=agent_id,
                actor_user_id=user["id"],
            )
        except AgentTaskError as error:
            flash_i18n(error.message_key, "error")

            return _redirect_back()

        if task["task_type"] == "visit":
            return redirect(
                url_for("agenda_follow_up", task_id=task["id"])
            )

        flash_i18n("agent_task_flash_completed", "success")

        return _redirect_back()

    @app.route(
        "/agenda/<int:task_id>/follow-up",
        methods=["GET", "POST"],
    )
    @login_required
    def agenda_follow_up(task_id):
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()

        try:
            task = load_editable_task(
                organization_id,
                task_id,
                agent_id=agent_id,
            )
        except AgentTaskError:
            abort(404)

        outcome = None
        errors = []
        note = (request.form.get("note") or "").strip()

        if request.method == "POST" and request.form.get("save"):
            outcome = {
                "note": note,
                "interest": request.form.get("interest") or "neutral",
                "objection": request.form.get("objection") or "",
                "area": request.form.get("area") or "",
                "budget": request.form.get("budget") or "",
                "next_action": request.form.get("next_action") or "",
            }

            try:
                save_visit_outcome(
                    organization_id,
                    task_id,
                    outcome,
                    agent_id=agent_id,
                )
            except AgentTaskError as error:
                errors = [error.message_key]
            else:
                flash_i18n("agenda_followup_saved", "success")

                return redirect(url_for("agenda_index"))

        elif request.method == "POST":
            try:
                outcome = summarize_visit_outcome(note)
            except AgendaAiError as error:
                errors = [error.message_key]

        return render_template(
            "agenda/follow_up.html",
            task=task,
            outcome=outcome,
            note=note,
            errors=errors,
        )

    @app.route("/agenda/<int:task_id>/reschedule", methods=["POST"])
    @login_required
    def agenda_reschedule(task_id):
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()

        try:
            reschedule_task(
                organization_id,
                task_id,
                due_date=request.form.get("due_date"),
                due_time=request.form.get("due_time"),
                agent_id=agent_id,
                actor_user_id=user["id"],
            )
        except AgentTaskError as error:
            flash_i18n(error.message_key, "error")
        else:
            flash_i18n("agent_task_flash_rescheduled", "success")

        return _redirect_back()

    @app.route("/agenda/<int:task_id>/confirm-attendance", methods=["POST"])
    @login_required
    def agenda_confirm_attendance(task_id):
        _user, agent_id = _require_agent_user()
        organization_id = require_user_organization()

        try:
            confirm_attendance(
                organization_id,
                task_id,
                agent_id=agent_id,
            )
        except AgentTaskError as error:
            flash_i18n(error.message_key, "error")
        else:
            flash_i18n("agenda_attendance_confirmed", "success")

        return _redirect_back()

    @app.route("/agenda/<int:task_id>/cancel", methods=["POST"])
    @login_required
    def agenda_cancel(task_id):
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()

        try:
            cancel_task(
                organization_id,
                task_id,
                agent_id=agent_id,
                actor_user_id=user["id"],
            )
        except AgentTaskError as error:
            flash_i18n(error.message_key, "error")
        else:
            flash_i18n("agent_task_flash_cancelled", "success")

        return _redirect_back()

    @app.route("/agenda/calendar/connect")
    @login_required
    def agenda_calendar_connect():
        _require_agent_user()

        try:
            url = begin_oauth(session)
        except GoogleCalendarError as error:
            flash_i18n(error.message_key, "error")
            return redirect(url_for("agenda_index"))

        return redirect(url)

    @app.route("/agenda/calendar/callback")
    @login_required
    def agenda_calendar_callback():
        user, _agent_id = _require_agent_user()
        organization_id = require_user_organization()

        try:
            finish_oauth(
                session,
                organization_id=organization_id,
                user_id=user["id"],
                code=(request.args.get("code") or "").strip(),
                state=(request.args.get("state") or "").strip(),
                error=(request.args.get("error") or "").strip() or None,
            )
        except GoogleCalendarError as error:
            flash_i18n(error.message_key, "error")
            return redirect(url_for("agenda_index"))
        except Exception:
            flash_i18n("agenda_calendar_flash_oauth_error", "error")
            return redirect(url_for("agenda_index"))

        flash_i18n("agenda_calendar_flash_connected", "success")

        return redirect(url_for("agenda_index"))

    @app.route("/agenda/calendar/disconnect", methods=["POST"])
    @login_required
    def agenda_calendar_disconnect():
        user, _agent_id = _require_agent_user()
        organization_id = require_user_organization()

        disconnect_calendar(organization_id, user["id"])
        flash_i18n("agenda_calendar_flash_disconnected", "success")

        return _redirect_back()

    @app.route("/agenda/calendar/sync", methods=["POST"])
    @login_required
    def agenda_calendar_sync():
        _user, agent_id = _require_agent_user()
        organization_id = require_user_organization()

        try:
            sync_now(organization_id, agent_id=agent_id)
        except GoogleCalendarError as error:
            flash_i18n(error.message_key, "error")
        else:
            flash_i18n("agenda_calendar_flash_synced", "success")

        return _redirect_back()


def _form_values_from_request():
    return {
        "title": request.form.get("title"),
        "task_type": request.form.get("task_type"),
        "priority": request.form.get("priority") or "normal",
        "due_date": request.form.get("due_date"),
        "due_time": request.form.get("due_time"),
        "property_id": (
            request.form.get("property_id") or ""
        ).strip()
        or None,
        "operation_id": (
            request.form.get("operation_id") or ""
        ).strip()
        or None,
        "contact_name": request.form.get("contact_name"),
        "duration_minutes": request.form.get("duration_minutes"),
        "reminder_minutes": request.form.get("reminder_minutes"),
        "attendance_status": request.form.get("attendance_status"),
        "description": request.form.get("description"),
        "ai_suggestion": request.form.get("ai_suggestion"),
    }


def _local_date(organization_id, task):
    return format_local_date_iso(
        task["due_at"],
        organization_timezone(organization_id),
    )


def _local_time(organization_id, task):
    return format_local_time(
        task["due_at"],
        organization_timezone(organization_id),
    )
