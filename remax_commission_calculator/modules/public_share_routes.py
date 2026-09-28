"""Login-free /p/<token> and /s/<token> pages."""

from __future__ import annotations

from flask import Response, abort, render_template, request, url_for

from modules.database.properties_repository import get_property_record
from modules.i18n import translate
from modules.inbound_inquiry import (
    InboundClosed,
    allow_inquiry_rate,
    form_token_ok,
    issue_form_token,
    submit_property_inquiry,
    submit_shortlist_inquiry,
    validate_public_inquiry,
)
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

    def _form_state(kind, token, *, open_form, errors=None, values=None):
        language = get_current_language()
        return {
            "inquiry_open": bool(open_form),
            "inquiry_action": url_for(
                "public_property_inquiry" if kind == "property" else "public_shortlist_inquiry",
                token=token,
            ),
            "form_token": issue_form_token(kind, token) if open_form else "",
            "inquiry_errors": [
                translate(key, language) for key in (errors or [])
            ],
            "inquiry_values": values or {},
        }

    def _sent(payload):
        page = public_context(payload)
        return render_template("public/inquiry_sent.html", office=page.get("office") or {})

    def _accept_form(kind, token, payload, *, open_form):
        language = get_current_language()
        if not allow_inquiry_rate(token):
            limited = render_template(
                "public/inquiry_sent.html",
                office=(public_context(payload).get("office") or {}),
                limited=True,
            )
            return (limited, 429), None, None
        if not form_token_ok(request.form.get("form_token"), kind, token):
            page_errors = ["public_inquiry_err_token"]
            return None, page_errors, {}
        if (request.form.get("company_website") or "").strip():
            return _sent(payload), None, None
        checked = validate_public_inquiry(
            request.form.get("name"),
            request.form.get("phone"),
            request.form.get("email"),
            request.form.get("message"),
        )
        if checked["errors"]:
            return None, checked["errors"], checked
        try:
            if kind == "property":
                submit_property_inquiry(
                    token,
                    name=checked["name"],
                    phone=checked["phone"],
                    email=checked["email"],
                    message=checked["message"],
                    language=language,
                )
            else:
                submit_shortlist_inquiry(
                    token,
                    name=checked["name"],
                    phone=checked["phone"],
                    email=checked["email"],
                    message=checked["message"],
                    language=language,
                )
        except InboundClosed:
            return "closed", None, None
        return _sent(payload), None, None

    @app.route("/p/<token>")
    def public_property(token):
        language = get_current_language()
        status, payload = resolve_property(token, language=language, base_url=_base())
        if status == "revoked":
            return _gone()
        if status != "ok":
            return _missing()
        mark_opened(KIND_PROPERTY, payload)
        page = public_context(payload)
        page.update(
            _form_state(
                "property",
                token,
                open_form=payload["listing"]["available"],
            )
        )
        return render_template("public/property.html", **page)

    @app.post("/p/<token>/inquiry")
    def public_property_inquiry(token):
        language = get_current_language()
        status, payload = resolve_property(token, language=language, base_url=_base())
        if status == "revoked":
            return _gone()
        if status != "ok" or not payload["listing"]["available"]:
            return _missing()
        outcome, errors, values = _accept_form(
            "property",
            token,
            payload,
            open_form=True,
        )
        if outcome == "closed":
            return _missing()
        if outcome is not None:
            return outcome
        page = public_context(payload)
        page.update(
            _form_state(
                "property",
                token,
                open_form=True,
                errors=errors,
                values=values,
            )
        )
        return render_template("public/property.html", **page), 400

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
        page = public_context(payload)
        page.update(
            _form_state(
                "shortlist",
                token,
                open_form=any(card.get("available") for card in payload.get("cards") or []),
            )
        )
        return render_template("public/shortlist.html", **page)

    @app.post("/s/<token>/inquiry")
    def public_shortlist_inquiry(token):
        language = get_current_language()
        status, payload = resolve_shortlist(token, language=language, base_url=_base())
        if status in ("revoked", "expired"):
            return _gone()
        if status != "ok" or not any(
            card.get("available") for card in payload.get("cards") or []
        ):
            return _missing()
        outcome, errors, values = _accept_form(
            "shortlist",
            token,
            payload,
            open_form=True,
        )
        if outcome == "closed":
            return _gone() if status in ("revoked", "expired") else _missing()
        if outcome is not None:
            return outcome
        page = public_context(payload)
        page.update(
            _form_state(
                "shortlist",
                token,
                open_form=True,
                errors=errors,
                values=values,
            )
        )
        return render_template("public/shortlist.html", **page), 400
