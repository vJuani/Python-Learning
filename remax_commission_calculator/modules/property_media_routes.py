"""
Property brochure and private documentation routes.
"""

from __future__ import annotations

import io

from flask import (
    abort,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from modules.auth import (
    can_write,
    get_current_user,
    is_guest_session,
    login_required,
    write_required,
)
from modules.property_brochure import generate_property_brochure
from modules.property_documents import (
    archive_property_document_safe,
    create_property_document_with_files,
    document_type_choices,
    list_documents_for_property,
    load_downloadable_normalized_pdf,
    load_downloadable_original_file,
)
from modules.property_inventory import decorate_property_for_display
from modules.property_media_access import (
    PropertyMediaError,
    require_property_media_access,
)
from modules.database.properties_repository import get_property_record


def register_property_media_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]
    flash_i18n = helpers["flash_i18n"]

    def _require_media_user():
        if is_guest_session():
            abort(403)
        user = get_current_user()
        if user is None:
            abort(403)
        return user

    def _handle_media_error(error, property_id=None):
        if error.status_code in (403, 404):
            abort(error.status_code)
        flash_i18n(error.message_key, "error")
        if property_id:
            return redirect(
                url_for("properties_detail", property_id=property_id)
            )
        return redirect(url_for("properties_list"))

    def _file_storages():
        files = request.files.getlist("files")
        if not files:
            single = request.files.get("file")
            if single is not None:
                files = [single]
        return [item for item in files if item and item.filename]

    @app.route(
        "/properties/<int:property_id>/brochure",
        methods=["GET", "POST"],
    )
    @login_required
    def property_brochure(property_id):
        user = _require_media_user()
        organization_id = require_user_organization()
        language = get_current_language()

        if request.method == "GET":
            property_data = get_property_record(property_id, organization_id)
            try:
                require_property_media_access(
                    user,
                    property_data,
                    is_guest=False,
                )
            except PropertyMediaError as error:
                return _handle_media_error(error, property_id)
            return render_template(
                "properties/brochure.html",
                property_data=decorate_property_for_display(
                    property_data,
                    language=language,
                ),
            )

        include_agent = request.form.get("include_agent_contact") == "1"
        try:
            result = generate_property_brochure(
                property_id=property_id,
                organization_id=organization_id,
                include_agent_contact=include_agent,
                requesting_user=user,
                is_guest=False,
                language=language,
            )
        except PropertyMediaError as error:
            return _handle_media_error(error, property_id)

        return send_file(
            io.BytesIO(result["pdf_bytes"]),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=result["filename"],
        )

    @app.route(
        "/properties/<int:property_id>/documents/new",
        methods=["GET", "POST"],
    )
    @login_required
    @write_required
    def property_document_new(property_id):
        user = _require_media_user()
        organization_id = require_user_organization()
        language = get_current_language()
        property_data = get_property_record(property_id, organization_id)
        if property_data is None:
            abort(404)

        try:
            list_documents_for_property(
                property_id,
                organization_id,
                user,
                is_guest=False,
            )
        except PropertyMediaError as error:
            return _handle_media_error(error, property_id)

        if request.method == "POST":
            document, error_key = create_property_document_with_files(
                property_id=property_id,
                organization_id=organization_id,
                requesting_user=user,
                document_type=request.form.get("document_type"),
                title=request.form.get("title"),
                description=request.form.get("description"),
                file_storages=_file_storages(),
                is_guest=False,
            )
            if error_key:
                flash_i18n(error_key, "error")
            else:
                flash_i18n("property_doc_saved", "success")
                return redirect(
                    url_for("properties_detail", property_id=property_id)
                )
            if document and document.get("status") == "error":
                flash_i18n(
                    document.get("normalize_error")
                    or "property_doc_err_normalize",
                    "error",
                )

        return render_template(
            "properties/document_new.html",
            property_data=decorate_property_for_display(
                property_data,
                language=language,
            ),
            document_types=document_type_choices(),
            can_write_documents=can_write(user),
        )

    @app.route(
        "/properties/<int:property_id>/documents/<int:document_id>/download"
    )
    @login_required
    def property_document_download(property_id, document_id):
        user = _require_media_user()
        organization_id = require_user_organization()
        try:
            payload = load_downloadable_normalized_pdf(
                document_id=document_id,
                organization_id=organization_id,
                requesting_user=user,
                is_guest=False,
            )
        except PropertyMediaError as error:
            return _handle_media_error(error, property_id)

        as_attachment = request.args.get("download") == "1"
        return send_file(
            io.BytesIO(payload["path"].read_bytes()),
            mimetype=payload["mimetype"],
            as_attachment=as_attachment,
            download_name=payload["download_name"],
        )

    @app.route(
        "/properties/<int:property_id>/documents/<int:document_id>"
        "/files/<int:file_id>/download"
    )
    @login_required
    def property_document_file_download(property_id, document_id, file_id):
        user = _require_media_user()
        organization_id = require_user_organization()
        try:
            payload = load_downloadable_original_file(
                file_id=file_id,
                organization_id=organization_id,
                requesting_user=user,
                is_guest=False,
            )
        except PropertyMediaError as error:
            return _handle_media_error(error, property_id)

        if payload["file"]["document_id"] != document_id:
            abort(404)
        if payload["document"]["property_id"] != property_id:
            abort(404)

        as_attachment = request.args.get("download", "1") == "1"
        return send_file(
            io.BytesIO(payload["path"].read_bytes()),
            mimetype=payload["mimetype"],
            as_attachment=as_attachment,
            download_name=payload["download_name"],
        )

    @app.route(
        "/properties/<int:property_id>/documents/<int:document_id>/archive",
        methods=["POST"],
    )
    @login_required
    @write_required
    def property_document_archive(property_id, document_id):
        user = _require_media_user()
        organization_id = require_user_organization()
        try:
            archive_property_document_safe(
                document_id=document_id,
                organization_id=organization_id,
                requesting_user=user,
                is_guest=False,
            )
        except PropertyMediaError as error:
            return _handle_media_error(error, property_id)

        flash_i18n("property_doc_archived", "success")
        return redirect(url_for("properties_detail", property_id=property_id))
