"""HTTP entry for global JRH IA. Writes never happen here."""

from __future__ import annotations

from flask import abort, render_template, request, url_for

from modules.auth import (
    get_current_user,
    is_agent,
    is_guest_session,
    login_required,
)
from modules.jrh_intent import (
    INTENT_AGENDA,
    INTENT_CONTACT,
    INTENT_INVOICE,
    INTENT_NAVIGATION,
    INTENT_OPERATION,
    INTENT_PENDING,
    INTENT_PROPERTY_SEARCH,
    JrhIntentError,
    interpret_jrh_request,
)


def attach_intent_urls(result):
    attached = dict(result or {})
    intents = []
    for item in attached.get("intents") or []:
        row = dict(item)
        data = row.get("data") or {}
        intent_type = row.get("type")
        href = ""
        if intent_type == INTENT_AGENDA:
            href = url_for("agenda_compose", prompt=data.get("source_prompt") or "")
        elif intent_type == INTENT_PROPERTY_SEARCH:
            contact_id = data.get("contact_id")
            if contact_id:
                href = url_for("contacts_property_matches", contact_id=contact_id)
            elif data.get("candidates"):
                href = url_for("contacts_index")
            else:
                href = url_for("contacts_index")
        elif intent_type == INTENT_CONTACT:
            if data.get("contact_id"):
                href = url_for("contacts_detail", contact_id=data["contact_id"])
            elif data.get("filter"):
                href = url_for("contacts_index", filter=data["filter"])
            else:
                href = url_for("contacts_index")
        elif intent_type == INTENT_PENDING:
            href = url_for("pendings_center")
        elif intent_type == INTENT_INVOICE:
            operation_id = data.get("operation_id")
            side = data.get("side")
            if operation_id and side:
                href = url_for(
                    "billing_prepare",
                    operation_id=operation_id,
                    side=side,
                )
            elif operation_id:
                href = url_for(
                    "billing_ai_select_operation",
                    operation_id=operation_id,
                )
            elif data.get("candidates"):
                for candidate in data["candidates"]:
                    if candidate.get("id"):
                        candidate["href"] = url_for(
                            "billing_ai_select_operation",
                            operation_id=candidate["id"],
                        )
                href = url_for(
                    "billing_ai_prepare",
                    prompt=data.get("source_prompt") or "",
                )
            else:
                href = url_for(
                    "billing_ai_prepare",
                    prompt=data.get("source_prompt") or "",
                )
        elif intent_type == INTENT_OPERATION:
            operation_id = data.get("operation_id")
            if operation_id:
                href = url_for("operations_detail", operation_id=operation_id)
            else:
                href = url_for("operations_list")
        elif intent_type == INTENT_NAVIGATION:
            target = data.get("target")
            endpoints = {
                "agenda": "agenda_index",
                "contacts": "contacts_index",
                "billing": "billing_list",
                "properties": "properties_list",
                "operations": "operations_list",
            }
            endpoint = endpoints.get(target)
            if endpoint:
                href = url_for(endpoint)
        row["href"] = href
        if intent_type == INTENT_AGENDA:
            row["cta_key"] = "jrh_cta_review"
        elif intent_type == INTENT_PROPERTY_SEARCH and data.get("contact_id"):
            row["cta_key"] = "jrh_cta_open_results"
        elif intent_type == INTENT_PENDING:
            row["cta_key"] = "jrh_cta_view"
        intents.append(row)
    attached["intents"] = intents
    return attached


def register_jrh_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    get_agent_scope = helpers["get_agent_scope"]
    get_agent_home_context = helpers["get_agent_home_context"]
    flash_i18n = helpers["flash_i18n"]

    @app.route("/jrh/interpret", methods=["POST"])
    @login_required
    def jrh_interpret():
        if is_guest_session():
            abort(403)
        user = get_current_user()
        organization_id = require_user_organization()
        agent_id, scope_blocked = get_agent_scope()
        if not is_agent(user) or scope_blocked or agent_id is None:
            abort(403)
        prompt = (request.form.get("prompt") or "").strip()
        language = get_current_language()
        try:
            result = attach_intent_urls(
                interpret_jrh_request(
                    prompt,
                    organization_id=organization_id,
                    agent_id=agent_id,
                    user_id=user.get("id"),
                    language=language,
                )
            )
        except JrhIntentError as error:
            flash_i18n(error.message_key, "error")
            result = None
        except Exception:
            flash_i18n("jrh_err_generic", "error")
            result = None
        return render_template(
            "dashboard/home_agent.html",
            **get_agent_home_context(
                organization_id,
                agent_id,
                jrh_result=result,
            ),
        )

    helpers["attach_intent_urls"] = attach_intent_urls
    return helpers
