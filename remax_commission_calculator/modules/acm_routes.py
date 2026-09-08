"""Agent-only ACM HTTP routes. Staff/admin/guest → 403."""

from __future__ import annotations

from flask import (
    abort,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from modules.acm_service import (
    AcmError,
    add_manual_comparable,
    agent_contact_for_acm,
    complete_property_area_and_create,
    create_acm_for_property,
    duplicate_acm,
    finalize_acm,
    get_acm_view,
    list_agent_acms,
    override_comparable_area,
    preview_acm_property,
    recalculate_acm,
    refresh_draft,
    require_acm_agent,
    set_comparable_selected,
    simulate_list_price,
)
from modules.auth import get_current_user, is_guest_session
from modules.pdf_acm_report import generate_acm_pdf_bytes


def register_acm_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    flash_i18n = helpers["flash_i18n"]

    def _forbidden():
        # Return a real 403. abort(403) is rewritten to a dashboard
        # redirect by the global errorhandler.
        return ("Forbidden", 403)

    def _agent_user():
        if is_guest_session():
            return None
        try:
            return require_acm_agent(get_current_user())
        except AcmError:
            return None

    def _handle(error):
        if error.status_code == 403:
            return _forbidden()
        if error.status_code == 404:
            abort(404)
        flash_i18n(error.message_key, "error")
        return redirect(url_for("acm_list"))

    @app.route("/acm")
    def acm_list():
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        items = list_agent_acms(organization_id, user=user)
        return render_template("acm/list.html", items=items)

    @app.route("/acm/new", methods=["GET", "POST"])
    def acm_new():
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        language = get_current_language()
        property_id = request.values.get("property_id", type=int)
        if request.method == "GET" and not property_id:
            return render_template("acm/new.html")
        if not property_id:
            flash_i18n("acm_err_property_missing", "error")
            return redirect(url_for("acm_list"))
        if request.method == "GET":
            try:
                preview = preview_acm_property(
                    organization_id,
                    user=user,
                    property_id=property_id,
                    language=language,
                )
            except AcmError as error:
                return _handle(error)
            if (preview.get("quality") or {}).get("can_valuate"):
                try:
                    view = create_acm_for_property(
                        organization_id,
                        user=user,
                        property_id=property_id,
                        language=language,
                    )
                except AcmError as error:
                    return _handle(error)
                return redirect(url_for("acm_detail", acm_id=view["acm"]["id"]))
            return render_template("acm/new.html", preview=preview)
        try:
            area = request.form.get("total_m2") or request.form.get("area")
            if area:
                view = complete_property_area_and_create(
                    organization_id,
                    user=user,
                    property_id=property_id,
                    area=area,
                    language=language,
                )
            else:
                view = create_acm_for_property(
                    organization_id,
                    user=user,
                    property_id=property_id,
                    language=language,
                )
        except AcmError as error:
            return _handle(error)
        return redirect(url_for("acm_detail", acm_id=view["acm"]["id"]))

    @app.route("/acm/<int:acm_id>")
    def acm_detail(acm_id):
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            view = get_acm_view(
                acm_id,
                organization_id,
                user=user,
                language=get_current_language(),
            )
        except AcmError as error:
            return _handle(error)
        return render_template("acm/detail.html", view=view)

    @app.route("/acm/<int:acm_id>/scenario", methods=["POST"])
    def acm_scenario(acm_id):
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        language = get_current_language()
        try:
            view = simulate_list_price(
                acm_id,
                organization_id,
                user=user,
                proposed_price=request.form.get("proposed_price"),
                language=language,
            )
        except AcmError as error:
            return _handle(error)
        return render_template("acm/detail.html", view=view)

    @app.route("/acm/<int:acm_id>/comparables", methods=["GET", "POST"])
    def acm_comparables(acm_id):
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        language = get_current_language()
        if request.method == "POST":
            try:
                if request.form.get("action") == "manual":
                    add_manual_comparable(
                        acm_id,
                        organization_id,
                        user=user,
                        payload={
                            "reference": request.form.get("reference"),
                            "address": request.form.get("address"),
                            "location": request.form.get("location"),
                            "neighborhood": request.form.get("neighborhood"),
                            "url": request.form.get("url"),
                            "price": request.form.get("price"),
                            "currency": request.form.get("currency") or "USD",
                            "area": request.form.get("area"),
                            "covered_m2": request.form.get("covered_m2"),
                            "total_m2": request.form.get("total_m2"),
                            "rooms": request.form.get("rooms"),
                            "bedrooms": request.form.get("bedrooms"),
                            "bathrooms": request.form.get("bathrooms"),
                            "parking_spaces": request.form.get("parking_spaces"),
                            "property_type": request.form.get("property_type"),
                            "price_kind": request.form.get("price_kind"),
                            "observed_at": request.form.get("observed_at"),
                            "notes": request.form.get("notes"),
                        },
                        language=language,
                    )
                elif request.form.get("action") == "complete":
                    override_comparable_area(
                        acm_id,
                        organization_id,
                        user=user,
                        comparable_id=int(request.form.get("comparable_id")),
                        area=request.form.get("area"),
                        language=language,
                    )
                elif request.form.get("action") in {"toggle", "exclude", "include"}:
                    selected = request.form.get("selected") == "1"
                    if request.form.get("action") == "exclude":
                        selected = False
                    if request.form.get("action") == "include":
                        selected = True
                    set_comparable_selected(
                        acm_id,
                        organization_id,
                        user=user,
                        comparable_id=int(request.form.get("comparable_id")),
                        selected=selected,
                        language=language,
                    )
                elif request.form.get("action") == "refresh":
                    refresh_draft(
                        acm_id,
                        organization_id,
                        user=user,
                        language=language,
                        filters={
                            "same_zone": request.form.get("same_zone") == "1",
                            "area_pct": request.form.get("area_pct") or "0.20",
                            "rooms_delta": request.form.get("rooms_delta") or "1",
                            "max_age_months": request.form.get("max_age_months") or None,
                            "include_closing": request.form.get("include_closing") == "1",
                            "include_listing": request.form.get("include_listing") == "1",
                        },
                    )
                else:
                    recalculate_acm(
                        acm_id,
                        organization_id,
                        user=user,
                        language=language,
                    )
            except AcmError as error:
                return _handle(error)
            return redirect(url_for("acm_comparables", acm_id=acm_id))
        try:
            view = get_acm_view(
                acm_id,
                organization_id,
                user=user,
                language=language,
            )
        except AcmError as error:
            return _handle(error)
        confirm_id = request.args.get("confirm_exclude", type=int)
        confirm_row = None
        if confirm_id:
            confirm_row = next(
                (
                    row
                    for row in view["comparables"]
                    if int(row.get("id") or 0) == confirm_id
                ),
                None,
            )
        return render_template(
            "acm/comparables.html",
            view=view,
            confirm_exclude=confirm_row,
        )

    @app.route("/acm/<int:acm_id>/finalize", methods=["POST"])
    def acm_finalize(acm_id):
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            view = finalize_acm(
                acm_id,
                organization_id,
                user=user,
                language=get_current_language(),
            )
        except AcmError as error:
            return _handle(error)
        flash_i18n("acm_finalized", "success")
        return redirect(url_for("acm_detail", acm_id=view["acm"]["id"]))

    @app.route("/acm/<int:acm_id>/duplicate", methods=["POST"])
    def acm_duplicate(acm_id):
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            view = duplicate_acm(
                acm_id,
                organization_id,
                user=user,
                language=get_current_language(),
            )
        except AcmError as error:
            return _handle(error)
        return redirect(url_for("acm_detail", acm_id=view["acm"]["id"]))

    @app.route("/acm/<int:acm_id>/exclude", methods=["POST"])
    def acm_exclude(acm_id):
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            set_comparable_selected(
                acm_id,
                organization_id,
                user=user,
                comparable_id=int(request.form.get("comparable_id")),
                selected=False,
                language=get_current_language(),
            )
        except AcmError as error:
            return _handle(error)
        return redirect(url_for("acm_detail", acm_id=acm_id))

    @app.route("/acm/<int:acm_id>/pdf")
    def acm_pdf(acm_id):
        user = _agent_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        include_agent = request.args.get("include_agent") != "0"
        try:
            view = get_acm_view(
                acm_id,
                organization_id,
                user=user,
                language=get_current_language(),
            )
        except AcmError as error:
            return _handle(error)
        view["agent_contact"] = agent_contact_for_acm(view) if include_agent else {}
        payload = generate_acm_pdf_bytes(
            view,
            include_agent=include_agent,
            language=get_current_language(),
        )
        filename = "ACM.pdf"
        address = (view.get("subject") or {}).get("address") or "ACM"
        safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in address)
        filename = f"ACM_{safe[:40]}.pdf"
        return send_file(
            payload,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
