"""
Agenda routes (Phase 4B).

Permissions in V1:

* Agent: full control over their own tasks (create, edit, complete,
  reschedule, cancel) and no visibility of anybody else's agenda.
* Staff/Admin: read-only view of the agenda of the agents in their own
  organization, with an agent filter. The service already accepts an
  ``agent_id`` so letting staff create tasks later is a route change,
  not a redesign.
* Guest: no access.
"""

from __future__ import annotations

from flask import abort, redirect, render_template, request, url_for

from modules.agent_tasks import (
    AGENDA_FILTERS,
    PRIORITIES,
    TASK_TYPES,
    AgentTaskError,
    build_agenda_view,
    cancel_task,
    complete_task,
    create_task,
    default_form_values,
    load_editable_task,
    reschedule_task,
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
from modules.organization_time import (
    format_local_date_iso,
    format_local_time,
    organization_timezone,
)


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
        """Only an agent owns an agenda in V1."""
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

        return render_template(
            "agenda/index.html",
            agenda=agenda,
            agenda_filters=AGENDA_FILTERS,
            task_types=TASK_TYPES,
            can_manage=viewer_is_agent,
            agents=agents,
            selected_agent_id=agent_id,
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
        )

        return _render_form(
            form_values=form_values,
            organization_id=organization_id,
            agent_id=agent_id,
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
                "description": task["description"],
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
            complete_task(
                organization_id,
                task_id,
                agent_id=agent_id,
                actor_user_id=user["id"],
            )
        except AgentTaskError as error:
            flash_i18n(error.message_key, "error")
        else:
            flash_i18n("agent_task_flash_completed", "success")

        return _redirect_back()

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


def _form_values_from_request():
    return {
        "title": request.form.get("title"),
        "task_type": request.form.get("task_type"),
        "priority": request.form.get("priority"),
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
        "description": request.form.get("description"),
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
