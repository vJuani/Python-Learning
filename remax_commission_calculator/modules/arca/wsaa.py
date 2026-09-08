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
from urllib.error import HTTPError, URLError

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

# WSAA schema expects xs:dateTime. AFIP examples use GMT-3.
ARCA_TRA_TZ = timezone(timedelta(hours=-3))
TRA_GENERATION_SKEW = timedelta(minutes=1)
TRA_EXPIRATION_WINDOW = timedelta(minutes=10)
_FAULT_TEXT_MAX = 240
_BASE64ISH = re.compile(r"(?:[A-Za-z0-9+/]{40,}={0,2})")
_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----",
    re.DOTALL,
)
_RFC2822_WEEKDAY = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),",
)

USER_AUTH_ERROR_KEY = "invoice_err_arca_auth_failed"
USER_TA_PENDING_KEY = "invoice_err_arca_ta_pending"
USER_TA_PERSIST_KEY = "invoice_err_arca_ta_persist_failed"
DEBUG_WSAA_SCHEMA = "arca_err_wsaa_schema"
DEBUG_WSAA_CMS = "arca_err_wsaa_cms"
DEBUG_WSAA_UNAUTHORIZED = "arca_err_wsaa_unauthorized"
DEBUG_WSAA_CERTIFICATE = "arca_err_wsaa_certificate"
DEBUG_WSAA_CLOCK = "arca_err_wsaa_clock"
DEBUG_WSAA_TA_EXISTS = "arca_err_wsaa_ta_exists"
DEBUG_WSAA_UNAVAILABLE = "arca_err_wsaa_unavailable"
DEBUG_TA_PERSIST_FAILED = "arca_ta_persist_failed"


@dataclass(frozen=True)
class TicketAcceso:
    token: str
    sign: str
    expires_at: datetime
    service: str
    cuit: str
    environment: str
    generation_time: datetime | None = None


class WsaaAuthError(ValueError):
    """WSAA login failed. Exception message stays user-facing; details are logs-only."""

    def __init__(
        self,
        *,
        debug_key=USER_AUTH_ERROR_KEY,
        user_key=None,
        http_status=None,
        faultcode="",
        faultstring="",
    ):
        mapped_user_key = user_key or (
            USER_TA_PENDING_KEY
            if debug_key == DEBUG_WSAA_TA_EXISTS
            else USER_AUTH_ERROR_KEY
        )
        super().__init__(mapped_user_key)
        self.user_key = mapped_user_key
        self.debug_key = debug_key or USER_AUTH_ERROR_KEY
        self.http_status = http_status
        self.faultcode = faultcode or ""
        self.faultstring = faultstring or ""


class ArcaTaPersistError(RuntimeError):
    """loginCms succeeded but the TA could not be stored."""

    def __init__(
        self,
        *,
        environment="",
        organization_id=None,
        user_id=None,
        service="",
        db_engine="",
        exception_type="",
    ):
        super().__init__(USER_TA_PERSIST_KEY)
        self.user_key = USER_TA_PERSIST_KEY
        self.debug_key = DEBUG_TA_PERSIST_FAILED
        self.environment = environment
        self.organization_id = organization_id
        self.user_id = user_id
        self.service = service
        self.db_engine = db_engine
        self.exception_type = exception_type


def parse_ta_cache_key(cache_key: str | None) -> dict:
    """Split `{org}:{user}:{environment}:{service}` when that shape is used."""
    parts = str(cache_key or "").split(":")
    if (
        len(parts) == 4
        and parts[0].isdigit()
        and parts[1].isdigit()
    ):
        return {
            "organization_id": parts[0],
            "user_id": parts[1],
            "environment": parts[2],
            "service": parts[3],
        }
    return {
        "organization_id": None,
        "user_id": None,
        "environment": parts[-1] if parts and parts[-1] else None,
        "service": parts[-2] if len(parts) >= 2 else None,
    }


def _log_ta_persist_failed(
    *,
    environment,
    organization_id,
    user_id,
    service,
    db_engine,
    exception_type,
):
    logger.error(
        "arca_ta_persist_failed environment=%s organization_id=%s "
        "user_id=%s service=%s db_engine=%s exception_type=%s",
        environment,
        organization_id,
        user_id,
        service,
        db_engine,
        exception_type,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def ta_is_reusable(
    ticket: TicketAcceso | None,
    *,
    now: datetime | None = None,
    margin_seconds: int = TA_RENEWAL_MARGIN_SECONDS,
    require_margin: bool = True,
) -> bool:
    if ticket is None or not ticket.token or not ticket.sign:
        return False
    current = _aware(now or _utc_now())
    expires = _aware(ticket.expires_at)
    if require_margin:
        return expires > current + timedelta(seconds=margin_seconds)
    return expires > current


def _tra_now() -> datetime:
    return datetime.now(ARCA_TRA_TZ)


def format_tra_datetime(value: datetime) -> str:
    """Format a WSAA TRA timestamp as xs:dateTime with an explicit offset."""
    if value.tzinfo is None:
        aware = value.replace(tzinfo=ARCA_TRA_TZ)
    else:
        aware = value.astimezone(ARCA_TRA_TZ)
    formatted = aware.replace(microsecond=0).isoformat()
    if _RFC2822_WEEKDAY.search(formatted) or "GMT" in formatted:
        raise ValueError("TRA datetime must be ISO 8601, not RFC 2822")
    return formatted


def build_tra_xml(service: str) -> bytes:
    now = _tra_now()
    generation = now - TRA_GENERATION_SKEW
    expiration = now + TRA_EXPIRATION_WINDOW
    unique_id = int(time.time())

    tra = f"""<?xml version="1.0" encoding="UTF-8"?>
<loginTicketRequest version="1.0">
<header>
    <uniqueId>{unique_id}</uniqueId>
    <generationTime>{format_tra_datetime(generation)}</generationTime>
    <expirationTime>{format_tra_datetime(expiration)}</expirationTime>
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
        raise WsaaAuthError()

    token = (credentials.findtext("token") or "").strip()
    sign = (credentials.findtext("sign") or "").strip()
    expiration_text = (
        header.findtext("expirationTime") or ""
    ).strip()
    if not token or not sign or not expiration_text:
        raise WsaaAuthError()

    expires_at = datetime.fromisoformat(
        expiration_text.replace("Z", "+00:00")
    )
    generation_text = (header.findtext("generationTime") or "").strip()
    generation_time = None
    if generation_text:
        try:
            generation_time = datetime.fromisoformat(
                generation_text.replace("Z", "+00:00")
            )
        except ValueError:
            generation_time = None
    service = (header.findtext("service") or WSAA_SERVICE_WSFE).strip()
    cuit = re.sub(r"\D", "", header.findtext("uniqueId") or "")

    return TicketAcceso(
        token=token,
        sign=sign,
        expires_at=expires_at,
        service=service,
        cuit=cuit,
        environment=get_arca_environment(),
        generation_time=generation_time,
    )


def sanitize_wsaa_fault_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = _PEM_BLOCK.sub("[redacted]", text)
    text = _BASE64ISH.sub("[redacted]", text)
    return text[:_FAULT_TEXT_MAX]


def _local_xml_text(root: ET.Element, local_name: str) -> str:
    wanted = local_name.lower()
    for element in root.iter():
        tag = element.tag.split("}")[-1].lower()
        if tag == wanted and (element.text or "").strip():
            return (element.text or "").strip()
    return ""


def parse_wsaa_soap_fault(body: str) -> tuple[str, str]:
    """Return sanitized (faultcode, faultstring) from a SOAP envelope."""
    raw = str(body or "").strip()
    if not raw:
        return "", ""
    faultcode = ""
    faultstring = ""
    try:
        root = ET.fromstring(raw)
        faultcode = _local_xml_text(root, "faultcode")
        faultstring = _local_xml_text(root, "faultstring")
    except ET.ParseError:
        code_match = re.search(
            r"<(?:[\w.-]+:)?faultcode[^>]*>(.*?)</(?:[\w.-]+:)?faultcode>",
            raw,
            re.DOTALL | re.IGNORECASE,
        )
        string_match = re.search(
            r"<(?:[\w.-]+:)?faultstring[^>]*>(.*?)</(?:[\w.-]+:)?faultstring>",
            raw,
            re.DOTALL | re.IGNORECASE,
        )
        faultcode = re.sub(r"<[^>]+>", " ", code_match.group(1)).strip() if code_match else ""
        faultstring = (
            re.sub(r"<[^>]+>", " ", string_match.group(1)).strip()
            if string_match
            else ""
        )
    return (
        sanitize_wsaa_fault_text(faultcode),
        sanitize_wsaa_fault_text(faultstring),
    )


def map_wsaa_fault(faultcode: str, faultstring: str) -> str:
    blob = f"{faultcode} {faultstring}".lower()
    if not blob.strip():
        return USER_AUTH_ERROR_KEY
    if (
        "alreadyauthenticated" in blob
        or "ya posee un ta" in blob
        or "ta valido" in blob
        or "ta válido" in blob
    ):
        return DEBUG_WSAA_TA_EXISTS
    if "schema" in blob or "interpretar el xml" in blob:
        return DEBUG_WSAA_SCHEMA
    if "cms.sign" in blob or "firma inválida" in blob or "firma invalida" in blob:
        return DEBUG_WSAA_CMS
    if "cms.bad" in blob or "algoritmo no soportado" in blob:
        return DEBUG_WSAA_CMS
    if "no autorizado" in blob or "autorizaci" in blob:
        return DEBUG_WSAA_UNAUTHORIZED
    if "certificado no emitido" in blob or "ac de confianza" in blob:
        return DEBUG_WSAA_CERTIFICATE
    if "generationtime" in blob or "expirationtime" in blob or "clock" in blob:
        return DEBUG_WSAA_CLOCK
    if "unavailable" in blob or "timeout" in blob:
        return DEBUG_WSAA_UNAVAILABLE
    return USER_AUTH_ERROR_KEY


def _log_arca_auth_failed(*, http_status=None, faultcode="", faultstring="", debug_key=""):
    logger.error(
        "arca_auth_failed environment=%s http_status=%s faultcode=%s faultstring=%s debug_key=%s",
        get_arca_environment(),
        http_status if http_status is not None else "",
        sanitize_wsaa_fault_text(faultcode),
        sanitize_wsaa_fault_text(faultstring),
        debug_key or USER_AUTH_ERROR_KEY,
    )


def _raise_wsaa_auth_failed(*, http_status=None, body="", debug_key=None):
    faultcode, faultstring = parse_wsaa_soap_fault(body)
    mapped = debug_key or map_wsaa_fault(faultcode, faultstring)
    _log_arca_auth_failed(
        http_status=http_status,
        faultcode=faultcode,
        faultstring=faultstring,
        debug_key=mapped,
    )
    raise WsaaAuthError(
        debug_key=mapped,
        http_status=http_status,
        faultcode=faultcode,
        faultstring=faultstring,
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
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            http_status = getattr(response, "status", None) or response.getcode()
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        fault_body = ""
        try:
            fault_body = (error.read() or b"").decode("utf-8", errors="replace")
        except Exception:
            fault_body = ""
        _raise_wsaa_auth_failed(http_status=error.code, body=fault_body)
    except URLError as error:
        _log_arca_auth_failed(
            http_status="unavailable",
            debug_key=DEBUG_WSAA_UNAVAILABLE,
        )
        raise WsaaAuthError(
            debug_key=DEBUG_WSAA_UNAVAILABLE,
            http_status="unavailable",
        ) from error

    if "<loginCmsReturn>" not in body:
        _raise_wsaa_auth_failed(
            http_status=http_status,
            body=body,
            debug_key=DEBUG_WSAA_UNAVAILABLE if not body.strip() else None,
        )

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
    cached = cache_getter(cache_key) if cache_getter else None
    if ta_is_reusable(cached):
        logger.info("arca_auth_success cache_hit")
        return cached

    logger.info("arca_auth_start")
    try:
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
            generation_time=ticket.generation_time,
        )
    except WsaaAuthError as error:
        if error.debug_key == DEBUG_WSAA_TA_EXISTS:
            logger.info("arca_auth_ta_exists")
            if ta_is_reusable(cached, require_margin=False):
                logger.info("arca_auth_success cache_reuse_after_ta_exists")
                return cached
            raise WsaaAuthError(
                debug_key=DEBUG_WSAA_TA_EXISTS,
                user_key=USER_TA_PENDING_KEY,
                http_status=error.http_status,
                faultcode=error.faultcode,
                faultstring=error.faultstring,
            ) from None
        raise

    if cache_setter:
        try:
            cache_setter(cache_key, ticket)
        except ArcaTaPersistError:
            raise
        except Exception as error:
            from modules.config import get_database_backend

            parsed = parse_ta_cache_key(cache_key)
            environment_name = (
                getattr(ticket, "environment", None) or environment
            )
            service_name = getattr(ticket, "service", None) or service
            db_engine = get_database_backend()
            exception_type = type(error).__name__
            _log_ta_persist_failed(
                environment=environment_name,
                organization_id=parsed["organization_id"],
                user_id=parsed["user_id"],
                service=service_name,
                db_engine=db_engine,
                exception_type=exception_type,
            )
            raise ArcaTaPersistError(
                environment=environment_name,
                organization_id=parsed["organization_id"],
                user_id=parsed["user_id"],
                service=service_name,
                db_engine=db_engine,
                exception_type=exception_type,
            ) from error

    logger.info("arca_auth_success")
    return ticket
