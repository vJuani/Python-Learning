"""Property Detail V2 — premium listing sheet. Display only."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from PIL import Image

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_property_detail_v2.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("DATABASE_URL", None)

from modules.acm_service import create_acm_for_property
from modules.agent_photo import save_agent_profile_photo
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.properties_repository import STATUS_APPROVED
from modules.database.property_media_repository import STRATEGY_REMOTE, upsert_property_media
from modules.database.users_repository import get_user_by_id
from modules.property_detail_view import (
    compact_property_title,
    extra_feature_chips,
    maintenance_line,
    public_source_badge,
)
from modules.property_documents import create_property_document_with_files
from web_app import app


def _png_bytes():
    image = Image.new("RGBA", (80, 100), (12, 80, 200, 180))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_bytes():
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n"


class FakeStorage:
    def __init__(self, filename, data, mimetype="application/pdf"):
        self.filename = filename
        self.stream = io.BytesIO(data)
        self.mimetype = mimetype


class PropertyDetailV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="property-detail-v2")
        create_tables()
        cls.org = add_organization("Detail V2 Org")
        cls.other_org = add_organization("Other Detail Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana Listing", "Alto", cls.org)
        cls.agent_b_id = add_agent("Bruno Other", "Alto", cls.org)
        cls.other_agent = add_agent("Clara Other Org", "Alto", cls.other_org)
        cls.agent_user = add_user(
            "detail_v2_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            is_active=True,
            first_name="Ana",
            last_name="Listing",
            phone="1144441111",
            email="ana.listing@jrh.test",
        )
        cls.agent_b_user = add_user(
            "detail_v2_agent_b",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_b_id,
            is_active=True,
            first_name="Bruno",
            last_name="Other",
            email="bruno.other@jrh.test",
        )
        cls.admin = add_user(
            "detail_v2_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            is_active=True,
            first_name="Admin",
            last_name="Office",
            email="admin.detail@jrh.test",
        )
        cls.other_admin = add_user(
            "detail_v2_other_admin",
            pwd,
            ROLE_ADMIN,
            cls.other_org,
            is_active=True,
            email="other.admin@jrh.test",
        )
        cls.manual_id = add_property(
            "Italia 100",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=210000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Palermo",
            locality="CABA",
            rooms=3,
            bedrooms=2,
            bathrooms=1,
            covered_m2=70,
            parking_spaces=1,
            description="Manual corta.",
        )
        long_address = (
            "Martín Rodríguez 2268 (B1644), San Fernando, Buenos Aires, Argentina"
        )
        cls.synced_id = add_property(
            long_address,
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            property_type="house",
            listing_price=159000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="San Fernando",
            locality="San Fernando",
            administrative_area="Buenos Aires",
            country="Argentina",
            postal_code="B1644",
            rooms=4,
            bedrooms=3,
            bathrooms=1,
            covered_m2=105,
            parking_spaces=1,
            latitude=-34.441,
            longitude=-58.558,
            formatted_address=long_address,
            description=("Casa luminosa. " * 40).strip(),
            features={"pool": True, "grill": True, "garden": True},
        )
        cls._mark_external(
            cls.synced_id,
            source="redremax",
            external_id="AR.42.27.1.2268",
            metadata={
                "mlsid": "2268-SF",
                "street": "Martín Rodríguez",
                "street_number": "2268",
                "maintenance": {"value": 685000, "currency": "ARS"},
                "year_build": 2010,
                "apt_credit": True,
                "dimensions": {
                    "covered": 105,
                    "land": 346,
                    "uncovered": 20,
                    "total_built": 125,
                },
            },
        )
        for index in range(5):
            upsert_property_media(
                cls.org,
                cls.synced_id,
                source="redremax",
                external_media_id=f"photo-{index}",
                original_url=f"https://redremax-images.s3.amazonaws.com/listings/photo-{index}.jpg",
                storage_strategy=STRATEGY_REMOTE,
                is_cover=index == 0,
                position=index,
            )
        cls.empty_id = add_property(
            "Sin Fotos 12",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            property_type="ph",
            listing_price=99000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        cls.other_prop = add_property(
            "Otra Org 1",
            "CABA",
            cls.other_org,
            agent_id=cls.other_agent,
            status=STATUS_APPROVED,
            listing_price=100000,
            listing_currency="USD",
        )
        cls.other_agent_prop = add_property(
            "De Bruno 9",
            "CABA",
            cls.org,
            agent_id=cls.agent_b_id,
            status=STATUS_APPROVED,
            listing_price=120000,
            listing_currency="USD",
        )

    @classmethod
    def tearDownClass(cls):
        _TEST_TMP.cleanup()

    @classmethod
    def _mark_external(cls, property_id, *, source, external_id, metadata):
        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE properties
                SET external_source = ?, external_id = ?, last_synced_at = ?,
                    external_metadata_json = ?
                WHERE id = ? AND organization_id = ?
                """,
                (
                    source,
                    external_id,
                    "2026-09-10T12:00:00",
                    json.dumps(metadata),
                    property_id,
                    cls.org,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _login(self, user_id):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        return client

    def _html(self, user_id, property_id, **kwargs):
        client = self._login(user_id)
        response = client.get(f"/properties/{property_id}", **kwargs)
        return response, response.get_data(as_text=True)

    def test_01_manual_property_render(self):
        response, html = self._html(self.admin, self.manual_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Italia 100", html)
        self.assertIn("Manual", html)
        self.assertIn("is-property-detail-page", html)
        self.assertIn("property-detail.css", html)
        self.assertNotIn("Mock Network", html)

    def test_02_redremax_property_render(self):
        response, html = self._html(self.admin, self.synced_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("RedREMAX", html)
        self.assertIn("Martín Rodríguez 2268", html)
        self.assertIn("photo-0.jpg", html)
        self.assertNotIn("Mock Network", html)

    def test_03_cover_image(self):
        _, html = self._html(self.admin, self.synced_id)
        self.assertIn("property-gallery__hero", html)
        self.assertIn("fetchpriority=\"high\"", html)
        self.assertIn("photo-0.jpg", html)

    def test_04_gallery(self):
        _, html = self._html(self.admin, self.synced_id)
        self.assertIn("property-gallery", html)
        self.assertIn("Ver todas las fotos", html)
        self.assertIn("photo-1.jpg", html)
        self.assertIn("loading=\"lazy\"", html)

    def test_05_no_image_placeholder(self):
        _, html = self._html(self.admin, self.empty_id)
        self.assertIn("Sin fotos disponibles", html)
        self.assertIn("property-media-placeholder", html)
        self.assertIn("pd-gallery__empty", html)
        self.assertNotIn("photo-0.jpg", html)

    def test_06_long_address_compact_display(self):
        row = {
            "address": "Martín Rodríguez 2268 (B1644), San Fernando, Buenos Aires, Argentina",
            "external_metadata_json": json.dumps(
                {"street": "Martín Rodríguez", "street_number": "2268"}
            ),
        }
        self.assertEqual(compact_property_title(row), "Martín Rodríguez 2268")
        _, html = self._html(self.admin, self.synced_id)
        self.assertIn("<h1 class=\"pd-title\">Martín Rodríguez 2268</h1>", html)
        self.assertNotIn("<h1 class=\"pd-title\">Martín Rodríguez 2268 (B1644)", html)

    def test_07_price_formatting(self):
        _, html = self._html(self.admin, self.synced_id)
        self.assertIn("USD 159.000,00", html)
        self.assertIn("Expensas", html)
        self.assertIn("ARS 685.000", html)

    def test_08_optional_fields_hidden_when_null(self):
        _, html = self._html(self.admin, self.empty_id)
        self.assertNotIn("Expensas", html)
        self.assertNotIn("Sobre esta propiedad", html)
        self.assertIsNone(maintenance_line({"external_metadata_json": "{}"}))

    def test_09_agent_card_uses_correct_agent(self):
        _, html = self._html(self.admin, self.synced_id)
        card = re.search(r'data-pd-agent.*?</article>', html, re.S)
        self.assertIsNotNone(card)
        self.assertIn("Ana Listing", card.group(0))
        self.assertNotIn("Admin Office", card.group(0))
        _, other = self._html(self.admin, self.other_agent_prop)
        other_card = re.search(r'data-pd-agent.*?</article>', other, re.S)
        self.assertIsNotNone(other_card)
        self.assertIn("Bruno Other", other_card.group(0))
        self.assertNotIn("Ana Listing", other_card.group(0))

    def test_10_agent_photo_shown_if_available(self):
        save_agent_profile_photo(self.org, self.agent_id, _png_bytes(), content_type="image/png")
        _, html = self._html(self.admin, self.synced_id)
        self.assertIn(f"/agents/{self.agent_id}/profile-photo", html)

    def test_11_maps(self):
        _, html = self._html(self.admin, self.synced_id)
        self.assertTrue(
            "data-jrh-maps" in html or "Cómo llegar" in html or "google.com/maps" in html
        )
        self.assertIn("Cómo llegar", html)

    def test_12_acm_cta(self):
        _, html = self._html(self.agent_user, self.synced_id)
        self.assertIn("Análisis de mercado", html)
        self.assertIn("Crear ACM", html)
        self.assertIn(f"/acm/new?property_id={self.synced_id}", html)

    def test_13_existing_acm_summary(self):
        user = get_user_by_id(self.agent_user)
        view = create_acm_for_property(
            self.org,
            user=user,
            property_id=self.synced_id,
        )
        connection = get_connection()
        try:
            connection.execute(
                "UPDATE property_acms SET estimated_value = ?, currency = ? WHERE id = ?",
                (155000, "USD", view["acm"]["id"]),
            )
            connection.commit()
        finally:
            connection.close()
        _, html = self._html(self.agent_user, self.synced_id)
        self.assertIn("Último ACM", html)
        self.assertIn("Ver ACM", html)
        self.assertIn("USD 155.000,00", html)

    def test_14_documents_permission(self):
        user = get_user_by_id(self.agent_user)
        document, error = create_property_document_with_files(
            property_id=self.manual_id,
            organization_id=self.org,
            requesting_user=user,
            document_type="other",
            title="ContratoPrivadoV2.pdf",
            file_storages=[FakeStorage("ContratoPrivadoV2.pdf", _pdf_bytes())],
        )
        self.assertIsNone(error)
        self.assertIsNotNone(document)
        _, html = self._html(self.agent_user, self.manual_id)
        self.assertIn("Documentación", html)
        self.assertIn("ContratoPrivadoV2.pdf", html)
        other = self._login(self.other_admin).get(f"/properties/{self.manual_id}")
        self.assertIn(other.status_code, (302, 403, 404))

    def test_15_source_metadata(self):
        _, html = self._html(self.admin, self.synced_id)
        self.assertIn("Información de origen", html)
        self.assertIn("MLS #2268-SF", html)
        self.assertIn("Sincronizada", html)
        self.assertNotIn("Mock Network", html)
        self.assertEqual(public_source_badge({"external_source": "mock_network"}), "Importada")

    def test_16_another_org_denied(self):
        response = self._login(self.admin).get(f"/properties/{self.other_prop}")
        self.assertIn(response.status_code, (302, 403, 404))

    def test_17_agent_scope(self):
        response = self._login(self.agent_user).get(f"/properties/{self.other_agent_prop}")
        self.assertIn(response.status_code, (302, 403, 404))
        ok, html = self._html(self.agent_user, self.synced_id)
        self.assertEqual(ok.status_code, 200)
        self.assertIn("Ana Listing", html)

    def test_18_mobile_200(self):
        response, html = self._html(
            self.admin,
            self.synced_id,
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("pd-mobilebar", html)

    def test_19_dark_mode_classes(self):
        css = (Path(__file__).resolve().parents[1] / "static/css/property-detail.css").read_text(encoding="utf-8")
        self.assertIn('[data-theme="dark"]', css)
        _, html = self._html(self.admin, self.synced_id)
        self.assertIn("property-detail.css", html)
        self.assertNotIn("filter: brightness(0)", html)

    def test_20_remote_image_failure_safe(self):
        _, html = self._html(self.admin, self.synced_id)
        self.assertIn("data-media-fallback", html)
        self.assertIn("__jrhMediaFallback", html)
        self.assertIn("referrerpolicy=\"no-referrer\"", html)

    def test_numeric_feature_ids_hidden(self):
        labels = extra_feature_chips({"feature_labels": ["Pileta", "1842", "Parrilla"]})
        self.assertEqual(labels, ["Pileta", "Parrilla"])
