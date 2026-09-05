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
from modules.database.properties_repository import get_property_record
from modules.listings_normalize import listing_from_property
from modules.property_match import (
    build_whatsapp_message,
    criteria_is_temporary,
    decorate_match,
    persist_search_preferences,
    rank_contact_properties,
    resolve_criteria,
    search_chip_labels,
    whatsapp_share_url,
)
from modules.listing_sources import listing_source_capabilities
from modules.property_features import FEATURE_KEYS, normalize_wanted_features
from modules.property_types import LISTING_PURPOSES, PROPERTY_TYPES


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

    def _scoped_contact(contact_id):
        user = _require_user()
        organization_id = require_user_organization()
        agent_id, can_manage = _scope(user)
        try:
            contact = load_contact(
                organization_id,
                contact_id,
                agent_id=agent_id,
            )
        except ContactError:
            abort(404)
        return user, organization_id, agent_id, can_manage, contact

    def _requested_criteria():
        source = request.form if request.method == "POST" else request.args
        has_search = any(
            source.get(name)
            for name in (
                "areas",
                "area",
                "budget_min",
                "budget_max",
                "budget_currency",
                "property_types",
                "property_type",
                "rooms",
                "bedrooms",
                "features",
                "feature",
                "purpose",
                "listing_purpose",
            )
        )
        if not has_search:
            return None
        return preferences_from_form(source)

    def _selected_ids(name):
        ids = []
        for raw in request.values.getlist(name):
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        return ids

    @app.route(
        "/contacts/<int:contact_id>/property-matches",
        methods=["GET", "POST"],
    )
    @login_required
    def contacts_property_matches(contact_id):
        user, organization_id, agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        language = get_current_language()
        override = _requested_criteria()

        if request.method == "POST" and request.form.get("save_search") and can_manage:
            persist_search_preferences(
                organization_id,
                contact,
                override or preferences_from_form(request.form),
            )
            flash_i18n("contacts_flash_prefs_updated", "success")
            return redirect(
                url_for("contacts_property_matches", contact_id=contact_id)
            )

        criteria = resolve_criteria(contact, override)
        if criteria.get("features"):
            criteria = dict(criteria)
            criteria["features"] = normalize_wanted_features(criteria["features"])
        ranked = rank_contact_properties(
            organization_id,
            contact,
            agent_id=agent_id,
            criteria_override=criteria if override else None,
        )
        cards = []
        for item in ranked:
            card = decorate_match(item, language=language)
            if item.get("external_listing_id"):
                card["view_url"] = item.get("external_url")
                card["share_url"] = url_for(
                    "contacts_property_matches_share",
                    contact_id=contact_id,
                    external_listing_id=item["external_listing_id"],
                )
                card["agenda_url"] = url_for(
                    "agenda_compose",
                    contact_id=contact_id,
                    external_listing_id=item["external_listing_id"],
                    type="visit",
                )
            else:
                card["internal_url"] = url_for(
                    "properties_detail",
                    property_id=item["property_id"],
                )
                card["view_url"] = card["internal_url"]
                card["share_url"] = url_for(
                    "contacts_property_matches_share",
                    contact_id=contact_id,
                    property_id=item["property_id"],
                )
                card["agenda_url"] = url_for(
                    "agenda_compose",
                    contact_id=contact_id,
                    property_id=item["property_id"],
                    type="visit",
                )
            cards.append(card)

        show_more = str(request.values.get("show_more") or "") in ("1", "true")
        visible = [card for card in cards if not card["hidden"]]
        hidden = [card for card in cards if card["hidden"]]
        shown = cards if show_more else visible
        temporary = criteria_is_temporary(contact, criteria)
        first_name = (contact.get("name") or "").split()[0]
        card = decorate_contact(
            contact,
            organization_id=organization_id,
            language=language,
        )

        return render_template(
            "contacts/property_matches.html",
            contact=card,
            first_name=first_name,
            criteria=criteria,
            chips=search_chip_labels(criteria, language=language),
            matches=shown,
            hidden_count=len(hidden),
            compatible_count=len(visible),
            total_count=len(cards),
            show_more=show_more,
            is_temporary=temporary,
            can_manage=can_manage,
            feature_keys=FEATURE_KEYS,
            property_types=PROPERTY_TYPES,
            listing_purposes=LISTING_PURPOSES,
            listing_sources=listing_source_capabilities(),
        )

    @app.route("/contacts/<int:contact_id>/property-matches/share")
    @login_required
    def contacts_property_matches_share(contact_id):
        user, organization_id, agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        language = get_current_language()
        selected_property_ids = _selected_ids("property_id")
        selected_external_ids = _selected_ids("external_listing_id")
        if not selected_property_ids and not selected_external_ids:
            abort(404)

        ranked = rank_contact_properties(
            organization_id,
            contact,
            agent_id=agent_id,
        )
        by_property = {item.get("property_id"): item for item in ranked}
        by_external = {
            item.get("external_listing_id"): item
            for item in ranked
            if item.get("external_listing_id")
        }
        selected = []
        for property_id in selected_property_ids:
            item = by_property.get(property_id)
            if item is None:
                record = get_property_record(property_id, organization_id)
                if record is None:
                    abort(404)
                if agent_id is not None and record.get("agent_id") != agent_id:
                    abort(404)
                item = {
                    "property_id": property_id,
                    "internal_property_id": property_id,
                    "source": "internal",
                    "score": 0,
                    "level": "low",
                    "hidden": True,
                    "dimensions": {},
                    "listing": listing_from_property(record),
                    "property": record,
                    "visited": False,
                    "discarded": False,
                }
            card = decorate_match(item, language=language)
            card["internal_url"] = url_for(
                "properties_detail",
                property_id=property_id,
                _external=False,
            )
            selected.append(card)
        for listing_id in selected_external_ids:
            item = by_external.get(listing_id)
            if item is None:
                from modules.database.external_listings_repository import (
                    get_external_listing,
                )
                from modules.listings_normalize import listing_from_external_listing

                record = get_external_listing(listing_id, organization_id)
                if record is None:
                    abort(404)
                item = {
                    "external_listing_id": listing_id,
                    "source": record["source"],
                    "external_url": record.get("external_url"),
                    "score": 0,
                    "level": "low",
                    "hidden": True,
                    "dimensions": {},
                    "listing": listing_from_external_listing(record),
                    "property": record,
                    "visited": False,
                    "discarded": False,
                }
            card = decorate_match(item, language=language)
            selected.append(card)

        if not selected:
            abort(404)

        message = build_whatsapp_message(
            contact,
            selected,
            language=language,
        )
        url = whatsapp_share_url(contact.get("phone"), message)
        if url is None:
            flash_i18n("contacts_add_phone", "error")
            return redirect(
                url_for("contacts_property_matches", contact_id=contact_id)
            )
        return redirect(url)
