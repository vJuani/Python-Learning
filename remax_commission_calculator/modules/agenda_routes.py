"""
Agenda routes (Phase 4B + intelligent shell).

Permissions stay the same: agents own their tasks, staff is read-only.
The compose / voice / screenshot flows only create a draft; saving
still goes through ``create_task``.
"""

from __future__ import annotations

import json
import logging

from flask import abort, redirect, render_template, request, session, url_for

from modules.agenda_ai import (
    TIME_REQUIRED_TYPES,
    AgendaAiError,
    compose_from_image,
    compose_from_prompt,
    interpret_agenda_input,
    refresh_item,
    summarize_visit_outcome,
)
from modules.contacts import (
    ContactError,
    apply_known_contact,
    bind_compose_contact,
    create_agent_contact,
    diff_preference_update,
    load_contact,
    preferences_from_outcome,
    save_contact_preference_update,
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
from modules.database.properties_repository import (
    get_properties,
    get_property_record,
)
from modules.google_calendar import (
    GoogleCalendarError,
    attach_google_overlay,
    begin_oauth,
    calendar_chip_for,
    disconnect_calendar,
    finish_oauth,
    retry_task_sync,
    sync_now,
)
from modules.i18n import translate
from modules.organization_time import (
    format_local_date_iso,
    format_local_time,
    now_utc,
    organization_timezone,
)
from modules.visit_outcome import (
    attach_suggested_task,
    format_budget_label,
    is_search_next_action,
    outcome_from_form,
    outcome_is_present,
    properties_search_args,
)


logger = logging.getLogger(__name__)

_ITEM_KEYS = (
    "title",
    "task_type",
    "due_date",
    "due_time",
    "contact_name",
    "contact_id",
    "contact_match",
    "property_id",
    "external_listing_id",
    "property_address",
    "listing_source_label",
    "listing_neighborhood",
    "listing_price",
    "property_query",
    "property_match",
    "description",
    "duration_minutes",
    "reminder_minutes",
    "attendance_status",
    "source_prompt",
    "ui_status",
    "item_status",
)


def _items_from_form(form):
    try:
        count = int(form.get("item_count") or 0)
    except (TypeError, ValueError):
        count = 0

    remove_at = form.get("remove_item")
    items = []

    for index in range(count):
        if remove_at != "" and str(index) == str(remove_at):
            continue

        prefix = f"items-{index}-"
        item = {key: (form.get(f"{prefix}{key}") or "") for key in _ITEM_KEYS}
        item["date_found"] = form.get(f"{prefix}date_found") == "1" or bool(
            item.get("due_date")
        )
        item["time_found"] = form.get(f"{prefix}time_found") == "1" or bool(
            item.get("due_time")
        )
        raw_candidates = form.get(f"{prefix}candidates") or "[]"
        try:
            item["property_candidates"] = json.loads(raw_candidates)
        except (TypeError, ValueError):
            item["property_candidates"] = []

        choice = (form.get(f"{prefix}property_choice") or "").strip()
        if choice == "none":
            item["property_id"] = ""
            item["property_match"] = "none"
        elif choice:
            item["property_id"] = choice
            item["property_match"] = "single"
            for candidate in item["property_candidates"]:
                if str(candidate.get("id")) == str(choice):
                    item["property_address"] = candidate.get("address") or ""
                    break

        raw_contacts = form.get(f"{prefix}contact_candidates") or "[]"
        try:
            item["contact_candidates"] = json.loads(raw_contacts)
        except (TypeError, ValueError):
            item["contact_candidates"] = []

        contact_choice = (form.get(f"{prefix}contact_choice") or "").strip()
        if contact_choice == "none":
            item["contact_id"] = ""
            item["contact_match"] = "skipped"
            item["contact_preview"] = None
        elif contact_choice:
            for candidate in item["contact_candidates"]:
                if str(candidate.get("id")) == str(contact_choice):
                    item["contact_id"] = candidate.get("id")
                    item["contact_name"] = (
                        candidate.get("name") or item.get("contact_name")
                    )
                    item["contact_match"] = "single"
                    item["contact_preview"] = candidate
                    break

        if item.get("contact_id") and not item.get("contact_preview"):
            for candidate in item.get("contact_candidates") or []:
                if str(candidate.get("id")) == str(item["contact_id"]):
                    item["contact_preview"] = candidate
                    break

        items.append(refresh_item(item))

    return items


def _item_to_payload(item):
    due_time = item.get("due_time")
    if not due_time and item.get("task_type") not in TIME_REQUIRED_TYPES:
        due_time = "09:00"

    return {
        "title": item.get("title"),
        "task_type": item.get("task_type"),
        "priority": item.get("priority") or "normal",
        "due_date": item.get("due_date"),
        "due_time": due_time,
        "property_id": (item.get("property_id") or "") or None,
        "external_listing_id": (
            item.get("external_listing_id") or ""
        ) or None,
        "operation_id": None,
        "contact_name": item.get("contact_name"),
        "contact_id": (item.get("contact_id") or "") or None,
        "duration_minutes": item.get("duration_minutes"),
        "reminder_minutes": item.get("reminder_minutes"),
        "attendance_status": item.get("attendance_status"),
        "description": item.get("description"),
        "ai_suggestion": item.get("title"),
    }


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

    def _scoped_contact(organization_id, agent_id, raw_id):
        if raw_id in (None, ""):
            return None
        try:
            contact_id = int(raw_id)
        except (TypeError, ValueError):
            abort(404)
        try:
            return load_contact(
                organization_id,
                contact_id,
                agent_id=agent_id,
            )
        except ContactError:
            abort(404)

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
            create_result=session.pop("agenda_create_result", None),
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
            external_listing_id=(
                request.args.get("external_listing_id") or ""
            ).strip()
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
        language = get_current_language()
        draft = None
        errors = []
        creating_contact_index = None
        compose_contact = _scoped_contact(
            organization_id,
            agent_id,
            request.form.get("compose_contact_id")
            or request.args.get("contact_id"),
        )
        prompt = (
            request.form.get("prompt")
            or request.form.get("whatsapp_text")
            or request.args.get("prompt")
            or ""
        ).strip()
        if compose_contact and not prompt:
            prompt = translate(
                "contacts_schedule_prompt",
                language,
                name=compose_contact["name"],
            )

        items = []
        property_id = (request.args.get("property_id") or "").strip()
        external_listing_id = (
            request.args.get("external_listing_id") or ""
        ).strip()
        requested_type = (request.args.get("type") or "").strip()
        if (
            request.method == "GET"
            and compose_contact
            and (property_id or external_listing_id)
        ):
            defaults = default_form_values(organization_id)
            if external_listing_id:
                from modules.database.external_listings_repository import (
                    get_external_listing,
                )
                from modules.listing_sources import SOURCE_LABEL_KEYS
                from modules.property_inventory import format_listing_money

                try:
                    external_listing_id = int(external_listing_id)
                except (TypeError, ValueError):
                    abort(404)
                listing_row = get_external_listing(
                    external_listing_id,
                    organization_id,
                )
                if listing_row is None:
                    abort(404)
                source_label = translate(
                    SOURCE_LABEL_KEYS.get(
                        listing_row["source"],
                        "listing_source_internal",
                    ),
                    language,
                )
                price = format_listing_money(
                    listing_row.get("price"),
                    listing_row.get("currency"),
                    language=language,
                )
                draft = {
                    "title": translate(
                        "matches_visit_title",
                        language,
                        name=compose_contact["name"],
                        address=listing_row.get("address") or "",
                    ),
                    "task_type": requested_type or "visit",
                    "property_id": "",
                    "external_listing_id": listing_row["id"],
                    "property_address": listing_row.get("address") or "",
                    "listing_source_label": source_label,
                    "listing_neighborhood": listing_row.get("neighborhood") or "",
                    "listing_price": price or "",
                    "property_match": "single",
                    "description": " · ".join(
                        part
                        for part in (
                            source_label,
                            listing_row.get("neighborhood"),
                            price,
                        )
                        if part
                    ),
                    "due_date": defaults["due_date"],
                    "due_time": defaults["due_time"],
                    "duration_minutes": 60,
                    "reminder_minutes": 15,
                }
            else:
                try:
                    property_id = int(property_id)
                except (TypeError, ValueError):
                    abort(404)
                property_row = get_property_record(property_id, organization_id)
                if property_row is None:
                    abort(404)
                if agent_id is not None and property_row.get("agent_id") != agent_id:
                    abort(404)
                draft = {
                    "title": translate(
                        "matches_visit_title",
                        language,
                        name=compose_contact["name"],
                        address=property_row.get("address") or "",
                    ),
                    "task_type": requested_type or "visit",
                    "property_id": property_row["id"],
                    "external_listing_id": "",
                    "property_address": property_row.get("address") or "",
                    "listing_source_label": "",
                    "listing_neighborhood": "",
                    "listing_price": "",
                    "property_match": "single",
                    "description": "",
                    "due_date": defaults["due_date"],
                    "due_time": defaults["due_time"],
                    "duration_minutes": 60,
                    "reminder_minutes": 15,
                }
            apply_known_contact(draft, compose_contact, language=language)
            draft = refresh_item(draft)
            items = [draft]
            prompt = draft["title"]

        if request.method == "POST" and (
            request.form.get("confirm_ready")
            or request.form.get("confirm_one")
            or request.form.get("remove_item") not in (None, "")
            or request.form.get("quick_create_contact") not in (None, "")
            or request.form.get("create_contact_item") not in (None, "")
            or request.form.get("item_count")
        ):
            items = _items_from_form(request.form)
            confirm_one = (request.form.get("confirm_one") or "").strip()
            confirm_ready = bool(request.form.get("confirm_ready"))
            create_at = (request.form.get("create_contact_item") or "").strip()
            quick_at = (request.form.get("quick_create_contact") or "").strip()

            if quick_at != "":
                try:
                    index = int(quick_at)
                except (TypeError, ValueError):
                    index = -1
                if 0 <= index < len(items):
                    try:
                        created_contact = create_agent_contact(
                            organization_id,
                            agent_id,
                            {
                                "name": request.form.get("quick_name")
                                or items[index].get("contact_name"),
                                "phone": request.form.get("quick_phone"),
                                "email": request.form.get("quick_email"),
                                "source": "agenda",
                            },
                        )
                    except ContactError as error:
                        errors = [error.message_key]
                        creating_contact_index = index
                    else:
                        apply_known_contact(
                            items[index],
                            created_contact,
                            language=language,
                        )
                        refresh_item(items[index])
            elif create_at != "":
                try:
                    creating_contact_index = int(create_at)
                except (TypeError, ValueError):
                    creating_contact_index = None

            if confirm_one != "" or confirm_ready:
                selected = []
                if confirm_one != "":
                    try:
                        index = int(confirm_one)
                    except (TypeError, ValueError):
                        index = -1
                    if 0 <= index < len(items) and items[index].get("item_status") == "ready":
                        selected = [items[index]]
                else:
                    selected = [
                        item for item in items if item.get("item_status") == "ready"
                    ]

                if not selected:
                    errors = ["agenda_ai_err_none_ready"]
                else:
                    created = []
                    remaining = []
                    create_failed = False
                    for item in items:
                        if item not in selected:
                            remaining.append(item)
                            continue
                        try:
                            created.append(
                                create_task(
                                    organization_id,
                                    agent_id,
                                    _item_to_payload(item),
                                    created_by_user_id=user["id"],
                                )
                            )
                        except AgentTaskError as error:
                            errors = [error.message_key]
                            item["ui_status"] = "error"
                            item["item_status"] = "needs_attention"
                            remaining.append(item)
                            create_failed = True

                    if created and not create_failed:
                        calendar = calendar_chip_for(
                            organization_id,
                            user,
                            agent_id=agent_id,
                            can_manage=True,
                        )
                        pending = []
                        if calendar.get("state") in ("synced", "error"):
                            pending = [
                                task["id"]
                                for task in created
                                if not task.get("google_event_id")
                            ]
                        session["agenda_create_result"] = {
                            "created": len(created),
                            "synced": len(created) - len(pending),
                            "pending_ids": pending,
                        }
                        if remaining:
                            items = remaining
                        else:
                            flash_i18n("agent_task_flash_created", "success")
                            return redirect(url_for("agenda_index"))

            draft = items[0] if len(items) == 1 else None

        elif request.method == "POST" and request.form.get("confirm"):
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
                    items = [draft]
                except AgendaAiError as error:
                    errors.append(error.message_key)
            else:
                form_values = _form_values_from_request()

                if choice == "none":
                    form_values["property_id"] = None
                elif choice:
                    form_values["property_id"] = choice

                try:
                    created = create_task(
                        organization_id,
                        agent_id,
                        form_values,
                        created_by_user_id=user["id"],
                    )
                except AgentTaskError as error:
                    errors = [error.message_key]
                    draft = form_values
                    draft["ui_status"] = "error"
                    items = [draft]
                else:
                    calendar = calendar_chip_for(
                        organization_id,
                        user,
                        agent_id=agent_id,
                        can_manage=True,
                    )
                    pending = []
                    if calendar.get("state") in ("synced", "error") and not created.get(
                        "google_event_id"
                    ):
                        pending = [created["id"]]
                    session["agenda_create_result"] = {
                        "created": 1,
                        "synced": 0 if pending else 1,
                        "pending_ids": pending,
                    }
                    flash_i18n("agent_task_flash_created", "success")

                    return redirect(url_for("agenda_index"))

        elif request.method == "POST":
            try:
                bundle = interpret_agenda_input(
                    prompt,
                    organization_id,
                    agent_id,
                )
                items = bundle["items"]
                if compose_contact:
                    bind_compose_contact(
                        items,
                        compose_contact,
                        language=language,
                    )
                    for item in items:
                        refresh_item(item)
                draft = items[0] if len(items) == 1 else None
            except AgendaAiError as error:
                errors = [error.message_key]

        return render_template(
            "agenda/compose.html",
            prompt=prompt,
            draft=draft,
            items=items,
            errors=errors,
            compose_contact=compose_contact,
            creating_contact_index=creating_contact_index,
            voice_mode=request.args.get("mode") == "voice",
        )

    @app.route("/agenda/capture", methods=["GET", "POST"])
    @login_required
    def agenda_capture():
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()
        draft = None
        errors = []
        items = []

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
                    items = [draft]
                elif whatsapp_text:
                    bundle = interpret_agenda_input(
                        whatsapp_text,
                        organization_id,
                        agent_id,
                    )
                    items = bundle["items"]
                    draft = items[0] if len(items) == 1 else None
                else:
                    raise AgendaAiError("agenda_ai_err_empty_prompt")
            except AgendaAiError as error:
                errors = [error.message_key]

        return render_template(
            "agenda/capture.html",
            draft=draft,
            items=items,
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
                "external_listing_id": task.get("external_listing_id") or "",
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
        items = []
        errors = []
        followup_contact = None
        merge_preview = None
        if task.get("contact_id"):
            try:
                followup_contact = load_contact(
                    organization_id,
                    task["contact_id"],
                    agent_id=agent_id,
                )
            except ContactError:
                followup_contact = None
        note = (request.form.get("note") or "").strip()
        step = (request.form.get("step") or request.args.get("step") or "entry")
        intent = (request.form.get("intent") or "").strip()

        if request.method == "POST" and intent == "discard":
            outcome = None
            note = ""
            step = "entry"

        elif request.method == "POST" and intent == "edit":
            outcome = attach_suggested_task(outcome_from_form(request.form), task)
            note = outcome.get("note") or note
            step = "edit"

        elif request.method == "POST" and intent == "preview":
            outcome = attach_suggested_task(outcome_from_form(request.form), task)
            note = outcome.get("note") or note
            step = "preview"

        elif request.method == "POST" and intent == "save":
            outcome = attach_suggested_task(outcome_from_form(request.form), task)
            try:
                save_visit_outcome(
                    organization_id,
                    task_id,
                    outcome,
                    agent_id=agent_id,
                )
            except AgentTaskError as error:
                errors = [error.message_key]
                step = "preview"
            else:
                flash_i18n("agenda_followup_saved", "success")
                next_step = "next"
                if followup_contact:
                    preview = diff_preference_update(
                        followup_contact.get("preferences_json"),
                        preferences_from_outcome(outcome),
                    )
                    if preview.get("has_changes"):
                        next_step = "contact_prefs"
                return redirect(
                    url_for(
                        "agenda_follow_up",
                        task_id=task["id"],
                        step=next_step,
                    )
                )

        elif request.method == "POST" and intent == "apply_contact_prefs":
            if followup_contact:
                try:
                    save_contact_preference_update(
                        organization_id,
                        followup_contact["id"],
                        preferences_from_outcome(
                            outcome_from_form(request.form)
                            or task.get("outcome_json")
                        ),
                        accepted_conflicts=request.form.getlist(
                            "accept_conflict"
                        ),
                        agent_id=agent_id,
                    )
                except ContactError as error:
                    errors = [error.message_key]
                    step = "contact_prefs"
                else:
                    flash_i18n("contacts_flash_prefs_updated", "success")
                    return redirect(
                        url_for(
                            "agenda_follow_up",
                            task_id=task["id"],
                            step="next",
                        )
                    )
            else:
                return redirect(
                    url_for("agenda_follow_up", task_id=task["id"], step="next")
                )

        elif request.method == "POST" and intent == "skip_contact_prefs":
            return redirect(
                url_for("agenda_follow_up", task_id=task["id"], step="next")
            )

        elif request.method == "POST" and intent == "schedule":
            selected = _items_from_form(request.form)
            ready = [
                item for item in selected if item.get("item_status") == "ready"
            ]
            if not ready:
                errors = ["agenda_ai_err_none_ready"]
                outcome = attach_suggested_task(
                    outcome_from_form(request.form),
                    task,
                )
                step = "next"
                items = selected
            else:
                try:
                    create_task(
                        organization_id,
                        agent_id,
                        _item_to_payload(ready[0]),
                        created_by_user_id=user["id"],
                    )
                except AgentTaskError as error:
                    errors = [error.message_key]
                    outcome = attach_suggested_task(
                        outcome_from_form(request.form),
                        task,
                    )
                    step = "next"
                    items = selected
                else:
                    flash_i18n("agent_task_flash_created", "success")
                    return redirect(url_for("agenda_index"))

        elif request.method == "POST" and intent == "summarize":
            try:
                outcome = attach_suggested_task(
                    summarize_visit_outcome(note),
                    task,
                )
                step = "preview"
            except AgendaAiError as error:
                errors = [error.message_key]
                step = "entry"

        elif request.method == "POST":
            try:
                outcome = attach_suggested_task(
                    summarize_visit_outcome(note),
                    task,
                )
                step = "preview"
            except AgendaAiError as error:
                errors = [error.message_key]
                step = "entry"

        if step == "contact_prefs":
            if outcome is None and outcome_is_present(task.get("outcome_json")):
                outcome = attach_suggested_task(task.get("outcome_json"), task)
            if followup_contact and outcome:
                merge_preview = diff_preference_update(
                    followup_contact.get("preferences_json"),
                    preferences_from_outcome(outcome),
                )
            if not merge_preview or not merge_preview.get("has_changes"):
                step = "next"

        if step == "next":
            if outcome is None and outcome_is_present(task.get("outcome_json")):
                outcome = attach_suggested_task(task.get("outcome_json"), task)
            if outcome:
                prompt = (outcome.get("suggested_task") or {}).get("prompt") or ""
                if prompt:
                    try:
                        items = interpret_agenda_input(
                            prompt,
                            organization_id,
                            agent_id,
                        )["items"]
                        if followup_contact:
                            bind_compose_contact(
                                items,
                                followup_contact,
                            )
                            for item in items:
                                refresh_item(item)
                    except AgendaAiError as error:
                        errors.append(error.message_key)

        return render_template(
            "agenda/follow_up.html",
            task=task,
            outcome=outcome,
            note=note or (outcome or {}).get("note") or "",
            errors=errors,
            step=step,
            items=items,
            followup_contact=followup_contact,
            merge_preview=merge_preview,
            budget_label=format_budget_label((outcome or {}).get("budget")),
            is_search_action=is_search_next_action(
                (outcome or {}).get("next_action")
            ),
            properties_query=properties_search_args(outcome),
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
            return redirect(url_for("settings_integrations"))

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
            return redirect(url_for("settings_integrations"))
        except Exception:
            flash_i18n("agenda_calendar_flash_oauth_error", "error")
            return redirect(url_for("settings_integrations"))

        flash_i18n("agenda_calendar_flash_connected", "success")

        return redirect(url_for("settings_integrations"))

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

    @app.route("/settings/integrations")
    @login_required
    def settings_integrations():
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()
        calendar = calendar_chip_for(
            organization_id,
            user,
            agent_id=agent_id,
            can_manage=True,
        )

        return render_template(
            "settings/integrations.html",
            calendar=calendar,
        )

    @app.route("/agenda/calendar/retry", methods=["POST"])
    @login_required
    def agenda_calendar_retry():
        user, agent_id = _require_agent_user()
        organization_id = require_user_organization()
        raw_ids = request.form.getlist("task_id")
        synced = 0
        pending = []

        for raw in raw_ids:
            try:
                task_id = int(raw)
            except (TypeError, ValueError):
                continue

            try:
                task = load_editable_task(
                    organization_id,
                    task_id,
                    agent_id=agent_id,
                )
            except AgentTaskError:
                continue

            pushed = retry_task_sync(task, actor_user_id=user["id"])
            if pushed or task.get("google_event_id"):
                synced += 1
            else:
                pending.append(task_id)

        if pending:
            flash_i18n("agenda_calendar_flash_retry_partial", "error")
            session["agenda_create_result"] = {
                "created": len(raw_ids),
                "synced": synced,
                "pending_ids": pending,
            }
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
        "external_listing_id": (
            request.form.get("external_listing_id") or ""
        ).strip()
        or None,
        "operation_id": (
            request.form.get("operation_id") or ""
        ).strip()
        or None,
        "contact_name": request.form.get("contact_name"),
        "contact_id": (request.form.get("contact_id") or "") or None,
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
