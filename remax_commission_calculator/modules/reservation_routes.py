"""Reservation screens. Agents read their own rows. Staff edits."""

from __future__ import annotations

from flask import abort, redirect, render_template, request, send_file, url_for

from modules.auth import admin_required, get_current_user, is_admin, is_agent, is_guest_session, login_required
from modules.database.agents_repository import get_agents
from modules.reservation_board import (
    board_filter_choices,
    build_reservation_board,
    present_reservation,
    property_cover_url,
)
from modules.reservations import (
    DOCUMENT_TYPES,
    OPEN_STATUSES,
    ReservationError,
    add_note,
    cancel_reservation,
    document_path,
    load_reservation_detail,
    save_document,
    update_reservation,
)


def register_reservation_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    flash_i18n = helpers["flash_i18n"]

    def _viewer():
        if is_guest_session():
            abort(403)
        user = get_current_user()
        if user is None:
            abort(403)
        organization_id = require_user_organization()
        if is_admin(user):
            return user, organization_id, None
        if is_agent(user) and user.get("agent_id"):
            return user, organization_id, user["agent_id"]
        abort(403)

    def _present_detail(organization_id, detail):
        from modules.property_sync.media import list_covers_for_properties

        property_row = detail.get("property") or {}
        reservation = dict(detail["reservation"])
        reservation["neighborhood"] = property_row.get("neighborhood")
        reservation["property_external_id"] = property_row.get("external_id")
        reservation["listing_purpose"] = property_row.get("listing_purpose")
        property_id = reservation.get("property_id")
        covers = list_covers_for_properties(organization_id, [property_id] if property_id else [])
        try:
            cover = covers.get(int(property_id)) if property_id else None
        except (TypeError, ValueError):
            cover = None
        return present_reservation(
            reservation,
            cover_url=property_cover_url(cover, property_id),
            operation=detail.get("operation"),
        )

    @app.route("/reservations", methods=["GET"])
    @login_required
    def reservations_list():
        user, organization_id, agent_id = _viewer()
        filters = {"status": request.args.get("status") or ""}
        if agent_id is None:
            raw_agent = request.args.get("agent_id") or ""
            try:
                filters["agent_id"] = int(raw_agent) if raw_agent else ""
            except ValueError:
                filters["agent_id"] = ""
        for key in (
            "view",
            "q",
            "address",
            "client",
            "purpose",
            "payment_method",
            "credit",
            "next_milestone",
            "close_from",
            "close_to",
            "reserved_from",
            "reserved_to",
            "has_operation",
            "agent_query",
        ):
            filters[key] = request.args.get(key) or ""
        board = build_reservation_board(
            organization_id,
            agent_id=agent_id,
            filters=filters,
        )
        choices = board_filter_choices()
        agents = []
        if agent_id is None:
            agents = [
                {"id": agent["id"], "name": agent["name"]}
                for agent in get_agents(organization_id)
                if agent.get("name")
            ]
        return render_template(
            "reservations/list.html",
            board=board,
            reservations=board["rows"],
            filters=board["filters"],
            statuses=choices["statuses"],
            payments=choices["payments"],
            milestones=choices["milestones"],
            agents=agents,
            read_only=agent_id is not None,
            viewer=user,
        )

    @app.route("/reservations/<int:reservation_id>", methods=["GET"])
    @login_required
    def reservations_detail(reservation_id):
        user, organization_id, agent_id = _viewer()
        detail = load_reservation_detail(
            organization_id,
            reservation_id,
            agent_id=agent_id,
        )
        if detail is None:
            abort(404)
        detail["reservation"] = _present_detail(organization_id, detail)
        template = (
            "reservations/_detail_panel.html"
            if request.args.get("panel") == "1"
            else "reservations/detail.html"
        )
        return render_template(
            template,
            detail=detail,
            statuses=OPEN_STATUSES,
            document_types=DOCUMENT_TYPES,
            read_only=agent_id is not None,
            viewer=user,
            panel=request.args.get("panel") == "1",
        )

    @app.route("/reservations/<int:reservation_id>", methods=["POST"])
    @admin_required
    def reservations_update(reservation_id):
        _user, organization_id, _agent_id = _viewer()
        try:
            update_reservation(
                organization_id,
                reservation_id,
                {
                    "reservation_status": request.form.get("reservation_status"),
                    "payment_method": request.form.get("payment_method"),
                    "next_milestone": request.form.get("next_milestone"),
                    "estimated_closing_date": request.form.get("estimated_closing_date"),
                    "notes": request.form.get("notes"),
                },
                actor_user_id=_user["id"],
            )
        except ReservationError as error:
            flash_i18n(error.message_key, "error")
            return redirect(url_for("reservations_detail", reservation_id=reservation_id))
        flash_i18n("reservation_saved", "success")
        return redirect(url_for("reservations_detail", reservation_id=reservation_id))

    @app.route("/reservations/<int:reservation_id>/notes", methods=["POST"])
    @admin_required
    def reservations_note(reservation_id):
        user, organization_id, _agent_id = _viewer()
        try:
            add_note(
                organization_id,
                reservation_id,
                request.form.get("body"),
                actor_user_id=user["id"],
            )
        except ReservationError as error:
            flash_i18n(error.message_key, "error")
        else:
            flash_i18n("reservation_note_saved", "success")
        return redirect(url_for("reservations_detail", reservation_id=reservation_id))

    @app.route("/reservations/<int:reservation_id>/cancel", methods=["POST"])
    @admin_required
    def reservations_cancel(reservation_id):
        user, organization_id, _agent_id = _viewer()
        try:
            cancel_reservation(
                organization_id,
                reservation_id,
                actor_user_id=user["id"],
                keep_negotiated_price=request.form.get("keep_negotiated_price") == "1",
            )
        except ReservationError as error:
            flash_i18n(error.message_key, "error")
        else:
            flash_i18n("reservation_cancelled", "success")
        return redirect(url_for("reservations_detail", reservation_id=reservation_id))

    @app.route("/reservations/<int:reservation_id>/documents", methods=["POST"])
    @admin_required
    def reservations_document_upload(reservation_id):
        user, organization_id, _agent_id = _viewer()
        upload = request.files.get("document")
        try:
            if upload is None:
                raise ReservationError("reservation_err_document")
            save_document(
                organization_id,
                reservation_id,
                upload,
                request.form.get("doc_type") or "other",
                actor_user_id=user["id"],
            )
        except ReservationError as error:
            flash_i18n(error.message_key, "error")
        else:
            flash_i18n("reservation_document_saved", "success")
        return redirect(url_for("reservations_detail", reservation_id=reservation_id))

    @app.route(
        "/reservations/<int:reservation_id>/documents/<int:document_id>",
        methods=["GET"],
    )
    @login_required
    def reservations_document_download(reservation_id, document_id):
        _user, organization_id, agent_id = _viewer()
        detail = load_reservation_detail(
            organization_id,
            reservation_id,
            agent_id=agent_id,
        )
        if detail is None:
            abort(404)
        document, path = document_path(organization_id, document_id)
        if (
            document is None
            or path is None
            or not path.is_file()
            or document["reservation_id"] != reservation_id
        ):
            abort(404)
        return send_file(
            path,
            as_attachment=True,
            download_name=document["original_filename"],
            mimetype=document.get("content_type") or "application/octet-stream",
        )
