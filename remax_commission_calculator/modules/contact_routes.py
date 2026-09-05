"""
Agent contacts: list, create, commercial profile and preferences.
"""

from __future__ import annotations

from flask import abort, redirect, render_template, request, url_for

from modules.auth import (
    get_current_user,
    is_agent,
    is_guest_session,
    login_required,
)
from modules.contacts import (
    CONTACT_FILTERS,
    ContactError,
    create_agent_contact,
    decorate_contact,
    load_contact,
    list_contact_cards,
    preferences_from_form,
    update_agent_contact,
)
from modules.database.contacts_repository import SOURCES, STATUSES


def register_contact_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    flash_i18n = helpers["flash_i18n"]

    def _require_user():
        if is_guest_session():
            abort(403)

        user = get_current_user()
        if user is None:
            abort(403)

        return user

    def _scope(user):
        if is_agent(user):
            return user.get("agent_id"), True
        return None, False

    def _payload_from_form():
        return {
            "name": request.form.get("name"),
            "phone": request.form.get("phone"),
            "email": request.form.get("email"),
            "status": request.form.get("status") or "lead",
            "source": request.form.get("source") or "manual",
            "notes": request.form.get("notes"),
            "preferences": preferences_from_form(request.form),
        }

    @app.route("/contacts")
    @login_required
    def contacts_index():
        user = _require_user()
        organization_id = require_user_organization()
        language = get_current_language()
        agent_id, can_manage = _scope(user)
        contact_filter = (request.args.get("filter") or "all").strip()
        if contact_filter not in CONTACT_FILTERS:
            contact_filter = "all"
        search = (request.args.get("q") or "").strip()

        cards = list_contact_cards(
            organization_id,
            agent_id=agent_id,
            contact_filter=contact_filter,
            search=search,
            language=language,
        )

        return render_template(
            "contacts/index.html",
            contacts=cards,
            contact_filter=contact_filter,
            search=search,
            can_manage=can_manage,
        )

    @app.route("/contacts/new", methods=["GET", "POST"])
    @login_required
    def contacts_new():
        user = _require_user()
        organization_id = require_user_organization()
        agent_id, can_manage = _scope(user)

        if not can_manage or agent_id is None:
            abort(403)

        errors = []
        form = {
            "name": "",
            "phone": "",
            "email": "",
            "status": "lead",
            "notes": "",
        }

        if request.method == "POST":
            form = _payload_from_form()
            try:
                contact = create_agent_contact(
                    organization_id,
                    agent_id,
                    form,
                )
            except ContactError as error:
                errors = [error.message_key]
            else:
                flash_i18n("contacts_flash_created", "success")
                return redirect(
                    url_for("contacts_detail", contact_id=contact["id"])
                )

        return render_template(
            "contacts/form.html",
            mode="new",
            form=form,
            errors=errors,
            statuses=STATUSES,
            sources=SOURCES,
        )

    @app.route("/contacts/<int:contact_id>")
    @login_required
    def contacts_detail(contact_id):
        user = _require_user()
        organization_id = require_user_organization()
        language = get_current_language()
        agent_id, can_manage = _scope(user)

        try:
            contact = load_contact(
                organization_id,
                contact_id,
                agent_id=agent_id,
            )
        except ContactError:
            abort(404)

        card = decorate_contact(
            contact,
            organization_id=organization_id,
            language=language,
        )

        return render_template(
            "contacts/detail.html",
            contact=card,
            can_manage=can_manage,
        )

    @app.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
    @login_required
    def contacts_edit(contact_id):
        user = _require_user()
        organization_id = require_user_organization()
        language = get_current_language()
        agent_id, can_manage = _scope(user)

        if not can_manage or agent_id is None:
            abort(403)

        try:
            contact = load_contact(
                organization_id,
                contact_id,
                agent_id=agent_id,
            )
        except ContactError:
            abort(404)

        errors = []
        card = decorate_contact(
            contact,
            organization_id=organization_id,
            language=language,
        )
        form = {
            "name": contact["name"],
            "phone": contact.get("phone") or "",
            "email": contact.get("email") or "",
            "status": contact["status"],
            "source": contact.get("source") or "manual",
            "notes": contact.get("notes") or "",
            "preferences": card["preferences"],
        }

        if request.method == "POST":
            form = _payload_from_form()
            try:
                updated = update_agent_contact(
                    organization_id,
                    contact_id,
                    form,
                    agent_id=agent_id,
                )
            except ContactError as error:
                errors = [error.message_key]
                form["preferences"] = preferences_from_form(request.form)
            else:
                flash_i18n("contacts_flash_updated", "success")
                return redirect(
                    url_for("contacts_detail", contact_id=updated["id"])
                )

        return render_template(
            "contacts/form.html",
            mode="edit",
            form=form,
            errors=errors,
            statuses=STATUSES,
            sources=SOURCES,
            contact=card,
        )
