"""Agent personal WhatsApp / Instagram contact channels."""

from __future__ import annotations

from flask import abort, redirect, render_template, request, url_for

from modules.agent_branding import can_edit_agent_photo
from modules.agent_contact_channels import (
    AgentContactError,
    link_agent_instagram,
    link_agent_whatsapp,
    resolve_agent_contact_channels,
    set_instagram_marketing_enabled,
    set_whatsapp_marketing_enabled,
    unlink_agent_instagram,
    unlink_agent_whatsapp,
)
from modules.auth import ROLE_ADMIN, ROLE_AGENT, get_current_user
from modules.database.agents_repository import get_agent_record


def register_agent_contact_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    flash_i18n = helpers["flash_i18n"]

    def _actor():
        return get_current_user()

    def _target_agent(user, organization_id, agent_id=None):
        if agent_id is None:
            agent_id = user.get("agent_id") if user else None
        if not agent_id:
            return None
        return get_agent_record(agent_id, organization_id)

    @app.route("/settings/contact", methods=["GET", "POST"])
    def agent_contact_channels_self():
        user = _actor()
        if not user or user.get("role") != ROLE_AGENT or not user.get("agent_id"):
            return ("Forbidden", 403)
        organization_id = require_user_organization()
        agent = _target_agent(user, organization_id)
        if agent is None:
            abort(404)
        return _handle_contact_form(
            user,
            agent,
            organization_id,
            success_endpoint="agent_contact_channels_self",
        )

    @app.route("/agents/<int:agent_id>/contact-channels", methods=["GET", "POST"])
    def agent_contact_channels_edit(agent_id):
        user = _actor()
        organization_id = require_user_organization()
        agent = _target_agent(user, organization_id, agent_id)
        if agent is None:
            abort(404)
        if not can_edit_agent_photo(user, agent):
            return ("Forbidden", 403)
        return _handle_contact_form(
            user,
            agent,
            organization_id,
            success_endpoint="agent_contact_channels_edit",
            endpoint_kwargs={"agent_id": agent_id},
        )

    def _handle_contact_form(user, agent, organization_id, success_endpoint, endpoint_kwargs=None):
        if request.method == "POST":
            action = request.form.get("action") or ""
            enable = request.form.get("use_in_marketing") == "1"
            try:
                if action == "link_whatsapp":
                    link_agent_whatsapp(
                        agent["id"],
                        organization_id,
                        request.form.get("whatsapp_number", ""),
                        enable_marketing=enable,
                    )
                    flash_i18n("agent_contact_whatsapp_saved", "success")
                elif action == "unlink_whatsapp":
                    unlink_agent_whatsapp(agent["id"], organization_id)
                    flash_i18n("agent_contact_whatsapp_removed", "success")
                elif action == "toggle_whatsapp":
                    set_whatsapp_marketing_enabled(
                        agent["id"],
                        organization_id,
                        request.form.get("enabled") == "1",
                    )
                    flash_i18n("agent_contact_updated", "success")
                elif action == "link_instagram":
                    link_agent_instagram(
                        agent["id"],
                        organization_id,
                        request.form.get("instagram_handle", ""),
                        enable_marketing=enable,
                    )
                    flash_i18n("agent_contact_instagram_saved", "success")
                elif action == "unlink_instagram":
                    unlink_agent_instagram(agent["id"], organization_id)
                    flash_i18n("agent_contact_instagram_removed", "success")
                elif action == "toggle_instagram":
                    set_instagram_marketing_enabled(
                        agent["id"],
                        organization_id,
                        request.form.get("enabled") == "1",
                    )
                    flash_i18n("agent_contact_updated", "success")
            except AgentContactError as error:
                flash_i18n(error.message_key, "error")
            return redirect(url_for(success_endpoint, **(endpoint_kwargs or {})))
        channels = resolve_agent_contact_channels(agent["id"], organization_id)
        return render_template(
            "settings/agent_contact_channels.html",
            agent=agent,
            channels=channels,
            can_edit=can_edit_agent_photo(user, agent),
            photo_is_admin=user.get("role") == ROLE_ADMIN,
            edit_whatsapp=request.args.get("edit") == "whatsapp",
            edit_instagram=request.args.get("edit") == "instagram",
        )
