"""WSAA TRA timestamps and sanitized SOAP fault handling."""

from __future__ import annotations

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_arca_wsaa.db")
os.environ["INVOICE_PROVIDER"] = "arca"
os.environ["ARCA_ENV"] = "homologation"
os.environ["SECRET_KEY"] = "test-arca-wsaa-secret"

from modules.arca.wsaa import (  # noqa: E402
    ARCA_TRA_TZ,
    DEBUG_WSAA_SCHEMA,
    DEBUG_WSAA_UNAVAILABLE,
    USER_AUTH_ERROR_KEY,
    WsaaAuthError,
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
