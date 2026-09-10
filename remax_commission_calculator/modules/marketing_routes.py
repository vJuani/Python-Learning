"""Marketing IA HTTP routes. Logic lives in marketing_service."""

from __future__ import annotations

from flask import (
    abort,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from modules.auth import get_current_user, is_admin, is_agent, is_guest_session
from modules.marketing_context import MarketingError
from modules.marketing_service import (
    generate_marketing_proposals,
    get_asset_view,
    get_generation_view,
    list_marketing_home,
    prepare_create_view,
    require_asset_access,
    resolve_asset_file,
    select_marketing_asset,
    update_asset_copy,
)
from modules.database.marketing_repository import get_marketing_asset


def register_marketing_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    flash_i18n = helpers["flash_i18n"]

    def _forbidden():
        return ("Forbidden", 403)

    def _marketing_user():
        if is_guest_session():
            return None
        user = get_current_user()
        if user is None:
            return None
        if is_admin(user) or is_agent(user):
            return user
        return None

    def _handle(error, fallback_endpoint="marketing_home"):
        if error.status_code == 403:
            return _forbidden()
        if error.status_code == 404:
            abort(404)
        flash_i18n(error.message_key, "error")
        return redirect(url_for(fallback_endpoint))

    @app.route("/marketing")
    def marketing_home():
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        view = list_marketing_home(
            organization_id,
            user,
            language=get_current_language(),
        )
        return render_template("marketing/home.html", view=view)

    @app.route("/marketing/new", methods=["GET"])
    def marketing_new():
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        language = get_current_language()
        property_id = request.args.get("property_id", type=int)
        try:
            view = prepare_create_view(
                organization_id,
                user,
                property_id=property_id,
                language=language,
            )
        except MarketingError as error:
            return _handle(error)
        return render_template(
            "marketing/new.html",
            view=view,
            selected_format=request.args.get("format") or "story",
            selected_style=request.args.get("style") or "elegant",
            selected_tone=request.args.get("tone") or "professional",
        )

    @app.route("/marketing/generate", methods=["POST"])
    def marketing_generate():
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        property_id = request.form.get("property_id", type=int)
        if not property_id:
            flash_i18n("marketing_err_property_missing", "error")
            return redirect(url_for("marketing_new"))
        try:
            result = generate_marketing_proposals(
                organization_id,
                user,
                property_id=property_id,
                form=request.form,
                language=get_current_language(),
            )
        except MarketingError as error:
            return _handle(error, "marketing_new")
        return redirect(
            url_for("marketing_proposals", generation_id=result["generation_id"])
        )

    @app.route("/marketing/generation/<generation_id>")
    def marketing_proposals(generation_id):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            view = get_generation_view(
                organization_id,
                user,
                generation_id,
                language=get_current_language(),
            )
        except MarketingError as error:
            return _handle(error)
        return render_template("marketing/proposals.html", view=view)

    @app.route("/marketing/assets/<int:asset_id>")
    def marketing_asset(asset_id):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            asset = get_asset_view(
                organization_id,
                user,
                asset_id,
                language=get_current_language(),
            )
        except MarketingError as error:
            return _handle(error)
        return render_template("marketing/asset.html", asset=asset)

    @app.route("/marketing/assets/<int:asset_id>/select", methods=["POST"])
    def marketing_select(asset_id):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            select_marketing_asset(organization_id, user, asset_id)
        except MarketingError as error:
            return _handle(error)
        return redirect(url_for("marketing_asset", asset_id=asset_id))

    @app.route("/marketing/assets/<int:asset_id>/edit", methods=["POST"])
    def marketing_edit(asset_id):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            update_asset_copy(
                organization_id,
                user,
                asset_id,
                request.form,
                language=get_current_language(),
            )
        except MarketingError as error:
            return _handle(error)
        flash_i18n("marketing_saved", "success")
        return redirect(url_for("marketing_asset", asset_id=asset_id))

    def _send_asset(asset_id, *, kind, as_attachment):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            asset = require_asset_access(
                user, get_marketing_asset(asset_id, organization_id)
            )
        except MarketingError as error:
            return _handle(error)
        path = resolve_asset_file(asset, kind=kind)
        if path is None:
            abort(404)
        mime = "application/pdf" if kind == "pdf" else "image/png"
        download_name = f"jrh-{asset.get('format')}-{asset_id}.{kind}"
        return send_file(
            path,
            mimetype=mime,
            as_attachment=as_attachment,
            download_name=download_name,
        )

    @app.route("/marketing/assets/<int:asset_id>/preview")
    def marketing_preview(asset_id):
        return _send_asset(asset_id, kind="png", as_attachment=False)

    @app.route("/marketing/assets/<int:asset_id>/download.png")
    def marketing_download_png(asset_id):
        return _send_asset(asset_id, kind="png", as_attachment=True)

    @app.route("/marketing/assets/<int:asset_id>/download.pdf")
    def marketing_download_pdf(asset_id):
        return _send_asset(asset_id, kind="pdf", as_attachment=True)
