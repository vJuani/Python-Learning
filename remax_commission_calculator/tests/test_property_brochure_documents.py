"""
Property brochure PDF and private property documents.
"""

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
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_property_media.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config, get_private_upload_root
from modules.contacts import create_agent_contact
from modules.database import add_agent, add_user, create_tables
from modules.database.organizations_repository import add_organization
from modules.database.properties_repository import add_property
from modules.database.property_documents_repository import (
    STATUS_ARCHIVED,
    get_property_document,
    list_property_document_files,
)
from modules.pdf_document_normalize import normalize_document_to_pdf
from modules.property_brochure import generate_property_brochure
from modules.property_documents import (
    MAX_DOCUMENT_BYTES,
    absolute_normalized_pdf_path,
    absolute_property_file_path,
    create_property_document_with_files,
    validate_property_document_upload,
)
from modules.property_media_access import PropertyMediaError
from web_app import app


def _pdf_bytes(size=220):
    payload = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    payload += b"1 0 obj<<>>endobj\ntrailer<<>>\n"
    if size > len(payload):
        payload += b"0" * (size - len(payload))
    return payload


def _image_bytes(fmt="JPEG", color=(200, 40, 40), size=(48, 36)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


class FakeStorage:
    def __init__(self, filename, data, mimetype="application/octet-stream"):
        self.filename = filename
        self.stream = io.BytesIO(data)
        self.mimetype = mimetype


class PropertyBrochureDocumentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "property-media-test"
        create_tables()

        cls.org_a = add_organization("JRH One")
        cls.org_b = add_organization("Otra Org")
        cls.agent_a1 = add_agent("Juan Perez", "Alto", cls.org_a)
        cls.agent_a2 = add_agent("Otro Agente", "Alto", cls.org_a)
        cls.agent_b = add_agent("Agente B", "Alto", cls.org_b)
        pwd = hash_password("Password1")
        cls.admin_a = add_user(
            "admin_media_a",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
            is_active=True,
            email="admin.media@example.com",
        )
        cls.user_a1 = add_user(
            "agent_media_a1",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a1,
            is_active=True,
            first_name="Juan",
            last_name="Perez",
            phone="+54 11 5555-1234",
            email="juan.perez@example.com",
        )
        cls.user_a2 = add_user(
            "agent_media_a2",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a2,
            is_active=True,
            email="otro.agente@example.com",
        )
        cls.admin_b = add_user(
            "admin_media_b",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
            is_active=True,
            email="admin.b@media.test",
        )
        cls.property_a = add_property(
            "Av. Libertador 4200",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a1,
            status="approved",
            property_type="apartment",
            listing_price=235000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Nunez",
            rooms=3,
            bedrooms=2,
            bathrooms=1,
            total_m2=78,
            covered_m2=70,
            parking_spaces=1,
            description="Departamento luminoso sobre Libertador.",
            features={"balcony": True},
            external_id="COM-00421",
        )
        cls.property_a2 = add_property(
            "Cabildo 1000",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a2,
            status="approved",
            property_type="apartment",
            listing_price=180000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Belgrano",
        )
        cls.property_b = add_property(
            "Santa Fe 100",
            "CABA",
            cls.org_b,
            agent_id=cls.agent_b,
            status="approved",
            property_type="apartment",
            listing_price=200000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        cls.contact = create_agent_contact(
            cls.org_a,
            cls.agent_a1,
            {
                "name": "Carolina Match",
                "phone": "5491112345678",
                "preferences": {
                    "areas": ["Nunez"],
                    "budget": {
                        "min": 200000,
                        "max": 250000,
                        "currency": "USD",
                    },
                    "property_types": ["apartment"],
                    "purpose": "sale",
                },
            },
        )

    @classmethod
    def tearDownClass(cls):
        _TEST_TMP.cleanup()

    def _client_as(self, user_id):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess.clear()
            sess["user_id"] = user_id
        return client

    def _user(self, user_id):
        from modules.database import get_user_by_id
        return get_user_by_id(user_id)

    def _pdf_text(self, pdf_bytes):
        parts = []
        for match in re.finditer(rb"stream\r?\n(.+?)endstream", pdf_bytes, re.S):
            decoded = _decode_pdf_stream(match.group(1))
            if decoded:
                parts.append(decoded.decode("latin-1", errors="ignore"))
        return "\n".join(parts)

    def test_01_brochure_with_agent_includes_contact(self):
        result = generate_property_brochure(
            self.property_a,
            self.org_a,
            True,
            self._user(self.user_a1),
        )
        text = self._pdf_text(result["pdf_bytes"])
        self.assertEqual(result["agent"]["name"], "Juan Perez")
        self.assertTrue("Juan Perez" in text, "agent name missing from PDF")
        self.assertTrue("+54 11 5555-1234" in text, "agent phone missing from PDF")
        self.assertTrue(
            "juan.perez@example.com" in text,
            "agent email missing from PDF",
        )
        self.assertTrue(result["include_agent_contact"])

    def test_02_brochure_without_agent_omits_contact(self):
        result = generate_property_brochure(
            self.property_a,
            self.org_a,
            False,
            self._user(self.user_a1),
        )
        text = self._pdf_text(result["pdf_bytes"])
        self.assertNotIn("+54 11 5555-1234", text)
        self.assertNotIn("juan.perez@example.com", text)
        self.assertFalse(result["include_agent_contact"])

    def test_03_other_org_rejected(self):
        with self.assertRaises(PropertyMediaError) as raised:
            generate_property_brochure(
                self.property_b,
                self.org_a,
                True,
                self._user(self.admin_a),
            )
        self.assertEqual(raised.exception.status_code, 404)

        client = self._client_as(self.admin_a)
        response = client.post(
            f"/properties/{self.property_b}/brochure",
            data={"include_agent_contact": "1"},
        )
        self.assertIn(response.status_code, (302, 404))
        self.assertNotEqual(response.mimetype, "application/pdf")

    def test_04_agent_without_access_rejected(self):
        with self.assertRaises(PropertyMediaError) as raised:
            generate_property_brochure(
                self.property_a,
                self.org_a,
                True,
                self._user(self.user_a2),
            )
        self.assertEqual(raised.exception.status_code, 403)

        client = self._client_as(self.user_a2)
        response = client.post(
            f"/properties/{self.property_a}/brochure",
            data={"include_agent_contact": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.mimetype, "application/pdf")

    def test_05_staff_own_org_works(self):
        result = generate_property_brochure(
            self.property_a,
            self.org_a,
            True,
            self._user(self.admin_a),
        )
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))
        self.assertTrue(
            "Juan Perez" in self._pdf_text(result["pdf_bytes"]),
            "staff brochure is missing the property agent",
        )

    def test_06_brochure_pdf_is_generated(self):
        client = self._client_as(self.user_a1)
        response = client.post(
            f"/properties/{self.property_a}/brochure",
            data={"include_agent_contact": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF"))
        self.assertIn("COM-00421", response.headers.get("Content-Disposition", ""))

    def test_07_missing_property_returns_error(self):
        with self.assertRaises(PropertyMediaError) as raised:
            generate_property_brochure(
                999999,
                self.org_a,
                True,
                self._user(self.admin_a),
            )
        self.assertEqual(raised.exception.status_code, 404)

        client = self._client_as(self.admin_a)
        response = client.post(
            "/properties/999999/brochure",
            data={"include_agent_contact": "1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_08_jpg_accepted(self):
        payload, error = validate_property_document_upload(
            FakeStorage("foto.jpg", _image_bytes("JPEG"), "image/jpeg")
        )
        self.assertIsNone(error)
        self.assertEqual(payload["content_type"], "image/jpeg")

    def test_09_png_accepted(self):
        payload, error = validate_property_document_upload(
            FakeStorage("foto.png", _image_bytes("PNG"), "image/png")
        )
        self.assertIsNone(error)
        self.assertEqual(payload["content_type"], "image/png")

    def test_10_webp_accepted(self):
        payload, error = validate_property_document_upload(
            FakeStorage("foto.webp", _image_bytes("WEBP"), "image/webp")
        )
        self.assertIsNone(error)
        self.assertEqual(payload["content_type"], "image/webp")

    def test_11_pdf_accepted(self):
        payload, error = validate_property_document_upload(
            FakeStorage("escritura.pdf", _pdf_bytes(), "application/pdf")
        )
        self.assertIsNone(error)
        self.assertEqual(payload["content_type"], "application/pdf")

    def test_12_fake_extension_rejected(self):
        payload, error = validate_property_document_upload(
            FakeStorage(
                "falso.pdf",
                _image_bytes("JPEG"),
                "application/pdf",
            )
        )
        self.assertIsNone(payload)
        self.assertEqual(error, "property_doc_err_content_mismatch")

    def test_13_oversized_rejected(self):
        huge = b"%PDF-1.4\n" + (b"0" * (MAX_DOCUMENT_BYTES + 10))
        payload, error = validate_property_document_upload(
            FakeStorage("grande.pdf", huge, "application/pdf")
        )
        self.assertIsNone(payload)
        self.assertEqual(error, "property_doc_err_too_large")

    def test_14_16_multiple_images_pdf_keeps_originals(self):
        colors = ((220, 30, 30), (30, 180, 40), (30, 60, 210))
        storages = [
            FakeStorage(
                f"{index + 1}.jpg",
                _image_bytes("JPEG", color, (60 + index * 10, 40)),
                "image/jpeg",
            )
            for index, color in enumerate(colors)
        ]
        document, error = create_property_document_with_files(
            property_id=self.property_a,
            organization_id=self.org_a,
            requesting_user=self._user(self.user_a1),
            document_type="deed",
            title="Escritura",
            file_storages=storages,
        )
        self.assertIsNone(error)
        self.assertEqual(document["original_count"], 3)
        self.assertTrue(document["has_normalized_pdf"])
        files = list_property_document_files(document["id"], self.org_a)
        originals = []
        for item in files:
            path = absolute_property_file_path(item, document)
            self.assertTrue(path.is_file())
            self.assertTrue(str(path).startswith(str(get_private_upload_root())))
            self.assertNotIn(os.sep + "static" + os.sep, str(path))
            originals.append(path)

        normalized = absolute_normalized_pdf_path(document)
        self.assertIsNotNone(normalized)
        self.assertTrue(normalized.is_file())
        self.assertTrue(normalized.read_bytes().startswith(b"%PDF"))
        self.assertTrue(
            str(normalized).startswith(str(get_private_upload_root()))
        )

        ordered = normalize_document_to_pdf(
            originals,
            Path(_TEST_TMP.name) / "order-check.pdf",
        )
        self.assertEqual(ordered["page_count"], 3)

        self.assertEqual(
            [item["original_filename"] for item in files],
            ["1.jpg", "2.jpg", "3.jpg"],
        )
        self.assertEqual(
            [item["sort_order"] for item in files],
            [0, 1, 2],
        )

    def test_18_private_file_not_via_static(self):
        document, error = create_property_document_with_files(
            property_id=self.property_a,
            organization_id=self.org_a,
            requesting_user=self._user(self.user_a1),
            document_type="abl",
            title="ABL",
            file_storages=[
                FakeStorage("abl.pdf", _pdf_bytes(), "application/pdf")
            ],
        )
        self.assertIsNone(error)
        file_row = document["files"][0]
        path = absolute_property_file_path(file_row, document)
        client = self._client_as(self.user_a1)
        response = client.get(
            "/static/uploads/organizations/"
            f"{self.org_a}/properties/{self.property_a}/documents/"
            f"{document['id']}/{file_row['stored_name']}"
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(path.is_file())

    def test_19_download_validates_organization(self):
        document, error = create_property_document_with_files(
            property_id=self.property_a,
            organization_id=self.org_a,
            requesting_user=self._user(self.user_a1),
            document_type="expenses",
            title="Expensas",
            file_storages=[
                FakeStorage("expensas.pdf", _pdf_bytes(), "application/pdf")
            ],
        )
        self.assertIsNone(error)
        client = self._client_as(self.admin_b)
        response = client.get(
            f"/properties/{self.property_a}/documents/{document['id']}/download"
        )
        self.assertIn(response.status_code, (302, 404))
        self.assertNotEqual(response.mimetype, "application/pdf")

        own = self._client_as(self.admin_a)
        ok = own.get(
            f"/properties/{self.property_a}/documents/{document['id']}/download"
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.mimetype, "application/pdf")

    def test_20_archived_keeps_history(self):
        document, error = create_property_document_with_files(
            property_id=self.property_a,
            organization_id=self.org_a,
            requesting_user=self._user(self.user_a1),
            document_type="reservation",
            title="Reserva",
            file_storages=[
                FakeStorage("reserva.pdf", _pdf_bytes(), "application/pdf")
            ],
        )
        self.assertIsNone(error)
        original_path = absolute_property_file_path(
            document["files"][0],
            document,
        )
        client = self._client_as(self.user_a1)
        response = client.post(
            f"/properties/{self.property_a}/documents/{document['id']}/archive"
        )
        self.assertEqual(response.status_code, 302)
        archived = get_property_document(document["id"], self.org_a)
        self.assertEqual(archived["status"], STATUS_ARCHIVED)
        self.assertEqual(archived["archived_by_user_id"], self.user_a1)
        self.assertTrue(original_path.is_file())

        blocked = client.get(
            f"/properties/{self.property_a}/documents/{document['id']}/download"
        )
        self.assertIn(blocked.status_code, (302, 404))

    def test_21_mobile_upload_render_200(self):
        client = self._client_as(self.user_a1)
        response = client.get(
            f"/properties/{self.property_a}/documents/new"
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("accept=", html)
        self.assertIn("image/jpeg", html)
        self.assertIn("application/pdf", html)

    def test_22_property_detail_render_200(self):
        client = self._client_as(self.user_a1)
        response = client.get(f"/properties/{self.property_a}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Descargar ficha", html)
        self.assertIn("Documentación", html)

    def test_23_brochure_from_match_4c(self):
        client = self._client_as(self.user_a1)
        matches = client.get(
            f"/contacts/{self.contact['id']}/property-matches"
        )
        self.assertEqual(matches.status_code, 200)
        html = matches.get_data(as_text=True)
        self.assertIn(
            f"/properties/{self.property_a}/brochure",
            html,
        )
        brochure = client.get(f"/properties/{self.property_a}/brochure")
        self.assertEqual(brochure.status_code, 200)
        generated = client.post(
            f"/properties/{self.property_a}/brochure",
            data={"include_agent_contact": "0"},
        )
        self.assertEqual(generated.status_code, 200)
        self.assertEqual(generated.mimetype, "application/pdf")

    def test_24_create_tables_idempotent(self):
        create_tables(create_backup=False)
        create_tables(create_backup=False)
        still = get_property_document
        self.assertTrue(callable(still))
        result = generate_property_brochure(
            self.property_a,
            self.org_a,
            False,
            self._user(self.admin_a),
        )
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))


def _decode_pdf_stream(raw):
    payload = raw.strip()
    try:
        decoded = base64.a85decode(
            payload,
            adobe=True,
            ignorechars=b" \r\n\t",
        )
    except Exception:
        decoded = None

    if decoded:
        try:
            return zlib.decompress(decoded)
        except Exception:
            return decoded

    try:
        return zlib.decompress(payload)
    except Exception:
        return b""


if __name__ == "__main__":
    unittest.main()
