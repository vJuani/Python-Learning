"""ACM V5 web/PDF: real data, premium layout, agent isolation."""

from __future__ import annotations

import base64
import io
import os
import re
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_acm_v5.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.acm_service import agent_contact_for_acm, create_acm_for_property, finalize_acm, get_acm_view
from modules.agent_branding import get_agent_branding
from modules.agent_photo import save_agent_profile_photo
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.properties_repository import STATUS_APPROVED
from modules.formatting import format_money
from modules.pdf_acm_report import generate_acm_pdf_bytes
from web_app import app


def _pdf_haystack(payload):
    parts = [payload]
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", payload, re.S):
        raw = match.group(1)
        try:
            raw = base64.a85decode(raw, adobe=True, ignorechars=b" \t\r\n")
        except Exception:
            pass
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        parts.append(raw)
    return b"\n".join(parts)


def _png_bytes(size=(120, 160)):
    image = Image.new("RGBA", size, (12, 80, 200, 180))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class AcmV5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="acm-v5-tests")
        create_tables()
        cls.org = add_organization("ACM V5 Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana ACM", "Alto", cls.org)
        cls.agent_user = add_user(
            "acm_v5_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            email="ana.v5@jrh.test",
            first_name="Ana",
            last_name="ACM",
            phone="1144441111",
        )
        cls.admin = add_user(
            "acm_v5_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            agent_id=cls.agent_id,
            email="admin.v5@jrh.test",
        )
        cls.agent_record = {
            "id": cls.agent_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.agent_id,
        }
        cls.target = cls._prop("Av. Libertador 4200", "Nunez", 228000, 78, 3, 2)
        for address, price, meters in (
            ("Libertador 4300", 220000, 74),
            ("Libertador 4400", 232000, 80),
            ("Congreso 2100", 215000, 72),
            ("Quesada 1800", 240000, 82),
        ):
            cls._prop(address, "Nunez", price, meters, 3, 2)

    @classmethod
    def _prop(cls, address, neighborhood, price, meters, rooms, bedrooms):
        return add_property(
            address,
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=price,
            listing_purpose="sale",
            listing_currency="USD",
            neighborhood=neighborhood,
            rooms=rooms,
            bedrooms=bedrooms,
            covered_m2=meters,
            total_m2=meters,
        )

    def _login(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.agent_user
            sess["role"] = ROLE_AGENT
            sess["organization_id"] = self.org
        return client

    def _create(self):
        return create_acm_for_property(
            self.org,
            user=self.agent_record,
            property_id=self.target,
            language="es",
        )

    def test_01_detail_renders_v5_chrome(self):
        view = self._create()
        html = self._login().get(f"/acm/{view['acm']['id']}").get_data(as_text=True)
        self.assertEqual(self._login().get(f"/acm/{view['acm']['id']}").status_code, 200)
        self.assertIn("acm-v5-header", html)
        self.assertIn("acm-v5-stepper", html)
        self.assertIn("acm-v5-target", html)
        self.assertIn("acm-v5-ai", html)
        self.assertIn("acm-v5-result", html)
        self.assertIn("acmPpm2Chart", html)
        self.assertIn("Descargar PDF con datos del agente", html)
        self.assertNotIn("Zonaprop", html)
        self.assertNotIn("Argenprop", html)
        self.assertNotIn("Mercado Libre", html)

    def test_02_mobile_page_200(self):
        view = self._create()
        response = self._login().get(
            f"/acm/{view['acm']['id']}",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("acm-v5-header", response.get_data(as_text=True))

    def test_03_web_and_pdf_share_reference_and_range(self):
        view = self._create()
        view["agent_contact"] = agent_contact_for_acm(view)
        html = self._login().get(f"/acm/{view['acm']['id']}").get_data(as_text=True)
        estimated = format_money(view["acm"]["estimated_value"], currency="USD", language="es")
        low = format_money(view["acm"]["suggested_min_value"], currency="USD", language="es")
        high = format_money(view["acm"]["suggested_max_value"], currency="USD", language="es")
        self.assertIn(estimated, html)
        payload = generate_acm_pdf_bytes(view, include_agent=True, language="es").read()
        text = _pdf_haystack(payload).decode("latin-1", "ignore")
        self.assertIn(estimated, text)
        self.assertTrue(low in text or low.replace("USD ", "") in text)
        self.assertTrue(high in text or high.replace("USD ", "") in text)
        self.assertIn(str(view["metrics"]["confidence_pct"]), html)

    def test_04_pdf_a4_and_agent_footer(self):
        view = self._create()
        view["agent_contact"] = agent_contact_for_acm(view)
        save_agent_profile_photo(self.org, self.agent_id, _png_bytes(), content_type="image/png")
        view["agent_contact"] = get_agent_branding(self.agent_id, self.org)
        with_agent = generate_acm_pdf_bytes(view, include_agent=True, language="es").read()
        without = generate_acm_pdf_bytes(view, include_agent=False, language="es").read()
        self.assertTrue(re.search(rb"595\.2\d+\s+841\.8\d+", with_agent) or b"/MediaBox" in with_agent)
        hay = _pdf_haystack(with_agent)
        self.assertIn(b"Ana ACM", hay)
        self.assertIn(b"ana.v5@jrh.test", hay)
        self.assertNotIn(b"admin.v5@jrh.test", hay)
        self.assertIn(b"INFORME ACM", hay)
        silent = _pdf_haystack(without)
        self.assertNotIn(b"Ana ACM", silent)
        self.assertNotIn(b"ana.v5@jrh.test", silent)
        self.assertNotIn(b"1144441111", silent)
        self.assertIn(b"JRH One", silent)

    def test_05_pdf_survives_remote_image_failure(self):
        view = self._create()
        view["photo_urls"] = ["https://example.invalid/missing.jpg"]
        view["agent_contact"] = agent_contact_for_acm(view)
        payload = generate_acm_pdf_bytes(view, include_agent=True, language="es").read()
        self.assertGreater(len(payload), 800)
        self.assertTrue(payload.startswith(b"%PDF"))

    def test_06_missing_photo_does_not_break_pdf(self):
        view = self._create()
        view["agent_contact"] = agent_contact_for_acm(view)
        view["agent_contact"]["has_photo"] = False
        view["agent_contact"]["profile_photo_key"] = None
        payload = generate_acm_pdf_bytes(view, include_agent=True, language="es").read()
        self.assertTrue(payload.startswith(b"%PDF"))
        self.assertIn(b"Ana ACM", _pdf_haystack(payload))

    def test_07_finalized_snapshot_preserved(self):
        view = self._create()
        before = view["acm"]["estimated_value"]
        finalize_acm(view["acm"]["id"], self.org, user=self.agent_record, language="es")
        later = get_acm_view(view["acm"]["id"], self.org, user=self.agent_record, language="es")
        self.assertEqual(later["acm"]["status"], "finalized")
        self.assertEqual(later["acm"]["estimated_value"], before)

    def test_08_agent_only_permissions(self):
        view = self._create()
        admin = app.test_client()
        with admin.session_transaction() as sess:
            sess["user_id"] = self.admin
            sess["role"] = ROLE_ADMIN
            sess["organization_id"] = self.org
        self.assertEqual(admin.get(f"/acm/{view['acm']['id']}").status_code, 403)

    def test_09_contact_comes_from_agent_not_admin(self):
        view = self._create()
        contact = agent_contact_for_acm(view)
        self.assertEqual(contact["email"], "ana.v5@jrh.test")
        self.assertEqual(contact["name"], "Ana ACM")
        self.assertNotEqual(contact["email"], "admin.v5@jrh.test")


if __name__ == "__main__":
    unittest.main()
