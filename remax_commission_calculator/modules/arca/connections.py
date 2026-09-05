"""Resolve per-user ARCA credentials. Never uses a shared org fallback."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from datetime import datetime, timezone

from cryptography import x509
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from modules.arca.config import (
    ARCA_ENV_HOMOLOGATION,
    get_arca_environment,
)
from modules.arca.secrets import ArcaCredentials
from modules.config import get_secret_key
from modules.database.arca_connections_repository import (
    ENV_HOMOLOGATION,
    STATUS_CONFIGURING,
    STATUS_CONNECTED,
    STATUS_ERROR,
    STATUS_NOT_CONFIGURED,
    delete_arca_connection,
    get_arca_connection,
    upsert_arca_connection,
)

logger = logging.getLogger(__name__)

WSASS_URL = "https://www.afip.gob.ar/ws/"


class ArcaConnectionError(ValueError):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def _fernet():
    digest = hashlib.sha256(get_secret_key().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value):
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt_secret(value):
    if not value:
        return None
    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def store_credentials(
    organization_id,
    user_id,
    *,
    certificate_pem=None,
    private_key_pem=None,
    csr_pem=None,
    environment=ENV_HOMOLOGATION,
    **fields,
):
    payload = dict(fields)
    if certificate_pem is not None:
        payload["certificate_encrypted"] = encrypt_secret(certificate_pem)
    if private_key_pem is not None:
        payload["private_key_encrypted"] = encrypt_secret(private_key_pem)
    if csr_pem is not None:
        payload["csr_encrypted"] = encrypt_secret(csr_pem)
    return upsert_arca_connection(
        organization_id,
        user_id,
        environment=environment,
        **payload,
    )


def load_credentials(connection) -> ArcaCredentials:
    """Load PEM from a connection row. Never reads global env."""
    if not connection:
        raise ArcaConnectionError("invoice_err_arca_not_linked")
    cert = decrypt_secret(connection.get("certificate_encrypted"))
    key = decrypt_secret(connection.get("private_key_encrypted"))
    if not cert or not key:
        raise ArcaConnectionError("invoice_err_arca_credentials_missing")
    return ArcaCredentials(
        certificate_pem=cert.encode("utf-8"),
        private_key_pem=key.encode("utf-8"),
    )


def delete_credentials(
    organization_id,
    user_id,
    *,
    environment=ENV_HOMOLOGATION,
):
    delete_arca_connection(
        organization_id,
        user_id,
        environment=environment,
    )


def ta_cache_key(organization_id, user_id, *, environment=None, service="wsfe"):
    env = environment or get_arca_environment()
    return f"{int(organization_id)}:{int(user_id)}:{env}:{service}"


def generate_key_and_csr(*, common_name, cuit=""):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attributes = [
        x509.NameAttribute(NameOID.COMMON_NAME, (common_name or "JRH One")[:64]),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JRH One"),
    ]
    digits = re.sub(r"\D", "", str(cuit or ""))
    if len(digits) == 11:
        attributes.append(
            x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {digits}")
        )
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name(attributes))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM)
    return key_pem, csr_pem


def _load_certificate(raw: bytes):
    text = raw.strip()
    try:
        return x509.load_pem_x509_certificate(text)
    except ValueError:
        try:
            return x509.load_der_x509_certificate(text)
        except ValueError as error:
            raise ArcaConnectionError("arca_err_certificate_invalid") from error


def inspect_certificate(raw: bytes) -> dict:
    cert = _load_certificate(raw)
    now = datetime.now(timezone.utc)
    expires = getattr(cert, "not_valid_after_utc", None)
    if expires is None:
        expires = cert.not_valid_after
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        raise ArcaConnectionError("arca_err_certificate_expired")
    subject = cert.subject.rfc4514_string()
    serial = format(cert.serial_number, "x")
    cuit = cuit_from_certificate(cert)
    return {
        "certificate": cert,
        "subject": subject,
        "serial": serial,
        "expires_at": expires.replace(microsecond=0).isoformat(),
        "cuit": cuit,
        "pem": cert.public_bytes(serialization.Encoding.PEM),
    }


def cuit_from_certificate(cert) -> str:
    for attribute in cert.subject:
        digits = re.sub(r"\D", "", attribute.value or "")
        if len(digits) == 11:
            return digits
    return ""


def assert_certificate_matches_key(cert, private_key_pem: bytes):
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    cert_numbers = cert.public_key().public_numbers()
    key_numbers = key.public_key().public_numbers()
    if (cert_numbers.n, cert_numbers.e) != (key_numbers.n, key_numbers.e):
        raise ArcaConnectionError("arca_err_certificate_key_mismatch")


def assert_identity_matches_certificate(identity_cuit, cert_cuit):
    identity = re.sub(r"\D", "", str(identity_cuit or ""))
    cert = re.sub(r"\D", "", str(cert_cuit or ""))
    if cert and identity and cert != identity:
        raise ArcaConnectionError("arca_err_cuit_mismatch")


def public_connection_view(connection):
    if not connection:
        return None
    return {
        "id": connection.get("id"),
        "environment": connection.get("environment"),
        "connection_status": connection.get("connection_status"),
        "point_of_sale": connection.get("point_of_sale") or "",
        "certificate_subject": connection.get("certificate_subject") or "",
        "certificate_serial": connection.get("certificate_serial") or "",
        "certificate_expires_at": connection.get("certificate_expires_at") or "",
        "last_verified_at": connection.get("last_verified_at") or "",
        "last_error": connection.get("last_error") or "",
        "has_certificate": bool(connection.get("certificate_encrypted")),
        "has_private_key": bool(connection.get("private_key_encrypted")),
        "has_csr": bool(connection.get("csr_encrypted")),
    }


def arca_chip_for(organization_id, user, *, environment=None):
    env = environment or ARCA_ENV_HOMOLOGATION
    try:
        env = get_arca_environment()
    except Exception:
        env = ARCA_ENV_HOMOLOGATION
    user_id = (user or {}).get("id")
    record = (
        get_arca_connection(organization_id, user_id, environment=env)
        if user_id
        else None
    )
    view = public_connection_view(record) or {
        "connection_status": STATUS_NOT_CONFIGURED,
        "environment": env,
        "point_of_sale": "",
    }
    status = view.get("connection_status") or STATUS_NOT_CONFIGURED
    return {
        **view,
        "state": status,
        "connected": status == STATUS_CONNECTED,
        "can_connect": True,
        "can_disconnect": status in (STATUS_CONNECTED, STATUS_CONFIGURING, STATUS_ERROR),
        "wsass_url": WSASS_URL,
    }


def require_user_connection(organization_id, user_id, *, environment=None):
    env = environment or get_arca_environment()
    record = get_arca_connection(
        organization_id,
        user_id,
        environment=env,
    )
    if (
        record is None
        or record.get("connection_status") != STATUS_CONNECTED
        or not record.get("certificate_encrypted")
        or not record.get("private_key_encrypted")
    ):
        raise ArcaConnectionError("invoice_err_arca_not_linked")
    return record
