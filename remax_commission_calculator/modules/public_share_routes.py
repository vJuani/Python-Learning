"""Login-free /p/<token> and /s/<token> pages."""

from __future__ import annotations

from flask import Response, abort, render_template, request

from modules.database.properties_repository import get_property_record
from modules.public_share import (
    KIND_PROPERTY,
    KIND_SHORTLIST,
    agent_photo_file,
    mark_opened,
    office_logo_file,
    photo_file,
    public_context,
    resolve_property,
    resolve_shortlist,
)


def register_public_share_routes(app, helpers):
    get_current_language = helpers["get_current_language"]

    def _base():
        return (request.url_root or "").rstrip("/")

    def _missing():
        return render_template("public/missing.html", gone=False), 404

    def _gone():
        return render_template("public/missing.html", gone=True), 410

    @app.route("/p/<token>")
    def public_property(token):
        language = get_current_language()
        status, payload = resolve_property(token, language=language, base_url=_base())
        if status == "revoked":
            return _gone()
        if status != "ok":
            return _missing()
        mark_opened(KIND_PROPERTY, payload)
        return render_template("public/property.html", **public_context(payload))

    @app.route("/p/<token>/photo/<int:index>")
    def public_property_photo(token, index):
        status, payload = resolve_property(token, base_url=_base())
        if status != "ok":
            abort(404)
        link_org = payload["_organization_id"]
        from modules.database.public_share_repository import get_property_link_by_token

        link = get_property_link_by_token(token)
        path, content_type = photo_file(link_org, link["property_id"], index)
        if path is None:
            abort(404)
        return Response(path.read_bytes(), mimetype=content_type)

    @app.route("/p/<token>/agent")
    def public_property_agent(token):
        status, payload = resolve_property(token, base_url=_base())
        if status != "ok":
            abort(404)
        from modules.database.public_share_repository import get_property_link_by_token

        link = get_property_link_by_token(token)
        record = get_property_record(link["property_id"], link["organization_id"])
        path = agent_photo_file(record)
        if path is None:
            abort(404)
        return Response(path.read_bytes(), mimetype="image/jpeg")

    @app.route("/p/<token>/logo")
    def public_property_logo(token):
        status, payload = resolve_property(token, base_url=_base())
        if status != "ok":
            abort(404)
        path = office_logo_file(payload["_organization_id"])
        if path is None:
            abort(404)
        return Response(path.read_bytes(), mimetype="image/png")

    @app.route("/s/<token>")
    def public_shortlist(token):
        language = get_current_language()
        status, payload = resolve_shortlist(token, language=language, base_url=_base())
        if status in ("revoked", "expired"):
            return _gone()
        if status != "ok":
            return _missing()
        mark_opened(KIND_SHORTLIST, payload)
        return render_template("public/shortlist.html", **public_context(payload))
