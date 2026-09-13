"""Agent professional photo HTTP. Org-scoped. Agent or admin only."""

from __future__ import annotations

from flask import abort, redirect, render_template, request, send_file, url_for

from modules.agent_branding import can_edit_agent_photo, get_agent_branding
from modules.agent_photo import (
    AgentPhotoError,
    delete_agent_profile_photo,
    resolve_agent_photo_path,
    save_agent_profile_photo,
)
from modules.auth import ROLE_ADMIN, ROLE_AGENT, get_current_user
from modules.database.agents_repository import get_agent_record


def register_agent_photo_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    flash_i18n = helpers["flash_i18n"]
    get_current_language = helpers["get_current_language"]

    def _actor():
        return get_current_user()

    def _target_agent(user, organization_id, agent_id=None):
        if agent_id is None:
            agent_id = user.get("agent_id") if user else None
        if not agent_id:
            return None
        return get_agent_record(agent_id, organization_id)

    @app.route("/profile/photo", methods=["GET", "POST"])
    def agent_photo_profile():
        user = _actor()
        if not user or user.get("role") != ROLE_AGENT or not user.get("agent_id"):
            return ("Forbidden", 403)
        organization_id = require_user_organization()
        agent = _target_agent(user, organization_id)
        if agent is None:
            abort(404)
        return _handle_photo_form(
            user,
            agent,
            organization_id,
            success_endpoint="agent_photo_profile",
        )

    @app.route("/agents/<int:agent_id>/photo", methods=["GET", "POST"])
    def agent_photo_edit(agent_id):
        user = _actor()
        organization_id = require_user_organization()
        agent = _target_agent(user, organization_id, agent_id)
        if agent is None:
            abort(404)
        if not can_edit_agent_photo(user, agent):
            return ("Forbidden", 403)
        return _handle_photo_form(
            user,
            agent,
            organization_id,
            success_endpoint="agent_photo_edit",
            endpoint_kwargs={"agent_id": agent_id},
        )

    @app.route("/agents/<int:agent_id>/profile-photo")
    def agent_profile_photo(agent_id):
        user = _actor()
        if not user:
            return ("Forbidden", 403)
        organization_id = require_user_organization()
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            abort(404)
        same_org = int(user.get("organization_id") or 0) == int(organization_id)
        if not same_org:
            return ("Forbidden", 403)
        path = resolve_agent_photo_path(agent)
        if path is None:
            abort(404)
        mime = agent.get("profile_photo_mime") or "image/png"
        return send_file(path, mimetype=mime)

    def _handle_photo_form(user, agent, organization_id, success_endpoint, endpoint_kwargs=None):
        language = get_current_language()
        branding = get_agent_branding(agent["id"], organization_id, language=language)
        if request.method == "POST":
            action = request.form.get("action") or "upload"
            try:
                if action == "delete":
                    delete_agent_profile_photo(organization_id, agent["id"])
                    flash_i18n("agent_photo_deleted", "success")
                else:
                    storage = request.files.get("photo")
                    if storage is None or not storage.filename:
                        raise AgentPhotoError("agent_photo_err_missing")
                    save_agent_profile_photo(
                        organization_id,
                        agent["id"],
                        storage,
                        content_type=storage.mimetype,
                    )
                    flash_i18n("agent_photo_saved", "success")
            except AgentPhotoError as error:
                flash_i18n(error.message_key, "error")
            return redirect(url_for(success_endpoint, **(endpoint_kwargs or {})))
        return render_template(
            "agents/photo.html",
            agent=agent,
            branding=branding,
            can_edit=can_edit_agent_photo(user, agent),
            photo_is_admin=user.get("role") == ROLE_ADMIN,
        )
