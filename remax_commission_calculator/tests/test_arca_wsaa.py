"""WSAA TRA timestamps and sanitized SOAP fault handling."""

from __future__ import annotations

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_arca_wsaa.db")
os.environ["INVOICE_PROVIDER"] = "arca"
os.environ["ARCA_ENV"] = "homologation"
os.environ["SECRET_KEY"] = "test-arca-wsaa-secret"

from modules.arca.secrets import ArcaCredentials  # noqa: E402
from modules.arca.wsaa import (  # noqa: E402
    ARCA_TRA_TZ,
    DEBUG_WSAA_SCHEMA,
    DEBUG_WSAA_TA_EXISTS,
    DEBUG_WSAA_UNAVAILABLE,
    USER_AUTH_ERROR_KEY,
    USER_TA_PENDING_KEY,
    TicketAcceso,
    WsaaAuthError,
    authenticate_wsaa,
    build_tra_xml,
    format_tra_datetime,
    map_wsaa_fault,
    parse_wsaa_soap_fault,
    sanitize_wsaa_fault_text,
    wsaa_login_cms,
)

_ISO8601 = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$"
_RFC2822_WEEKDAY = r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),"

_SOAP_FAULT = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <soapenv:Fault>
      <faultcode>soapenv:Server.userException</faultcode>
      <faultstring>ns1:cms.schema.invalid: No se ha podido interpretar el XML contra el SCHEMA</faultstring>
    </soapenv:Fault>
  </soapenv:Body>
</soapenv:Envelope>"""


class ArcaWsaaTests(unittest.TestCase):
    def test_tra_times_are_iso8601_with_timezone(self):
        xml_bytes = build_tra_xml("wsfe")
        root = ET.fromstring(xml_bytes)
        generation = root.find("./header/generationTime").text
        expiration = root.find("./header/expirationTime").text
        service = root.findtext("service")

        self.assertRegex(generation, _ISO8601)
        self.assertRegex(expiration, _ISO8601)
        self.assertEqual(service, "wsfe")

        generation_dt = datetime.fromisoformat(generation)
        expiration_dt = datetime.fromisoformat(expiration)
        self.assertIsNotNone(generation_dt.tzinfo)
        self.assertIsNotNone(expiration_dt.tzinfo)
        self.assertGreater(expiration_dt, generation_dt)

        now = datetime.now(ARCA_TRA_TZ)
        self.assertLess(generation_dt, now)
        self.assertGreater(expiration_dt, now)

    def test_tra_xml_is_well_formed(self):
        xml_bytes = build_tra_xml("wsfe")
        root = ET.fromstring(xml_bytes)
        self.assertEqual(root.tag, "loginTicketRequest")
        self.assertIsNotNone(root.find("./header/uniqueId").text)

    def test_tra_does_not_use_rfc2822(self):
        xml_text = build_tra_xml("wsfe").decode("utf-8")
        self.assertNotIn("GMT", xml_text)
        self.assertNotRegex(xml_text, _RFC2822_WEEKDAY)
        self.assertNotIn(" UTC", xml_text)

    def test_format_tra_datetime_rejects_rfc2822_shape(self):
        formatted = format_tra_datetime(datetime.now(ARCA_TRA_TZ))
        self.assertRegex(formatted, _ISO8601)
        self.assertTrue(formatted.endswith("-03:00"))

    def test_soap_fault_is_parsed_and_mapped(self):
        faultcode, faultstring = parse_wsaa_soap_fault(_SOAP_FAULT)
        self.assertEqual(faultcode, "soapenv:Server.userException")
        self.assertIn("cms.schema.invalid", faultstring)
        self.assertEqual(
            map_wsaa_fault(faultcode, faultstring),
            DEBUG_WSAA_SCHEMA,
        )

    def test_sanitize_redacts_secrets_and_keeps_fault(self):
        messy = (
            "cms.schema.invalid "
            + ("A" * 80)
            + " -----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----"
        )
        cleaned = sanitize_wsaa_fault_text(messy)
        self.assertIn("cms.schema.invalid", cleaned)
        self.assertNotIn("A" * 40, cleaned)
        self.assertNotIn("BEGIN CERTIFICATE", cleaned)
        self.assertIn("[redacted]", cleaned)

    def test_http_500_raises_user_facing_error_and_logs_sanitized_fault(self):
        error = HTTPError(
            url="https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=BytesIO(_SOAP_FAULT.encode("utf-8")),
        )
        cms = "Q" * 80
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertLogs("modules.arca.wsaa", level="ERROR") as captured:
                with self.assertRaises(WsaaAuthError) as raised:
                    wsaa_login_cms(cms)

        self.assertEqual(str(raised.exception), USER_AUTH_ERROR_KEY)
        self.assertEqual(raised.exception.user_key, USER_AUTH_ERROR_KEY)
        self.assertEqual(raised.exception.debug_key, DEBUG_WSAA_SCHEMA)
        self.assertEqual(raised.exception.http_status, 500)
        log_text = "\n".join(captured.output)
        self.assertIn("arca_auth_failed", log_text)
        self.assertIn("environment=homologation", log_text)
        self.assertIn("http_status=500", log_text)
        self.assertIn("cms.schema.invalid", log_text)
        self.assertIn(DEBUG_WSAA_SCHEMA, log_text)
        self.assertNotIn(cms, log_text)
        self.assertNotIn("loginTicketRequest", log_text)
        self.assertNotIn("<token>", log_text)

    def test_url_error_maps_to_unavailable_without_leaking_request(self):
        cms = "SECRETCMSPAYLOAD"
        with patch(
            "urllib.request.urlopen",
            side_effect=URLError("timed out"),
        ):
            with self.assertLogs("modules.arca.wsaa", level="ERROR") as captured:
                with self.assertRaises(WsaaAuthError) as raised:
                    wsaa_login_cms(cms)
        self.assertEqual(raised.exception.debug_key, DEBUG_WSAA_UNAVAILABLE)
        self.assertEqual(str(raised.exception), USER_AUTH_ERROR_KEY)
        self.assertNotIn(cms, "\n".join(captured.output))

    def test_already_authenticated_maps_to_ta_exists(self):
        self.assertEqual(
            map_wsaa_fault(
                "ns1:coe.alreadyAuthenticated",
                "El CEE ya posee un TA valido para el acceso al WSN solicitado",
            ),
            DEBUG_WSAA_TA_EXISTS,
        )

    def _fresh_ticket(self, minutes=60, token="MOCK-TOKEN"):
        return TicketAcceso(
            token=token,
            sign="MOCK-SIGN",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
            service="wsfe",
            cuit="20300000003",
            environment="homologation",
        )

    def _auth_transport(self, login_impl):
        class _Transport:
            def __init__(self):
                self.logins = 0

            def wsaa_login(self, cms_b64):
                self.logins += 1
                return login_impl()

        return _Transport()

    def _login_xml(self, token="NEW-TOKEN"):
        exp = (
            datetime.now(timezone.utc) + timedelta(hours=12)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<loginTicketResponse>
  <header>
    <generationTime>{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}</generationTime>
    <expirationTime>{exp}</expirationTime>
    <service>wsfe</service>
  </header>
  <credentials>
    <token>{token}</token>
    <sign>NEW-SIGN</sign>
  </credentials>
</loginTicketResponse>"""

    def test_first_auth_stores_ta_and_second_reuses_it(self):
        cache = {}
        transport = self._auth_transport(lambda: self._login_xml())
        creds = ArcaCredentials(certificate_pem=b"x", private_key_pem=b"y")
        with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
            first = authenticate_wsaa(
                creds,
                cuit="20300000003",
                transport=transport,
                cache_getter=cache.get,
                cache_setter=cache.__setitem__,
                cache_key="1:2:homologation:wsfe",
            )
            second = authenticate_wsaa(
                creds,
                cuit="20300000003",
                transport=transport,
                cache_getter=cache.get,
                cache_setter=cache.__setitem__,
                cache_key="1:2:homologation:wsfe",
            )
        self.assertEqual(transport.logins, 1)
        self.assertEqual(first.token, "NEW-TOKEN")
        self.assertEqual(second.token, "NEW-TOKEN")
        self.assertEqual(len(cache), 1)

    def test_expired_ta_requests_a_new_one(self):
        cache = {"k": self._fresh_ticket(minutes=-1, token="OLD-TOKEN")}
        transport = self._auth_transport(lambda: self._login_xml("REPLACED"))
        creds = ArcaCredentials(certificate_pem=b"x", private_key_pem=b"y")
        with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
            ticket = authenticate_wsaa(
                creds,
                cuit="20300000003",
                transport=transport,
                cache_getter=cache.get,
                cache_setter=cache.__setitem__,
                cache_key="k",
            )
        self.assertEqual(transport.logins, 1)
        self.assertEqual(ticket.token, "REPLACED")

    def test_ta_inside_renewal_margin_requests_a_new_one(self):
        cache = {"k": self._fresh_ticket(minutes=4, token="SOON")}
        transport = self._auth_transport(lambda: self._login_xml("RENEWED"))
        creds = ArcaCredentials(certificate_pem=b"x", private_key_pem=b"y")
        with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
            ticket = authenticate_wsaa(
                creds,
                cuit="20300000003",
                transport=transport,
                cache_getter=cache.get,
                cache_setter=cache.__setitem__,
                cache_key="k",
            )
        self.assertEqual(transport.logins, 1)
        self.assertEqual(ticket.token, "RENEWED")

    def test_already_authenticated_does_not_retry_login(self):
        calls = {"n": 0}

        def _fail():
            calls["n"] += 1
            raise WsaaAuthError(
                debug_key=DEBUG_WSAA_TA_EXISTS,
                http_status=500,
                faultcode="ns1:coe.alreadyAuthenticated",
                faultstring="El CEE ya posee un TA valido",
            )

        transport = self._auth_transport(_fail)
        creds = ArcaCredentials(certificate_pem=b"x", private_key_pem=b"y")
        with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
            with self.assertRaises(WsaaAuthError) as raised:
                authenticate_wsaa(
                    creds,
                    cuit="20300000003",
                    transport=transport,
                    cache_getter=lambda _key: None,
                    cache_setter=lambda _key, _ticket: None,
                    cache_key="k",
                )
        self.assertEqual(transport.logins, 1)
        self.assertEqual(raised.exception.debug_key, DEBUG_WSAA_TA_EXISTS)
        self.assertEqual(raised.exception.user_key, USER_TA_PENDING_KEY)
        self.assertEqual(str(raised.exception), USER_TA_PENDING_KEY)

    def test_already_authenticated_reuses_unexpired_cached_ta(self):
        cached = self._fresh_ticket(minutes=4, token="CACHED-TOKEN")

        def _fail():
            raise WsaaAuthError(
                debug_key=DEBUG_WSAA_TA_EXISTS,
                http_status=500,
                faultcode="ns1:coe.alreadyAuthenticated",
                faultstring="El CEE ya posee un TA valido",
            )

        transport = self._auth_transport(_fail)
        creds = ArcaCredentials(certificate_pem=b"x", private_key_pem=b"y")
        with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
            ticket = authenticate_wsaa(
                creds,
                cuit="20300000003",
                transport=transport,
                cache_getter=lambda _key: cached,
                cache_setter=lambda _key, _ticket: None,
                cache_key="k",
            )
        self.assertEqual(ticket.token, "CACHED-TOKEN")
        self.assertEqual(transport.logins, 1)

    def test_auth_logs_do_not_include_secrets(self):
        cache = {}
        transport = self._auth_transport(lambda: self._login_xml("SECRET-TOKEN"))
        creds = ArcaCredentials(certificate_pem=b"x", private_key_pem=b"y")
        with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
            with self.assertLogs("modules.arca.wsaa", level="INFO") as captured:
                authenticate_wsaa(
                    creds,
                    cuit="20300000003",
                    transport=transport,
                    cache_getter=cache.get,
                    cache_setter=cache.__setitem__,
                    cache_key="k",
                )
        log_text = "\n".join(captured.output)
        self.assertNotIn("SECRET-TOKEN", log_text)
        self.assertNotIn("NEW-SIGN", log_text)
        self.assertNotIn("MOCKCMS", log_text)
