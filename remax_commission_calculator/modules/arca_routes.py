"""Wizard to link a per-user ARCA connection. Never stores Clave Fiscal."""

from __future__ import annotations

from flask import (
    abort,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from modules.arca.config import get_arca_environment
from modules.arca.connections import (
    ArcaConnectionError,
    WSASS_URL,
    arca_chip_for,
    assert_certificate_matches_key,
    assert_identity_matches_certificate,
    decrypt_secret,
    delete_credentials,
    generate_key_and_csr,
    inspect_certificate,
    public_connection_view,
    store_credentials,
)
from modules.arca.identity import resolve_fiscal_identity
from modules.arca.issuer_config import _now_iso, test_arca_connection
from modules.invoicing import TAX_CONDITIONS
from modules.auth import (
    get_current_user,
    is_admin,
    is_agent,
    is_guest_session,
    login_required,
)
from modules.database.agent_billing_profiles_repository import (
    get_by_agent as get_agent_billing_profile,
    upsert_profile as upsert_agent_billing_profile,
)
from modules.database.arca_connections_repository import (
    ENV_HOMOLOGATION,
    STATUS_CONFIGURING,
    STATUS_CONNECTED,
    STATUS_ERROR,
    STATUS_NOT_CONFIGURED,
    get_arca_connection,
)
from modules.database.billing_issuer_profiles_repository import (
    get_profile as get_billing_issuer_profile,
    upsert_profile as upsert_billing_issuer_profile,
)


def register_arca_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    flash_i18n = helpers["flash_i18n"]

    def _require_billing_user():
        if is_guest_session():
            abort(403)
        user = get_current_user()
        if user is None:
            abort(403)
        if is_admin(user):
            return user
        if is_agent(user) and user.get("agent_id"):
            return user
        abort(403)

    def _environment():
        try:
            return get_arca_environment()
        except Exception:
            return ENV_HOMOLOGATION

    def _context(organization_id, user):
        identity = resolve_fiscal_identity(organization_id, user)
        connection = get_arca_connection(
            organization_id,
            user["id"],
            environment=_environment(),
        )
        return identity, connection, public_connection_view(connection)

    @app.route("/settings/arca")
    @login_required
    def settings_arca():
        user = _require_billing_user()
        organization_id = require_user_organization()
        identity, _record, view = _context(organization_id, user)
        return render_template(
            "settings/arca.html",
            identity=identity,
            arca=view or {"connection_status": STATUS_NOT_CONFIGURED},
            chip=arca_chip_for(organization_id, user),
        )

    @app.route("/settings/arca/connect", methods=["GET", "POST"])
    @login_required
    def settings_arca_connect():
        user = _require_billing_user()
        organization_id = require_user_organization()
        identity, record, view = _context(organization_id, user)
        if request.method == "POST":
            legal_name = (request.form.get("legal_name") or "").strip()
            tax_id = (request.form.get("tax_id") or "").strip()
            tax_condition = (request.form.get("tax_condition") or "").strip()
            fiscal_address = (request.form.get("fiscal_address") or "").strip()
            point_of_sale = (request.form.get("point_of_sale") or "").strip()
            if not point_of_sale.isdigit():
                flash_i18n("billing_missing_arca_point_of_sale", "error")
                return redirect(url_for("settings_arca_connect"))
            if identity.get("editable"):
                if identity["source"] == "agent" and user.get("agent_id"):
                    current = get_agent_billing_profile(
                        organization_id,
                        user["agent_id"],
                    ) or {}
                    upsert_agent_billing_profile(
                        organization_id,
                        user["agent_id"],
                        legal_name=legal_name,
                        tax_id=tax_id,
                        tax_condition=tax_condition,
                        fiscal_address=fiscal_address,
                        email=current.get("email") or "",
                    )
                elif identity["source"] == "office" and identity.get("profile_id"):
                    current = get_billing_issuer_profile(
                        organization_id,
                        identity["profile_id"],
                    ) or {}
                    upsert_billing_issuer_profile(
                        organization_id,
                        issuer_type=current.get("issuer_type") or "organization",
                        display_name=current.get("display_name") or legal_name,
                        legal_name=legal_name,
                        tax_id=tax_id,
                        profile_id=identity["profile_id"],
                        tax_condition=tax_condition,
                        fiscal_address=fiscal_address,
                        email=current.get("email"),
                    )
            store_credentials(
                organization_id,
                user["id"],
                environment=_environment(),
                connection_status=(
                    record.get("connection_status")
                    if record and record.get("connection_status") != STATUS_NOT_CONFIGURED
                    else STATUS_CONFIGURING
                ),
                point_of_sale=point_of_sale,
            )
            return redirect(url_for("settings_arca_authorize"))

        return render_template(
            "settings/arca_connect.html",
            step=1,
            identity=identity,
            arca=view or {"point_of_sale": ""},
            tax_conditions=TAX_CONDITIONS,
        )

    @app.route("/settings/arca/authorize", methods=["GET", "POST"])
    @login_required
    def settings_arca_authorize():
        user = _require_billing_user()
        organization_id = require_user_organization()
        identity, record, view = _context(organization_id, user)
        if record is None:
            flash_i18n("arca_err_complete_fiscal_first", "error")
            return redirect(url_for("settings_arca_connect"))
        if request.method == "POST" or not record.get("private_key_encrypted"):
            key_pem, csr_pem = generate_key_and_csr(
                common_name=identity.get("legal_name") or "JRH One",
                cuit=identity.get("tax_id") or "",
            )
            store_credentials(
                organization_id,
                user["id"],
                environment=_environment(),
                private_key_pem=key_pem,
                csr_pem=csr_pem,
                connection_status=STATUS_CONFIGURING,
            )
            if request.method == "POST":
                return redirect(url_for("settings_arca_authorize"))
            record = get_arca_connection(
                organization_id,
                user["id"],
                environment=_environment(),
            )
            view = public_connection_view(record)
        return render_template(
            "settings/arca_authorize.html",
            step=2,
            identity=identity,
            arca=view,
            wsass_url=WSASS_URL,
        )

    @app.route("/settings/arca/csr")
    @login_required
    def settings_arca_csr():
        user = _require_billing_user()
        organization_id = require_user_organization()
        record = get_arca_connection(
            organization_id,
            user["id"],
            environment=_environment(),
        )
        csr = decrypt_secret((record or {}).get("csr_encrypted"))
        if not csr:
            flash_i18n("arca_err_csr_missing", "error")
            return redirect(url_for("settings_arca_authorize"))
        from io import BytesIO

        buffer = BytesIO(csr.encode("utf-8"))
        return send_file(
            buffer,
            as_attachment=True,
            download_name="jrh-one-arca.csr",
            mimetype="application/pkcs10",
        )

    @app.route("/settings/arca/certificate", methods=["GET", "POST"])
    @login_required
    def settings_arca_certificate():
        user = _require_billing_user()
        organization_id = require_user_organization()
        identity, record, view = _context(organization_id, user)
        if record is None or not record.get("private_key_encrypted"):
            flash_i18n("arca_err_complete_fiscal_first", "error")
            return redirect(url_for("settings_arca_authorize"))
        if request.method == "POST":
            upload = request.files.get("certificate")
            raw = upload.read() if upload else b""
            if not raw:
                flash_i18n("arca_err_certificate_invalid", "error")
                return redirect(url_for("settings_arca_certificate"))
            try:
                inspected = inspect_certificate(raw)
                key_pem = decrypt_secret(record["private_key_encrypted"])
                assert_certificate_matches_key(
                    inspected["certificate"],
                    key_pem.encode("utf-8"),
                )
                assert_identity_matches_certificate(
                    identity.get("tax_id"),
                    inspected.get("cuit"),
                )
            except ArcaConnectionError as error:
                flash_i18n(error.message_key, "error")
                return redirect(url_for("settings_arca_certificate"))
            store_credentials(
                organization_id,
                user["id"],
                environment=_environment(),
                certificate_pem=inspected["pem"],
                connection_status=STATUS_CONFIGURING,
                certificate_subject=inspected["subject"],
                certificate_serial=inspected["serial"],
                certificate_expires_at=inspected["expires_at"],
                clear_error=True,
            )
            return redirect(url_for("settings_arca_verify"))
        return render_template(
            "settings/arca_certificate.html",
            step=3,
            identity=identity,
            arca=view,
        )

    @app.route("/settings/arca/verify", methods=["GET", "POST"])
    @login_required
    def settings_arca_verify():
        user = _require_billing_user()
        organization_id = require_user_organization()
        identity, record, view = _context(organization_id, user)
        if record is None:
            return redirect(url_for("settings_arca_connect"))
        if request.method == "POST":
            status, error_key = test_arca_connection(
                {
                    "tax_id": identity.get("tax_id"),
                    "arca_point_of_sale": record.get("point_of_sale"),
                },
                connection=record,
                organization_id=organization_id,
                user_id=user["id"],
            )
            store_credentials(
                organization_id,
                user["id"],
                environment=_environment(),
                connection_status=(
                    STATUS_CONNECTED if status == "connected" else STATUS_ERROR
                ),
                last_verified_at=_now_iso() if status == "connected" else None,
                last_error=error_key if status != "connected" else "",
                clear_error=status == "connected",
            )
            if status == "connected":
                flash_i18n("billing_arca_test_success", "success")
                return redirect(url_for("settings_arca"))
            flash_i18n(error_key or "billing_arca_test_failed", "error")
            return redirect(url_for("settings_arca_verify"))
        return render_template(
            "settings/arca_verify.html",
            identity=identity,
            arca=view or {},
        )

    @app.route("/settings/arca/disconnect", methods=["POST"])
    @login_required
    def settings_arca_disconnect():
        user = _require_billing_user()
        organization_id = require_user_organization()
        delete_credentials(
            organization_id,
            user["id"],
            environment=_environment(),
        )
        flash_i18n("arca_flash_disconnected", "success")
        return redirect(url_for("settings_integrations"))
