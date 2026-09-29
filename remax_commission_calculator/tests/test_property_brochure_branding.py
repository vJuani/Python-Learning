"""Property brochure uses the office brand, never another office or the product logo."""

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
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "brochure_brand.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["UPLOAD_DIR"] = str(Path(_TEST_TMP.name) / "static-uploads")
os.environ.pop("DATABASE_URL", None)

from modules.agent_contact_channels import link_agent_instagram, link_agent_whatsapp
from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config, get_private_upload_root
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.organization_settings_repository import (
    update_organization_marketing_fields,
    update_organization_settings,
)
from modules.database.properties_repository import STATUS_APPROVED
from modules.database.users_repository import get_user_by_id
from modules.organization_marketing_logo import marketing_logo_logical_key
from modules.property_brochure import generate_property_brochure
from web_app import app


def _pdf_text(payload):
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
    text = b"\n".join(parts).decode("latin-1", "ignore")
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), text)


class PropertyBrochureBrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="brochure-brand")
        create_tables()
        root = Path(os.environ["PRIVATE_UPLOAD_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        pwd = hash_password("Password1")
        cls.org_a = add_organization("Achard Propiedades QA")
        cls.org_b = add_organization("Otra Inmobiliaria")
        update_organization_settings(
            cls.org_a,
            "Achard Propiedades QA",
            "es",
            "ARS",
            "America/Argentina/Buenos_Aires",
            None,
            "#0f766e",
        )
        update_organization_marketing_fields(
            cls.org_a,
            marketing_brand_name="Achard Propiedades QA",
            legal_broker_name="Martín Prueba",
            legal_broker_license="QA-0000",
            legal_footer_line="Pie legal Achard QA",
        )
        logo_key = marketing_logo_logical_key(cls.org_a, ".png")
        logo_path = get_private_upload_root() / logo_key
        logo_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 80), (15, 118, 110)).save(logo_path, format="PNG")
        update_organization_marketing_fields(
            cls.org_a,
            marketing_logo_path=logo_key,
            marketing_logo_source="upload",
        )
        update_organization_marketing_fields(
            cls.org_b,
            marketing_brand_name="Otra Inmobiliaria",
            legal_broker_name="Laura Ajena",
            legal_broker_license="OTRA-999",
            legal_office_name="RE/MAX Data House",
            legal_footer_line="Corredor Público Mauro Marvisi CUCICBA 1762",
        )
        cls.agent_a = add_agent("Emilio", "Alto", cls.org_a)
        cls.agent_b = add_agent("Agente Ajeno", "Alto", cls.org_b)
        cls.user_a = add_user(
            "emilio_brand",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
            is_active=True,
            first_name="Emilio",
            last_name="Perez",
            phone="+54 11 4444-0000",
            email="emilio@achard.test",
        )
        cls.user_b = add_user(
            "ajeno_brand",
            pwd,
            ROLE_AGENT,
            cls.org_b,
            agent_id=cls.agent_b,
            is_active=True,
            first_name="Agente",
            last_name="Ajeno",
            email="ajeno@otra.test",
        )
        link_agent_whatsapp(cls.agent_a, cls.org_a, "1155554321", enable_marketing=True)
        link_agent_instagram(cls.agent_a, cls.org_a, "emilio.achard", enable_marketing=True)
        cls.prop_a = add_property(
            "Calle Test 1234",
            "Buenos Aires",
            cls.org_a,
            agent_id=cls.agent_a,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=185000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        cls.prop_b = add_property(
            "Calle Ajena 9",
            "Buenos Aires",
            cls.org_b,
            agent_id=cls.agent_b,
            status=STATUS_APPROVED,
            listing_price=90000,
            listing_currency="USD",
            listing_purpose="sale",
        )

    @classmethod
    def tearDownClass(cls):
        _TEST_TMP.cleanup()

    def _user(self, user_id):
        return get_user_by_id(user_id)

    def test_office_brand_and_legal_stay_on_that_property(self):
        result = generate_property_brochure(
            self.prop_a,
            self.org_a,
            True,
            self._user(self.user_a),
        )
        text = _pdf_text(result["pdf_bytes"])
        org = result["assets"]["organization_branding"]
        self.assertEqual(org["name"], "Achard Propiedades QA")
        self.assertEqual(org["legal_broker_name"], "Martín Prueba")
        self.assertEqual(org["legal_broker_license"], "QA-0000")
        self.assertEqual(org["legal_footer_line"], "Pie legal Achard QA")
        self.assertTrue(str(org["logo"]).endswith("marketing_logo.png"))
        self.assertIn(f"organizations{os.sep}{self.org_a}{os.sep}", str(org["logo"]).replace("/", os.sep))
        self.assertEqual(result["agent"]["name"], "Emilio Perez")
        self.assertIn("Achard Propiedades QA", text)
        self.assertIn("Emilio Perez", text)
        self.assertIn("emilio@achard.test", text)
        self.assertIn("+54 11 4444-0000", text)
        self.assertIn("+54 9 11 5555 4321", text)
        self.assertIn("@emilio.achard", text)
        self.assertIn("Martín Prueba", text)
        self.assertIn("QA-0000", text)
        self.assertIn("Pie legal Achard QA", text)
        self.assertIn("USD 185.000,00", text)
        self.assertIn("AGENTE RESPONSABLE", text)
        self.assertIn("Powered by JRH One", text)
        self.assertNotIn("Laura Ajena", text)
        self.assertNotIn("OTRA-999", text)
        self.assertNotIn("Data House", text)
        self.assertNotIn("Mauro Marvisi", text)
        self.assertNotIn("ajeno@otra.test", text)
        self.assertTrue(result["filename"].startswith("Achard"))
        self.assertFalse(result["filename"].startswith("JRH"))

    def test_other_office_does_not_receive_this_brand(self):
        result = generate_property_brochure(
            self.prop_b,
            self.org_b,
            True,
            self._user(self.user_b),
        )
        text = _pdf_text(result["pdf_bytes"])
        self.assertIn("Otra Inmobiliaria", text)
        self.assertIn("Laura Ajena", text)
        self.assertIn("OTRA-999", text)
        self.assertNotIn("Achard Propiedades QA", text)
        self.assertNotIn("Emilio Perez", text)
        self.assertNotIn("QA-0000", text)
        self.assertNotIn("Martín Prueba", text)
        self.assertNotIn("emilio@achard.test", text)
        self.assertIsNone(result["assets"]["organization_branding"]["logo"])

    def test_without_agent_keeps_office_legal_identity(self):
        result = generate_property_brochure(
            self.prop_a,
            self.org_a,
            False,
            self._user(self.user_a),
        )
        text = _pdf_text(result["pdf_bytes"])
        self.assertNotIn("emilio@achard.test", text)
        self.assertNotIn("+54 11 4444-0000", text)
        self.assertNotIn("@emilio.achard", text)
        self.assertIn("Achard Propiedades QA", text)
        self.assertIn("QA-0000", text)
        self.assertIn("Martín Prueba", text)
        self.assertIn("Powered by JRH One", text)

    def test_missing_logo_uses_commercial_name(self):
        org = add_organization("Estudio Norte")
        update_organization_marketing_fields(
            org,
            marketing_brand_name="Estudio Norte",
            legal_broker_name="Ana Norte",
            legal_broker_license="NORTE-1",
        )
        agent = add_agent("Ana", "Alto", org)
        user = add_user(
            "ana_norte",
            hash_password("Password1"),
            ROLE_AGENT,
            org,
            agent_id=agent,
            is_active=True,
            first_name="Ana",
            last_name="Norte",
            email="ana@norte.test",
        )
        prop = add_property(
            "Calle Norte 1",
            "Buenos Aires",
            org,
            agent_id=agent,
            status=STATUS_APPROVED,
            listing_price=100000,
            listing_currency="USD",
        )
        result = generate_property_brochure(prop, org, True, get_user_by_id(user))
        text = _pdf_text(result["pdf_bytes"])
        self.assertIsNone(result["assets"]["organization_branding"]["logo"])
        self.assertEqual(result["assets"]["organization_branding"]["name"], "Estudio Norte")
        self.assertIn("Estudio Norte", text)
        self.assertNotIn("Data House", text)
        self.assertNotIn("Mauro Marvisi", text)
