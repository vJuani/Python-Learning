"""
Agent contacts: list, create, commercial profile and preferences.
"""

from __future__ import annotations

from flask import abort, redirect, render_template, request, session, url_for

from modules.auth import (
    can_use_agent_workspace,
    get_current_user,
    is_agent,
    is_guest_session,
    login_required,
)
from modules.contact_import import (
    BrowserContactPickerProvider,
    VCardImportProvider,
    confirm_import,
    list_provider_capabilities,
    preview_import,
)
from modules.contact_follow_up import (
    CADENCE_CHOICES,
    COMMERCIAL_STAGES,
    FOLLOW_UP_PRIORITIES,
    POSTPONE_DAYS,
    FollowUpInputError,
    add_follow_up_note,
    cadence_form_value,
    mark_contacted,
    postpone_follow_up,
)
from modules.contacts import (
    CONTACT_FILTERS,
    CONTACT_TYPES,
    ContactError,
    archive_agent_contact,
    count_stale_contacts,
    create_agent_contact,
    decorate_contact,
    load_contact,
    list_contact_cards,
    preferences_from_form,
    restore_agent_contact,
    save_contact_need,
    update_agent_contact,
    whatsapp_digits,
)
from modules.follow_up_daily import collect_agent_follow_ups
from modules.organization_time import (
    format_local_date_iso,
    now_utc,
    organization_timezone,
)
from modules.database.contacts_repository import (
    SOURCES,
    STATUSES,
    record_property_interaction,
)
from modules.database.properties_repository import get_property_record
from modules.listings_normalize import listing_from_property
from modules.property_match import (
    RECOMMENDATION_LIMIT,
    build_whatsapp_message,
    criteria_is_temporary,
    decorate_match,
    persist_search_preferences,
    rank_contact_properties,
    resolve_criteria,
    search_chip_labels,
    whatsapp_share_url,
)

DISCARD_REASONS = (
    "precio",
    "zona",
    "distribucion",
    "estado",
    "cochera",
    "no_le_gusto",
    "otro",
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
        if not can_use_agent_workspace(user):
            abort(403)

        return user

    def _scope(user):
        if is_agent(user) and user.get("agent_id"):
            return user.get("agent_id"), True
        abort(403)

    def _payload_from_form():
        return {
            "name": request.form.get("name"),
            "phone": request.form.get("phone"),
            "email": request.form.get("email"),
            "status": request.form.get("status") or "lead",
            "source": request.form.get("source") or "manual",
            "notes": request.form.get("notes"),
            "company": request.form.get("company"),
            "contact_type": request.form.get("contact_type"),
            "commercial_stage": request.form.get("commercial_stage") or "",
            "follow_up_priority": request.form.get("follow_up_priority") or "",
            "follow_up_cadence_choice": request.form.get("follow_up_cadence") or "",
            "follow_up_reason": request.form.get("follow_up_reason") or "",
            "next_follow_up_date": request.form.get("next_follow_up_date") or "",
            "preferences": preferences_from_form(
                request.form,
                organization_id=require_user_organization(),
            ),
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
        stale_count = 0
        if can_manage and agent_id is not None:
            stale_count = count_stale_contacts(
                organization_id,
                agent_id=agent_id,
            )

        return render_template(
            "contacts/index.html",
            contacts=cards,
            contact_filter=contact_filter,
            search=search,
            can_manage=can_manage,
            stale_count=stale_count,
            import_capabilities=list_provider_capabilities() if can_manage else [],
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
            "commercial_stage": "new",
            "follow_up_priority": "potential",
            "follow_up_cadence_choice": "medium",
            "follow_up_reason": "",
            "next_follow_up_date": "",
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
            contact_types=CONTACT_TYPES,
            commercial_stages=COMMERCIAL_STAGES,
            follow_up_priorities=FOLLOW_UP_PRIORITIES,
            cadence_choices=CADENCE_CHOICES,
        )

    @app.route("/contacts/follow-ups")
    @login_required
    def contacts_follow_ups():
        user = _require_user()
        organization_id = require_user_organization()
        language = get_current_language()
        agent_id, can_manage = _scope(user)
        from modules.i18n import translate

        items = collect_agent_follow_ups(organization_id, agent_id)
        for item in items:
            digits = whatsapp_digits(item.get("phone"))
            item["whatsapp_url"] = f"https://wa.me/{digits}" if digits else None
            item["tel_url"] = f"tel:{digits}" if digits else None
            item["reason_label"] = translate(item["reason_key"], language)
        return render_template(
            "contacts/follow_ups.html",
            items=items,
            can_manage=can_manage,
            postpone_days=POSTPONE_DAYS,
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
        ranked = rank_contact_properties(
            organization_id,
            contact,
            agent_id=agent_id,
        )
        recommendations = [
            _present_match(contact_id, item, language)
            for item in ranked
            if int(item.get("score") or 0) > 0
        ][:RECOMMENDATION_LIMIT]
        _attach_match_photos(organization_id, recommendations)

        return render_template(
            "contacts/detail.html",
            contact=card,
            can_manage=can_manage,
            recommendations=recommendations,
            discard_reasons=DISCARD_REASONS,
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
            "company": contact.get("company") or "",
            "contact_type": contact.get("contact_type") or "",
            "commercial_stage": contact.get("commercial_stage") or "",
            "follow_up_priority": contact.get("follow_up_priority") or "",
            "follow_up_cadence_choice": cadence_form_value(contact),
            "follow_up_reason": contact.get("follow_up_reason") or "",
            "next_follow_up_date": format_local_date_iso(
                contact.get("next_follow_up_at"),
                organization_timezone(organization_id),
            ),
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
                form["preferences"] = preferences_from_form(
                    request.form,
                    organization_id=organization_id,
                )
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
            contact_types=CONTACT_TYPES,
            commercial_stages=COMMERCIAL_STAGES,
            follow_up_priorities=FOLLOW_UP_PRIORITIES,
            cadence_choices=CADENCE_CHOICES,
            contact=card,
        )

    def _follow_up_redirect(contact_id):
        target = (request.form.get("next") or "").strip()
        if target == "detail":
            return redirect(url_for("contacts_detail", contact_id=contact_id))
        return redirect(url_for("contacts_follow_ups"))

    @app.post("/contacts/<int:contact_id>/follow-up/contacted")
    @login_required
    def contacts_follow_up_contacted(contact_id):
        _user, organization_id, _agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        mark_contacted(contact, now=now_utc())
        flash_i18n("followup_flash_contacted", "success")
        return _follow_up_redirect(contact["id"])

    @app.post("/contacts/<int:contact_id>/follow-up/postpone")
    @login_required
    def contacts_follow_up_postpone(contact_id):
        _user, _organization_id, _agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        try:
            postpone_follow_up(
                contact,
                now=now_utc(),
                days=request.form.get("days"),
            )
        except FollowUpInputError:
            flash_i18n("contacts_err_invalid_follow_up", "error")
            return _follow_up_redirect(contact["id"])
        flash_i18n("followup_flash_postponed", "success")
        return _follow_up_redirect(contact["id"])

    @app.post("/contacts/<int:contact_id>/follow-up/note")
    @login_required
    def contacts_follow_up_note(contact_id):
        _user, organization_id, _agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        try:
            add_follow_up_note(
                contact,
                request.form.get("note"),
                now=now_utc(),
                tz=organization_timezone(organization_id),
            )
        except FollowUpInputError:
            flash_i18n("followup_err_note", "error")
            return _follow_up_redirect(contact["id"])
        flash_i18n("followup_flash_note", "success")
        return _follow_up_redirect(contact["id"])

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

    def _safe_contact_next(contact_id):
        target = str(request.form.get("next") or "")
        prefix = f"/contacts/{int(contact_id)}"
        if target.startswith(prefix) and not target.startswith("//"):
            return target
        return url_for("contacts_detail", contact_id=contact_id)

    def _owned_property(organization_id, agent_id, property_id):
        record = get_property_record(property_id, organization_id)
        if record is None:
            return None
        if agent_id is not None and record.get("agent_id") != agent_id:
            return None
        return record

    def _attach_match_photos(organization_id, cards):
        from modules.property_sync.media import (
            get_property_media_url,
            list_covers_for_properties,
        )

        ids = [card.get("property_id") for card in cards if card.get("property_id")]
        if not ids:
            return cards
        covers = list_covers_for_properties(organization_id, ids)
        for card in cards:
            property_id = card.get("property_id")
            if not property_id:
                continue
            cover = covers.get(int(property_id))
            if cover:
                card["photo_url"] = get_property_media_url(cover, property_id)
        return cards

    def _present_match(contact_id, item, language):
        card = decorate_match(item, language=language)
        property_id = item.get("property_id")
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
        elif property_id:
            card["internal_url"] = url_for("properties_detail", property_id=property_id)
            card["view_url"] = card["internal_url"]
            card["brochure_url"] = url_for("property_brochure", property_id=property_id)
            card["share_url"] = url_for(
                "contacts_property_matches_share",
                contact_id=contact_id,
                property_id=property_id,
            )
            card["agenda_url"] = url_for(
                "agenda_compose",
                contact_id=contact_id,
                property_id=property_id,
                type="visit",
            )
            card["shared_url"] = url_for(
                "contacts_property_match_shared",
                contact_id=contact_id,
                property_id=property_id,
            )
            card["discard_url"] = url_for(
                "contacts_property_match_discard",
                contact_id=contact_id,
                property_id=property_id,
            )
        reasons = card.get("reasons") or {}
        labels = []
        for bucket, limit in (("warnings", 1), ("missing", 1), ("conflicts", 1)):
            for entry in (reasons.get(bucket) or [])[:limit]:
                if entry.get("label"):
                    labels.append(entry["label"])
        for entry in reasons.get("matched") or []:
            if entry.get("label") and entry["label"] not in labels:
                labels.append(entry["label"])
        card["reason_labels"] = labels[:3]
        return card

    IMPORT_SESSION_KEY = "contact_import_preview"

    @app.route("/contacts/import", methods=["GET", "POST"])
    @login_required
    def contacts_import():
        user = _require_user()
        organization_id = require_user_organization()
        agent_id, can_manage = _scope(user)
        if not can_manage or agent_id is None:
            abort(403)
        language = get_current_language()
        errors = []
        if request.method == "POST":
            provider_id = (request.form.get("provider") or "").strip()
            selected = []
            source_type = "phone_import"
            if provider_id == BrowserContactPickerProvider.provider_id:
                raw = request.form.get("selected_json") or "[]"
                try:
                    import json

                    selected = json.loads(raw)
                except (TypeError, ValueError):
                    selected = []
                source_type = "phone_import"
            elif provider_id == VCardImportProvider.provider_id:
                upload = request.files.get("vcard")
                text = ""
                if upload and upload.filename:
                    text = upload.read().decode("utf-8", errors="ignore")
                selected = VCardImportProvider().parse_selected(text)
                source_type = "vcard"
            else:
                errors = ["contacts_import_unsupported"]
            if not errors:
                if not selected:
                    errors = ["contacts_import_empty"]
                else:
                    preview = preview_import(
                        organization_id,
                        agent_id,
                        selected,
                        source_type=source_type,
                        language=language,
                    )
                    session[IMPORT_SESSION_KEY] = preview
                    return redirect(url_for("contacts_import_preview"))
        return render_template(
            "contacts/import.html",
            errors=errors,
            capabilities=list_provider_capabilities(),
        )

    @app.route("/contacts/import/preview", methods=["GET", "POST"])
    @login_required
    def contacts_import_preview():
        user = _require_user()
        organization_id = require_user_organization()
        agent_id, can_manage = _scope(user)
        if not can_manage or agent_id is None:
            abort(403)
        preview = session.get(IMPORT_SESSION_KEY)
        if not preview:
            return redirect(url_for("contacts_import"))
        if request.method == "POST":
            token = request.form.get("import_token")
            if token != preview.get("import_token"):
                abort(400)
            decisions = {}
            for index, _item in enumerate(preview.get("items") or []):
                action = request.form.get(f"action_{index}") or "skip"
                decisions[index] = action
                match_id = request.form.get(f"match_id_{index}")
                if match_id:
                    decisions[f"{index}_match_id"] = match_id
            result = confirm_import(
                organization_id,
                agent_id,
                preview,
                decisions=decisions,
                import_token=token,
            )
            session.pop(IMPORT_SESSION_KEY, None)
            flash_i18n("contacts_import_done", "success")
            if result.get("created"):
                return redirect(
                    url_for(
                        "contacts_detail",
                        contact_id=result["created"][0]["id"],
                    )
                )
            return redirect(url_for("contacts_index"))
        return render_template(
            "contacts/import_preview.html",
            preview=preview,
        )

    @app.route("/contacts/<int:contact_id>/archive", methods=["POST"])
    @login_required
    def contacts_archive(contact_id):
        user, organization_id, agent_id, can_manage, _contact = _scoped_contact(
            contact_id
        )
        if not can_manage or agent_id is None:
            abort(403)
        archive_agent_contact(organization_id, contact_id, agent_id=agent_id)
        flash_i18n("contacts_flash_archived", "success")
        return redirect(url_for("contacts_index"))

    @app.route("/contacts/<int:contact_id>/restore", methods=["POST"])
    @login_required
    def contacts_restore(contact_id):
        user = _require_user()
        organization_id = require_user_organization()
        agent_id, can_manage = _scope(user)
        if not can_manage or agent_id is None:
            abort(403)
        restore_agent_contact(organization_id, contact_id, agent_id=agent_id)
        flash_i18n("contacts_flash_restored", "success")
        return redirect(url_for("contacts_detail", contact_id=contact_id))

    @app.route("/contacts/<int:contact_id>/need", methods=["GET", "POST"])
    @login_required
    def contacts_need(contact_id):
        user, organization_id, agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage or agent_id is None:
            abort(403)
        language = get_current_language()
        card = decorate_contact(
            contact,
            organization_id=organization_id,
            language=language,
        )
        form = {
            "name": contact["name"],
            "preferences": card["preferences"],
        }
        errors = []
        if request.method == "POST":
            try:
                save_contact_need(
                    organization_id,
                    contact_id,
                    preferences_from_form(
                        request.form,
                        organization_id=organization_id,
                    ),
                    agent_id=agent_id,
                    client_name=request.form.get("client_name") or contact.get("name"),
                )
            except ContactError as error:
                errors = [error.message_key]
            else:
                flash_i18n("contacts_flash_prefs_updated", "success")
                return redirect(url_for("contacts_detail", contact_id=contact_id))
        return render_template(
            "contacts/form.html",
            mode="need",
            form=form,
            errors=errors,
            statuses=STATUSES,
            sources=SOURCES,
            contact_types=CONTACT_TYPES,
            contact=card,
        )

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
                "center_latitude",
                "radius_km",
                "location_mode",
            )
        )
        if not has_search:
            return None
        return preferences_from_form(
            source,
            organization_id=require_user_organization(),
        )

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
                override or preferences_from_form(
                    request.form,
                    organization_id=organization_id,
                ),
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
        cards = [_present_match(contact_id, item, language) for item in ranked]
        _attach_match_photos(organization_id, cards)

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
            discard_reasons=DISCARD_REASONS,
        )

    @app.route(
        "/contacts/<int:contact_id>/property-matches/<int:property_id>/shared",
        methods=["POST"],
    )
    @login_required
    def contacts_property_match_shared(contact_id, property_id):
        _user, organization_id, agent_id, can_manage, _contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        if _owned_property(organization_id, agent_id, property_id) is None:
            abort(404)
        record_property_interaction(
            organization_id,
            agent_id,
            contact_id=contact_id,
            property_id=property_id,
            interaction_type="shared",
        )
        flash_i18n("matches_shared_done", "success")
        return redirect(_safe_contact_next(contact_id))

    @app.route(
        "/contacts/<int:contact_id>/property-matches/<int:property_id>/discard",
        methods=["POST"],
    )
    @login_required
    def contacts_property_match_discard(contact_id, property_id):
        _user, organization_id, agent_id, can_manage, _contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        if _owned_property(organization_id, agent_id, property_id) is None:
            abort(404)
        reason = (request.form.get("reason") or "").strip()
        if reason not in DISCARD_REASONS:
            reason = ""
        record_property_interaction(
            organization_id,
            agent_id,
            contact_id=contact_id,
            property_id=property_id,
            interaction_type="discarded",
            label=reason or None,
        )
        flash_i18n("matches_discarded_done", "success")
        return redirect(_safe_contact_next(contact_id))

    def _shortlist_key(contact_id):
        return f"property_shortlist_{int(contact_id)}"

    def _shortlist_share(stored):
        mode = stored.get("mode") or "individual"
        if mode not in ("individual", "collection"):
            mode = "individual"
        try:
            days = int(stored.get("share_days") or 30)
        except (TypeError, ValueError):
            days = 30
        return mode, days, stored.get("collection_token") or ""

    def _attach_share_links(organization_id, agent_id, contact, items, stored):
        from modules.public_share import prepare_client_links

        mode, days, token = _shortlist_share(stored)
        collection_url, token = prepare_client_links(
            organization_id,
            items,
            agent_id=agent_id,
            base_url=request.url_root.rstrip("/"),
            mode=mode,
            contact_id=contact["id"],
            days=days,
            collection_token=token,
        )
        return collection_url, token, mode, days

    def _load_shortlist(organization_id, agent_id, contact, language):
        from modules.property_shortlist import (
            ShortlistError,
            attach_match_scores,
            draft_whatsapp_message,
            select_properties,
        )

        stored = session.get(_shortlist_key(contact["id"])) or {}
        ids = stored.get("property_ids") or []
        items = attach_match_scores(
            organization_id,
            contact,
            select_properties(
                organization_id,
                contact["id"],
                ids,
                agent_id=agent_id,
            ),
            agent_id=agent_id,
        )
        collection_url, token, mode, days = _attach_share_links(
            organization_id,
            agent_id,
            contact,
            items,
            stored,
        )
        message = stored.get("message") or draft_whatsapp_message(
            contact,
            items,
            language=language,
            collection_url=collection_url if mode == "collection" else "",
        )
        return items, message, {
            "mode": mode,
            "share_days": days,
            "collection_token": token,
            "collection_url": collection_url if mode == "collection" else "",
        }

    def _save_shortlist(contact, items, message, share=None):
        share = share or {}
        session[_shortlist_key(contact["id"])] = {
            "property_ids": [item["property_id"] for item in items],
            "message": message,
            "mode": share.get("mode") or "individual",
            "share_days": share.get("share_days") or 30,
            "collection_token": share.get("collection_token") or "",
            "collection_url": share.get("collection_url") or "",
        }

    def _reject_shortlist(contact_id, error):
        flash_i18n(f"shortlist_err_{error.code}", "error")
        return redirect(url_for("contacts_detail", contact_id=contact_id))

    @app.route("/contacts/<int:contact_id>/shortlist", methods=["GET", "POST"])
    @login_required
    def contacts_property_shortlist(contact_id):
        from modules.property_shortlist import (
            ShortlistError,
            attach_match_scores,
            draft_whatsapp_message,
            select_properties,
        )

        _user, organization_id, agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        language = get_current_language()
        if request.method == "POST" and request.form.get("property_id"):
            try:
                items = attach_match_scores(
                    organization_id,
                    contact,
                    select_properties(
                        organization_id,
                        contact["id"],
                        request.form.getlist("property_id"),
                        agent_id=agent_id,
                    ),
                    agent_id=agent_id,
                )
            except ShortlistError as error:
                return _reject_shortlist(contact_id, error)
            share = {"mode": "individual", "share_days": 30, "collection_token": ""}
            collection_url, token, mode, days = _attach_share_links(
                organization_id,
                agent_id,
                contact,
                items,
                share,
            )
            share = {
                "mode": mode,
                "share_days": days,
                "collection_token": token,
                "collection_url": collection_url,
            }
            _save_shortlist(
                contact,
                items,
                draft_whatsapp_message(
                    contact,
                    items,
                    language=language,
                    collection_url=collection_url if mode == "collection" else "",
                ),
                share,
            )
            return redirect(url_for("contacts_property_shortlist", contact_id=contact_id))

        stored = session.get(_shortlist_key(contact_id)) or {}
        if not stored.get("property_ids"):
            return redirect(url_for("contacts_detail", contact_id=contact_id))
        try:
            items, message, share = _load_shortlist(
                organization_id,
                agent_id,
                contact,
                language,
            )
        except ShortlistError as error:
            session.pop(_shortlist_key(contact_id), None)
            return _reject_shortlist(contact_id, error)
        _save_shortlist(contact, items, message, share)
        first_name = (contact.get("name") or "").split()
        return render_template(
            "contacts/shortlist.html",
            contact=contact,
            first_name=first_name[0] if first_name else "",
            items=items,
            message=message,
            can_manage=can_manage,
            share_mode=share["mode"],
            share_days=share["share_days"],
        )

    @app.route("/contacts/<int:contact_id>/shortlist/update", methods=["POST"])
    @login_required
    def contacts_property_shortlist_update(contact_id):
        _user, organization_id, agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        language = get_current_language()
        try:
            items, message, share = _load_shortlist(
                organization_id,
                agent_id,
                contact,
                language,
            )
        except ShortlistError as error:
            return _reject_shortlist(contact_id, error)
        posted = []
        for raw in request.form.getlist("property_id"):
            try:
                posted.append(int(raw))
            except (TypeError, ValueError):
                continue
        ids = posted or [item["property_id"] for item in items]
        action_raw = request.form.get("action") or ""
        action, _sep, raw_index = action_raw.partition(":")
        if action == "mode":
            share["mode"] = raw_index if raw_index in ("individual", "collection") else "individual"
            try:
                share["share_days"] = int(request.form.get("share_days") or share["share_days"])
            except (TypeError, ValueError):
                share["share_days"] = 30
        try:
            index = int(raw_index or 0)
        except (TypeError, ValueError):
            index = 0
        if action == "remove" and 0 <= index < len(ids):
            ids.pop(index)
        elif action == "up" and 0 < index < len(ids):
            ids[index - 1], ids[index] = ids[index], ids[index - 1]
        elif action == "down" and 0 <= index < len(ids) - 1:
            ids[index + 1], ids[index] = ids[index], ids[index + 1]
        if not ids:
            session.pop(_shortlist_key(contact_id), None)
            return redirect(url_for("contacts_detail", contact_id=contact_id))
        from modules.property_shortlist import (
            ShortlistError,
            attach_match_scores,
            draft_whatsapp_message,
            select_properties,
        )

        try:
            items = attach_match_scores(
                organization_id,
                contact,
                select_properties(
                    organization_id,
                    contact["id"],
                    ids,
                    agent_id=agent_id,
                ),
                agent_id=agent_id,
            )
        except ShortlistError as error:
            return _reject_shortlist(contact_id, error)
        collection_url, token, mode, days = _attach_share_links(
            organization_id,
            agent_id,
            contact,
            items,
            share,
        )
        share = {
            "mode": mode,
            "share_days": days,
            "collection_token": token,
            "collection_url": collection_url if mode == "collection" else "",
        }
        if action == "mode":
            edited = draft_whatsapp_message(
                contact,
                items,
                language=language,
                collection_url=share["collection_url"],
            )
        else:
            edited = request.form.get("message")
            if edited is None:
                edited = message
        _save_shortlist(contact, items, edited, share)
        return redirect(url_for("contacts_property_shortlist", contact_id=contact_id))

    @app.route("/contacts/<int:contact_id>/shortlist/open", methods=["POST"])
    @login_required
    def contacts_property_shortlist_open(contact_id):
        from modules.property_match import whatsapp_share_url
        from modules.property_shortlist import (
            ShortlistError,
            attach_match_scores,
            record_shortlist_share,
            select_properties,
        )

        _user, organization_id, agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage or agent_id is None:
            abort(403)
        language = get_current_language()
        try:
            items = attach_match_scores(
                organization_id,
                contact,
                select_properties(
                    organization_id,
                    contact["id"],
                    request.form.getlist("property_id"),
                    agent_id=agent_id,
                ),
                agent_id=agent_id,
            )
        except ShortlistError as error:
            return _reject_shortlist(contact_id, error)
        message = (request.form.get("message") or "").strip()
        if not message:
            flash_i18n("shortlist_err_empty", "error")
            return redirect(url_for("contacts_property_shortlist", contact_id=contact_id))
        stored = session.get(_shortlist_key(contact_id)) or {}
        posted_mode = request.form.get("share_mode") or stored.get("mode") or "individual"
        posted_days = request.form.get("share_days") or stored.get("share_days") or 30
        collection_url, token, mode, days = _attach_share_links(
            organization_id,
            agent_id,
            contact,
            items,
            {
                "mode": posted_mode,
                "share_days": posted_days,
                "collection_token": stored.get("collection_token") or "",
            },
        )
        url = whatsapp_share_url(contact.get("phone"), message)
        if url is None:
            flash_i18n("contacts_add_phone", "error")
            return redirect(url_for("contacts_property_shortlist", contact_id=contact_id))
        updated, _written = record_shortlist_share(
            organization_id,
            contact,
            items,
            agent_id=agent_id,
            now=now_utc(),
            tz=organization_timezone(organization_id),
            language=language,
        )
        session[_shortlist_key(contact_id)] = {
            "property_ids": [item["property_id"] for item in items],
            "message": message,
            "whatsapp_url": url,
            "opened": True,
            "mode": mode,
            "share_days": days,
            "collection_token": token if mode == "collection" else "",
            "collection_url": collection_url if mode == "collection" else "",
        }
        session["property_shortlist_contact"] = updated.get("id")
        return redirect(url_for("contacts_property_shortlist_sent", contact_id=contact_id))

    @app.route("/contacts/<int:contact_id>/shortlist/sent")
    @login_required
    def contacts_property_shortlist_sent(contact_id):
        _user, _organization_id, _agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        stored = session.get(_shortlist_key(contact_id)) or {}
        if not stored.get("whatsapp_url"):
            return redirect(url_for("contacts_detail", contact_id=contact_id))
        return render_template(
            "contacts/shortlist_sent.html",
            contact=contact,
            whatsapp_url=stored["whatsapp_url"],
            can_manage=can_manage,
            collection_token=stored.get("collection_token") or "",
        )

    @app.route("/contacts/<int:contact_id>/shortlist/follow-up", methods=["POST"])
    @login_required
    def contacts_property_shortlist_follow_up(contact_id):
        from datetime import datetime

        from modules.contact_follow_up import FollowUpInputError, postpone_follow_up
        from modules.database.contacts_repository import update_contact
        from modules.organization_time import to_utc_iso

        _user, organization_id, _agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        days = request.form.get("days")
        when = (request.form.get("date") or "").strip()
        try:
            if days:
                postpone_follow_up(contact, now=now_utc(), days=int(days))
            elif when:
                tz = organization_timezone(organization_id)
                local = datetime.strptime(when, "%Y-%m-%d").replace(
                    hour=9,
                    minute=0,
                    tzinfo=tz,
                )
                if local <= now_utc().astimezone(tz):
                    raise FollowUpInputError("date")
                update_contact(
                    contact["id"],
                    organization_id,
                    next_follow_up_at=to_utc_iso(local),
                )
            else:
                raise FollowUpInputError("empty")
        except (FollowUpInputError, ValueError):
            flash_i18n("contacts_err_invalid_follow_up", "error")
            return redirect(
                url_for("contacts_property_shortlist_sent", contact_id=contact_id)
            )
        flash_i18n("shortlist_followup_saved", "success")
        return redirect(url_for("contacts_detail", contact_id=contact_id))

    @app.route("/contacts/<int:contact_id>/shortlist/revoke", methods=["POST"])
    @login_required
    def contacts_property_shortlist_revoke(contact_id):
        from modules.public_share import deactivate_shortlist

        _user, organization_id, _agent_id, can_manage, contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        deactivate_shortlist(
            organization_id,
            (request.form.get("token") or "").strip(),
            contact_id=contact["id"],
        )
        flash_i18n("public_revoked", "success")
        return redirect(url_for("contacts_property_shortlist_sent", contact_id=contact_id))

    @app.route("/contacts/<int:contact_id>/shortlist/revoke-property", methods=["POST"])
    @login_required
    def contacts_property_shortlist_revoke_property(contact_id):
        from modules.database.properties_repository import get_property_record
        from modules.public_share import revoke_property_link

        _user, organization_id, agent_id, can_manage, _contact = _scoped_contact(
            contact_id
        )
        if not can_manage:
            abort(403)
        try:
            property_id = int(request.form.get("revoke_property_id") or 0)
        except (TypeError, ValueError):
            property_id = 0
        record = get_property_record(property_id, organization_id)
        if record is None or (
            agent_id is not None and record.get("agent_id") != agent_id
        ):
            abort(404)
        revoke_property_link(organization_id, property_id)
        flash_i18n("public_revoked", "success")
        return redirect(url_for("contacts_property_shortlist", contact_id=contact_id))

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

        if can_manage and agent_id is not None:
            for card in selected:
                listing = card.get("listing") or {}
                record_property_interaction(
                    organization_id,
                    agent_id,
                    contact_id=contact["id"],
                    property_id=card.get("property_id") or listing.get("property_id"),
                    interaction_type="shared",
                    label=(
                        listing.get("address")
                        or listing.get("title")
                        or card.get("title")
                        or ""
                    ),
                )

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
