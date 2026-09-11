"""Marketing IA HTTP routes. Logic lives in marketing_service."""

from __future__ import annotations

from flask import (
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from modules.auth import get_current_user, is_admin, is_agent, is_guest_session
from modules.marketing_context import MarketingError
from modules.marketing_service import (
    asset_download_name,
    cleanup_expired_marketing_assets,
    get_asset_view,
    get_batch_status,
    prepare_create_view,
    require_asset_access,
    resolve_asset_file,
    retry_marketing_item,
    select_marketing_asset,
    start_marketing_batch,
    vary_marketing_asset,
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

    def _handle(error, fallback_endpoint="marketing_new"):
        if error.status_code == 403:
            return _forbidden()
        if error.status_code == 404:
            abort(404)
        flash_i18n(error.message_key, "error")
        return redirect(url_for(fallback_endpoint))

    def _composer_url(result=None, **query):
        if result:
            query.setdefault("generation_id", result.get("generation_id"))
            query.setdefault("property_id", result.get("property_id"))
        return url_for("marketing_new", **{key: value for key, value in query.items() if value})

    @app.route("/marketing")
    def marketing_home():
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        cleanup_expired_marketing_assets(organization_id)
        return redirect(url_for("marketing_new"))

    @app.route("/marketing/new", methods=["GET"])
    def marketing_new():
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        language = get_current_language()
        property_id = request.args.get("property_id", type=int)
        generation_id = (request.args.get("generation_id") or "").strip() or None
        try:
            view = prepare_create_view(
                organization_id,
                user,
                property_id=property_id,
                prompt=request.args.get("prompt"),
                language=language,
                generation_id=generation_id,
            )
        except MarketingError as error:
            return _handle(error)
        return render_template("marketing/new.html", view=view)

    @app.route("/marketing/generate", methods=["POST"])
    def marketing_generate():
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        property_id = request.form.get("property_id", type=int)
        prompt = (request.form.get("prompt") or "").strip()
        if not property_id:
            flash_i18n("marketing_err_property_missing", "error")
            return redirect(url_for("marketing_new"))
        if not prompt:
            flash_i18n("marketing_err_prompt_missing", "error")
            return redirect(url_for("marketing_new", property_id=property_id))
        include_values = request.form.getlist("include_agent")
        include_agent = None
        if include_values:
            include_agent = include_values[-1] not in {"0", "off", "false", ""}
        try:
            result = start_marketing_batch(
                organization_id,
                user,
                property_id=property_id,
                prompt=prompt,
                language=get_current_language(),
                idempotency_key=(request.form.get("idempotency_key") or "").strip() or None,
                include_agent=include_agent if include_values else None,
            )
        except MarketingError as error:
            return _handle(error, "marketing_new")
        return redirect(_composer_url(result))

    @app.route("/marketing/generation/<generation_id>")
    def marketing_proposals(generation_id):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            view = prepare_create_view(
                organization_id,
                user,
                generation_id=generation_id,
                language=get_current_language(),
            )
        except MarketingError as error:
            return _handle(error)
        return render_template("marketing/new.html", view=view)

    @app.route("/marketing/batch/<batch_id>/status")
    def marketing_batch_status(batch_id):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            payload = get_batch_status(
                organization_id,
                user,
                batch_id,
                language=get_current_language(),
            )
        except MarketingError as error:
            if error.status_code == 403:
                return _forbidden()
            return jsonify({"error": error.message_key}), error.status_code
        return jsonify(payload)

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

    @app.route("/marketing/assets/<int:asset_id>/retry", methods=["POST"])
    def marketing_retry(asset_id):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            asset = retry_marketing_item(
                organization_id,
                user,
                asset_id,
                language=get_current_language(),
            )
        except MarketingError as error:
            return _handle(error)
        return redirect(
            _composer_url(
                generation_id=asset.get("generation_id"),
                property_id=asset.get("property_id"),
            )
        )

    @app.route("/marketing/assets/<int:asset_id>/vary", methods=["POST"])
    def marketing_vary(asset_id):
        user = _marketing_user()
        if user is None:
            return _forbidden()
        organization_id = require_user_organization()
        try:
            result = vary_marketing_asset(
                organization_id,
                user,
                asset_id,
                request.form.get("prompt"),
                language=get_current_language(),
            )
        except MarketingError as error:
            return _handle(error)
        return redirect(_composer_url(result))

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
        download_name = asset_download_name(asset, kind=kind)
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
