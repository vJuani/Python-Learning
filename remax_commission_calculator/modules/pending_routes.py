"""
Pending Center routes (Phase 4A).

These routes only read derived state and navigate to the existing
flows. No module logic is duplicated here and no financial movement is
ever created from a pending action.
"""

from __future__ import annotations

from flask import abort, render_template, request

from modules.auth import (
    get_current_user,
    is_agent,
    is_guest_session,
    login_required,
)
from modules.pending_actions import (
    STAFF_CATEGORIES,
    build_agent_pending_actions,
    build_staff_pending_actions,
    filter_pending_actions,
)


def register_pending_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]

    @app.route("/pendings")
    @login_required
    def pendings_center():
        if is_guest_session():
            abort(403)

        user = get_current_user()

        if user is None:
            abort(403)

        organization_id = require_user_organization()
        language = get_current_language()
        scope = "agent" if is_agent(user) else "staff"

        if scope == "agent":
            agent_id = user.get("agent_id")

            if agent_id is None:
                abort(403)

            actions = build_agent_pending_actions(
                organization_id,
                agent_id,
                user_id=user["id"],
                language=language,
            )
            categories = []
            selected_category = "all"
        else:
            actions = build_staff_pending_actions(
                organization_id,
                language=language,
            )
            available = {action["category"] for action in actions}
            categories = [
                category
                for category in STAFF_CATEGORIES
                if category in available
            ]
            selected_category = (
                request.args.get("category") or "all"
            ).strip()

            if selected_category not in categories:
                selected_category = "all"

        return render_template(
            "pendings/index.html",
            scope=scope,
            pending_actions=filter_pending_actions(
                actions,
                selected_category,
            ),
            pending_total=len(actions),
            pending_categories=categories,
            selected_category=selected_category,
        )
