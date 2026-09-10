"""Staff/Admin property integration UI. Agents cannot configure the office feed."""

from __future__ import annotations

from pathlib import Path

from flask import abort, redirect, render_template, request, send_file, url_for

from modules.auth import admin_required, get_current_user, is_guest_session, login_required
from modules.database.properties_repository import get_property_record
from modules.property_media_access import (
    PropertyMediaError,
    require_property_media_access,
)
from modules.database.property_media_repository import get_property_media
from modules.property_sync.media import resolve_media_filesystem_path
from modules.property_sync.service import (
    PropertySyncError,
    SyncInProgressError,
    diagnose_redremax_connection,
    dry_run_property_sync,
    integration_dashboard,
    require_sync_admin,
    resolve_conflict,
    run_property_sync,
    test_property_source_connection,
    update_redremax_office,
)
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX


def register_property_sync_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    flash_i18n = helpers["flash_i18n"]

    def _admin_user():
        if is_guest_session():
            raise PropertySyncError("sync_err_admin_only", 403)
        return require_sync_admin(get_current_user())

    @app.route("/settings/integrations/properties")
    @admin_required
    def settings_property_integrations():
        try:
            _admin_user()
        except PropertySyncError:
            abort(403)
        organization_id = require_user_organization()
        language = get_current_language()
        view = integration_dashboard(organization_id, language=language)
        return render_template("settings/property_integrations.html", view=view)

    @app.route("/settings/integrations/properties/sync", methods=["POST"])
    @admin_required
    def settings_property_integrations_sync():
        try:
            _admin_user()
        except PropertySyncError:
            abort(403)
        organization_id = require_user_organization()
        provider = (request.form.get("provider") or "mock_network").strip()
        try:
            result = run_property_sync(
                organization_id,
                provider,
                language=get_current_language(),
            )
            flash_i18n("sync_run_done", "success")
        except SyncInProgressError:
            flash_i18n("sync_err_in_progress", "error")
        except PropertySyncError as error:
            flash_i18n(error.message_key, "error")
        return redirect(url_for("settings_property_integrations"))

    @app.route("/settings/integrations/properties/office", methods=["POST"])
    @admin_required
    def settings_property_integrations_office():
        try:
            _admin_user()
        except PropertySyncError:
            abort(403)
        organization_id = require_user_organization()
        office_id = (request.form.get("external_office_id") or "").strip()
        update_redremax_office(organization_id, office_id)
        flash_i18n("redremax_office_saved", "success")
        return redirect(url_for("settings_property_integrations"))

    @app.route("/settings/integrations/properties/test", methods=["POST"])
    @admin_required
    def settings_property_integrations_test():
        try:
            _admin_user()
        except PropertySyncError:
            abort(403)
        organization_id = require_user_organization()
        provider = (request.form.get("provider") or PROVIDER_REDREMAX).strip()
        try:
            test_property_source_connection(organization_id, provider)
            flash_i18n("redremax_test_ok", "success")
        except PropertySyncError as error:
            flash_i18n(error.message_key, "error")
        return redirect(url_for("settings_property_integrations"))

    @app.route("/settings/integrations/properties/diagnose", methods=["POST"])
    @admin_required
    def settings_property_integrations_diagnose():
        try:
            _admin_user()
        except PropertySyncError:
            abort(403)
        organization_id = require_user_organization()
        try:
            diagnose_redremax_connection(organization_id)
            flash_i18n("redremax_diagnose_done", "success")
        except PropertySyncError as error:
            flash_i18n(error.message_key, "error")
        return redirect(url_for("settings_property_integrations"))

    @app.route("/settings/integrations/properties/dry-run", methods=["POST"])
    @admin_required
    def settings_property_integrations_dry_run():
        try:
            _admin_user()
        except PropertySyncError:
            abort(403)
        organization_id = require_user_organization()
        provider = (request.form.get("provider") or PROVIDER_REDREMAX).strip()
        try:
            dry_run_property_sync(organization_id, provider)
            flash_i18n("redremax_dry_run_done", "success")
        except PropertySyncError as error:
            flash_i18n(error.message_key, "error")
        return redirect(url_for("settings_property_integrations"))

    @app.route(
        "/settings/integrations/properties/conflicts/<int:conflict_id>",
        methods=["POST"],
    )
    @admin_required
    def settings_property_integration_conflict(conflict_id):
        try:
            _admin_user()
        except PropertySyncError:
            abort(403)
        organization_id = require_user_organization()
        action = (request.form.get("action") or "").strip()
        try:
            resolve_conflict(organization_id, conflict_id, action)
            flash_i18n("sync_conflict_resolved", "success")
        except PropertySyncError as error:
            flash_i18n(error.message_key, "error")
        return redirect(url_for("settings_property_integrations"))

    @app.route("/properties/<int:property_id>/gallery/<int:media_id>")
    @login_required
    def property_gallery_file(property_id, media_id):
        if is_guest_session():
            abort(403)
        user = get_current_user()
        organization_id = require_user_organization()
        property_data = get_property_record(property_id, organization_id)
        try:
            require_property_media_access(user, property_data, is_guest=False)
        except PropertyMediaError as error:
            abort(error.status_code)
        item = get_property_media(media_id, organization_id)
        if item is None or int(item["property_id"]) != int(property_id):
            abort(404)
        path = resolve_media_filesystem_path(item)
        if path is None:
            abort(404)
        return send_file(Path(path), mimetype=item.get("content_type") or "image/png")
