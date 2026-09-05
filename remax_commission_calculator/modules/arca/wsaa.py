"""
WSAA authentication — Ticket de Acceso (TA) with cache.
"""

from __future__ import annotations

import base64
import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7

from modules.arca.config import (
    TA_RENEWAL_MARGIN_SECONDS,
    WSAA_SERVICE_WSFE,
    get_arca_environment,
    get_wsaa_url,
)
from modules.arca.secrets import ArcaCredentials

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TicketAcceso:
    token: str
    sign: str
    expires_at: datetime
    service: str
    cuit: str
    environment: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_tra_xml(service: str) -> bytes:
    now = _utc_now()
    generation = now - timedelta(minutes=1)
    expiration = now + timedelta(minutes=10)
    unique_id = int(time.time())

    tra = f"""<?xml version="1.0" encoding="UTF-8"?>
<loginTicketRequest version="1.0">
<header>
    <uniqueId>{unique_id}</uniqueId>
    <generationTime>{format_datetime(generation, usegmt=True)}</generationTime>
    <expirationTime>{format_datetime(expiration, usegmt=True)}</expirationTime>
</header>
<service>{service}</service>
</loginTicketRequest>"""
    return tra.encode("utf-8")


def sign_tra_cms(tra_xml: bytes, credentials: ArcaCredentials) -> str:
    password = (
        credentials.passphrase.encode("utf-8")
        if credentials.passphrase
        else None
    )
    private_key = serialization.load_pem_private_key(
        credentials.private_key_pem,
        password=password,
    )
    cert = x509.load_pem_x509_certificate(
        credentials.certificate_pem
    )

    builder = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra_xml)
        .add_signer(cert, private_key, hashes.SHA256())
    )
    signed = builder.sign(
        serialization.Encoding.DER,
        [pkcs7.PKCS7Options.Binary],
    )
    return base64.b64encode(signed).decode("ascii")


def parse_login_ticket_response(xml_text: str) -> TicketAcceso:
    root = ET.fromstring(xml_text)
    credentials = root.find("credentials")
    header = root.find("header")
    if credentials is None or header is None:
        raise ValueError("invoice_err_arca_auth_failed")

    token = (credentials.findtext("token") or "").strip()
    sign = (credentials.findtext("sign") or "").strip()
    expiration_text = (
        header.findtext("expirationTime") or ""
    ).strip()
    if not token or not sign or not expiration_text:
        raise ValueError("invoice_err_arca_auth_failed")

    expires_at = datetime.fromisoformat(
        expiration_text.replace("Z", "+00:00")
    )
    service = (header.findtext("service") or WSAA_SERVICE_WSFE).strip()
    cuit = re.sub(r"\D", "", header.findtext("uniqueId") or "")

    return TicketAcceso(
        token=token,
        sign=sign,
        expires_at=expires_at,
        service=service,
        cuit=cuit,
        environment=get_arca_environment(),
    )


def wsaa_login_cms(
    cms_b64: str,
    *,
    transport=None,
) -> str:
    """POST LoginCms and return login ticket response XML."""
    if transport is not None:
        return transport.wsaa_login(cms_b64)

    import urllib.request

    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wsaa="http://wsaa.view.sua.dvadac.dgi.gov">
  <soapenv:Header/>
  <soapenv:Body>
    <wsaa:loginCms>
      <wsaa:in0>{cms_b64}</wsaa:in0>
    </wsaa:loginCms>
  </soapenv:Body>
</soapenv:Envelope>"""

    url = get_wsaa_url()
    request = urllib.request.Request(
        url,
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read().decode("utf-8", errors="replace")

    if "<loginCmsReturn>" not in body:
        logger.error("arca_auth_failed: empty wsaa response")
        raise ValueError("invoice_err_arca_auth_failed")

    start = body.index("<loginCmsReturn>") + len(
        "<loginCmsReturn>"
    )
    end = body.index("</loginCmsReturn>")
    inner = body[start:end].strip()
    if inner.startswith("<![CDATA["):
        inner = inner[9:-3]
    return inner


def authenticate_wsaa(
    credentials: ArcaCredentials,
    *,
    cuit: str,
    service: str = WSAA_SERVICE_WSFE,
    transport=None,
    cache_getter=None,
    cache_setter=None,
    cache_key=None,
) -> TicketAcceso:
    """Obtain TA, using cache when valid."""
    environment = get_arca_environment()
    cache_key = cache_key or f"{cuit}:{service}:{environment}"

    if cache_getter:
        cached = cache_getter(cache_key)
        if cached and cached.expires_at > (
            _utc_now()
            + timedelta(seconds=TA_RENEWAL_MARGIN_SECONDS)
        ):
            logger.info("arca_auth_success: cache_hit")
            return cached

    logger.info("arca_auth_start")
    tra = build_tra_xml(service)
    cms = sign_tra_cms(tra, credentials)
    ticket_xml = wsaa_login_cms(cms, transport=transport)
    ticket = parse_login_ticket_response(ticket_xml)
    ticket = TicketAcceso(
        token=ticket.token,
        sign=ticket.sign,
        expires_at=ticket.expires_at,
        service=service,
        cuit=re.sub(r"\D", "", cuit),
        environment=environment,
    )

    if cache_setter:
        cache_setter(cache_key, ticket)

    logger.info("arca_auth_success")
    return ticket
