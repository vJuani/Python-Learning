"""Phase 5A.2: explicit RE/MAX catalog import → external_listings."""

from __future__ import annotations

import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_remax_catalog.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    count_properties,
    create_tables,
    get_properties,
)
from modules.database.external_listings_repository import (
    get_external_listing_by_source_id,
    list_active_external_listings,
    list_external_listings,
)
from modules.integrations import (
    confirm_remax_catalog,
    confirm_remax_export,
    preview_remax_catalog,
    preview_remax_export,
)
from modules.integrations.remax_catalog import RemaxCatalogError
from modules.listing_connectors.remax_export import RemaxExportConnector
from modules.listing_sources import (
    SEARCH_INDEXED,
    SEARCH_NOT_AUTHORIZED,
    SEARCH_UNSUPPORTED_SEARCH,
    SOURCE_ARGENPROP,
    SOURCE_INTERNAL,
    SOURCE_MERCADOLIBRE,
    SOURCE_REMAX,
    SOURCE_ZONAPROP,
    listing_source_capabilities,
    match_visible_sources,
)
from modules.listings_normalize import listing_from_external_listing
from modules.property_match import (
    build_whatsapp_message,
    decorate_match,
    match_properties,
    rank_contact_properties,
)
from web_app import app


OFFICE_CSV = """MLSID,Dirección,Altura,Código postal,Localidad,Status Listing,Tipo de Operación,Tipo de Propiedad,% al Comprador,% al Vendedor,Precio,Tipo de moneda
RM-OFF-100,Libertador,1000,C1425ABA,Palermo,Activa,Venta,Departamento Estándar,4,3,145000,USD
RM-OFF-101,Cabildo,200,B1636,Olivos,Reservada,Alquiler,Casa,4,15,2500,USD
"""

CATALOG_CSV = """MLSID,Dirección,Altura,Código postal,Localidad,Status Listing,Tipo de Operación,Tipo de Propiedad,Precio,Tipo de moneda,URL,Ambientes,Dormitorios,Baños,M2 cubiertos,M2 totales,Cocheras,Descripción
RM-CAT-100,Av. Cabildo,3200,C1428,Belgrano,Activa,Venta,Departamento Estándar,185000,USD,https://remax.com.ar/listings/RM-CAT-100,3,2,1,84,90,1,Departamento en Belgrano
RM-CAT-101,Libertador,4100,C1426,Palermo,Activa,Venta,Casa,420000,USD,,5,4,3,180,220,2,
"""

PARTIAL_CSV = """MLSID,Dirección,Altura,Código postal,Localidad,Status Listing,Tipo de Operación,Tipo de Propiedad,Precio,Tipo de moneda
RM-CAT-100,Av. Cabildo,3200,C1428,Belgrano,Activa,Venta,Departamento Estándar,190000,USD
"""

INVALID_ROWS_CSV = """MLSID,Dirección,Altura,Código postal,Localidad,Status Listing,Tipo de Operación,Tipo de Propiedad,Precio,Tipo de moneda
RM-OK-1,Santa Fe,1000,C1425,Palermo,Activa,Venta,Departamento Estándar,200000,USD
,Sin MLSID,10,C1425,Palermo,Activa,Venta,Departamento Estándar,100000,USD
RM-NO-ADDR,,,C1425,,Activa,Venta,Departamento Estándar,100000,USD
RM-BAD-PRICE,Cabildo,1,C1428,Belgrano,Activa,Venta,Departamento Estándar,abc,USD
RM-NO-CURR,Cabildo,2,C1428,Belgrano,Activa,Venta,Departamento Estándar,150000,
RM-BAD-CURR,Cabildo,3,C1428,Belgrano,Activa,Venta,Departamento Estándar,150000,EUR
RM-OK-1,Duplicada,20,C1425,Palermo,Activa,Venta,Departamento Estándar,210000,USD
"""


class RemaxCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-catalog-secret"
        create_tables()
        cls.org = add_organization("Catalog Org")
        cls.other_org = add_organization("Catalog Other")
        cls.password = "Password1"
        pwd = hash_password(cls.password)
        cls.admin = add_user(
            "catalog_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="catalog_admin@example.com",
        )
        cls.agent_id = add_agent("Catalog Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Other Catalog Agent", "Alto", cls.other_org)
        add_user(
            "catalog_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            email="catalog_agent@example.com",
        )
        cls.other_admin = add_user(
            "catalog_other_admin",
            pwd,
            ROLE_ADMIN,
            cls.other_org,
            email="catalog_other@example.com",
        )
        cls.prefs = {
            "areas": ["Belgrano"],
            "budget": {"min": 150000, "max": 200000, "currency": "USD"},
            "property_types": ["departamento"],
            "rooms": 3,
            "bedrooms": 2,
            "purpose": "sale",
        }
        cls.contact = create_agent_contact(
            cls.org,
            cls.agent_id,
            {
                "name": "Catalog Contact",
                "phone": "5491199990000",
                "preferences": cls.prefs,
            },
        )

    def _login_admin(self, client, *, org=None, user=None):
        with client.session_transaction() as sess:
            sess["user_id"] = user or self.admin
            sess["role"] = ROLE_ADMIN
            sess["organization_id"] = org or self.org

    def _import(self, raw, *, filename="red_septiembre.csv", snapshot=False):
        batch = preview_remax_catalog(
            self.org,
            raw,
            filename=filename,
            snapshot=snapshot,
        )
        return confirm_remax_catalog(self.org, batch["id"])

    def test_office_import_still_creates_properties(self):
        before_props = count_properties(self.org)
        before_ext = len(list_external_listings(self.org, source=SOURCE_REMAX))
        batch = preview_remax_export(
            self.org,
            OFFICE_CSV.encode("utf-8"),
            agent_id=self.agent_id,
            filename="oficina.csv",
        )
        result = confirm_remax_export(self.org, batch["id"])
        self.assertEqual(result.properties_created, 2)
        self.assertGreater(count_properties(self.org), before_props)
        self.assertEqual(
            len(list_external_listings(self.org, source=SOURCE_REMAX)),
            before_ext,
        )
        office_ids = {
            prop["address"]
            for prop in get_properties(self.org, include_all_statuses=True)
        }
        self.assertTrue(any("Libertador" in item for item in office_ids))

    def test_catalog_creates_external_listings_never_properties(self):
        before_props = count_properties(self.org)
        result = self._import(CATALOG_CSV.encode("utf-8"))
        self.assertGreaterEqual(result["imported"], 2)
        self.assertEqual(count_properties(self.org), before_props)
        listings = list_external_listings(self.org, source=SOURCE_REMAX)
        by_id = {item["external_id"]: item for item in listings}
        self.assertIn("RM-CAT-100", by_id)
        self.assertEqual(by_id["RM-CAT-100"]["source"], SOURCE_REMAX)
        self.assertIsNone(by_id["RM-CAT-100"].get("internal_property_id"))
        self.assertEqual(
            by_id["RM-CAT-100"]["external_url"],
            "https://remax.com.ar/listings/RM-CAT-100",
        )
        self.assertEqual(by_id["RM-CAT-100"]["neighborhood"], "Belgrano")
        self.assertEqual(by_id["RM-CAT-100"]["rooms"], 3)
        self.assertEqual(by_id["RM-CAT-100"]["bedrooms"], 2)
        self.assertEqual(by_id["RM-CAT-100"]["bathrooms"], 1)
        self.assertEqual(by_id["RM-CAT-100"]["covered_m2"], 84)
        self.assertEqual(by_id["RM-CAT-100"]["total_m2"], 90)
        self.assertEqual(by_id["RM-CAT-100"]["parking_spaces"], 1)
        self.assertEqual(by_id["RM-CAT-101"]["external_url"], None)

    def test_preview_counts_and_destination(self):
        unique_csv = CATALOG_CSV.replace("RM-CAT-100", "RM-PREV-100").replace(
            "RM-CAT-101",
            "RM-PREV-101",
        )
        batch = preview_remax_catalog(
            self.org,
            unique_csv.encode("utf-8"),
            filename="red_septiembre.csv",
        )
        preview = batch["preview"]
        self.assertEqual(preview["mode"], "remax_catalog")
        self.assertEqual(preview["filename"], "red_septiembre.csv")
        self.assertEqual(preview["detected"], 2)
        self.assertEqual(preview["created"], 2)
        self.assertEqual(preview["updated"], 0)
        self.assertEqual(preview["invalid"], 0)
        self.assertEqual(preview["destination"], "external_listings")
        self.assertFalse(preview["snapshot"])
        self.assertEqual(preview["import_kind"], "incremental")
        self.assertTrue(preview["can_confirm"])
        confirm_remax_catalog(self.org, batch["id"])

        again = preview_remax_catalog(
            self.org,
            unique_csv.encode("utf-8"),
            filename="red_septiembre.csv",
        )
        self.assertEqual(again["preview"]["created"], 0)
        self.assertEqual(again["preview"]["updated"], 2)

    def test_invalid_rows_do_not_break_import(self):
        before_props = count_properties(self.org)
        batch = preview_remax_catalog(
            self.org,
            INVALID_ROWS_CSV.encode("utf-8"),
            filename="mixed.csv",
        )
        preview = batch["preview"]
        self.assertEqual(preview["detected"], 7)
        self.assertEqual(preview["created"], 1)
        self.assertGreaterEqual(preview["invalid"], 5)
        self.assertTrue(preview["can_confirm"])
        codes = {
            code
            for row in preview["error_rows"]
            for code in row["errors"]
        }
        self.assertIn("mlsid_required", codes)
        self.assertIn("address_required", codes)
        self.assertIn("price_invalid", codes)
        self.assertIn("currency_required", codes)
        self.assertIn("currency_invalid", codes)
        self.assertIn("mlsid_duplicate", codes)
        result = confirm_remax_catalog(self.org, batch["id"])
        self.assertEqual(result["created"], 1)
        self.assertEqual(count_properties(self.org), before_props)
        self.assertIsNotNone(
            get_external_listing_by_source_id(self.org, SOURCE_REMAX, "RM-OK-1")
        )

    def test_upsert_same_mlsid_updates_and_keeps_source(self):
        self._import(CATALOG_CSV.encode("utf-8"))
        first = get_external_listing_by_source_id(
            self.org,
            SOURCE_REMAX,
            "RM-CAT-100",
        )
        first_seen = first["first_seen_at"]
        result = self._import(PARTIAL_CSV.encode("utf-8"))
        self.assertEqual(result["created"], 0)
        self.assertGreaterEqual(result["updated"] + result["unchanged"], 1)
        updated = get_external_listing_by_source_id(
            self.org,
            SOURCE_REMAX,
            "RM-CAT-100",
        )
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["source"], SOURCE_REMAX)
        self.assertEqual(updated["price"], 190000)
        self.assertEqual(updated["first_seen_at"], first_seen)
        self.assertIsNotNone(updated["last_seen_at"])

    def test_normalize_missing_fields_stay_none(self):
        connector = RemaxExportConnector()
        listing = connector.normalize(
            {
                "source": SOURCE_REMAX,
                "external_id": "RM-MIN",
                "address": "Calle Corta 1",
                "price": 100000,
                "currency": "USD",
            }
        )
        self.assertEqual(listing["source"], SOURCE_REMAX)
        self.assertIsNone(listing["external_url"])
        self.assertIsNone(listing["neighborhood"])
        self.assertIsNone(listing["rooms"])
        self.assertIsNone(listing["bedrooms"])
        self.assertEqual(listing["features"], {})
        self.assertEqual(listing["images"], [])

    def test_matcher_internal_plus_remax_catalog(self):
        self._import(CATALOG_CSV.encode("utf-8"))
        connector = RemaxExportConnector()
        search = connector.search({}, organization_id=self.org)
        self.assertEqual(search.status, SEARCH_INDEXED)
        self.assertTrue(search.ok)
        self.assertTrue(
            any(item["external_id"] == "RM-CAT-100" for item in search.listings)
        )
        ranked = rank_contact_properties(
            self.org,
            self.contact,
            agent_id=self.agent_id,
        )
        sources = {item["source"] for item in ranked}
        self.assertIn(SOURCE_REMAX, sources)
        self.assertNotIn(SOURCE_MERCADOLIBRE, sources)
        remax = next(item for item in ranked if item["source"] == SOURCE_REMAX)
        self.assertIsNotNone(remax["external_listing_id"])
        self.assertIsNone(remax.get("internal_property_id"))
        card = decorate_match(remax, language="es")
        self.assertEqual(card["source_label"], "RE/MAX")

    def test_whatsapp_with_and_without_url(self):
        self._import(CATALOG_CSV.encode("utf-8"))
        with_url = listing_from_external_listing(
            get_external_listing_by_source_id(
                self.org,
                SOURCE_REMAX,
                "RM-CAT-100",
            )
        )
        without_url = dict(with_url)
        without_url["external_url"] = None
        with_msg = build_whatsapp_message(
            self.contact,
            [
                decorate_match(
                    match_properties(self.contact, [with_url])[0],
                    language="es",
                )
            ],
            language="es",
        )
        without_msg = build_whatsapp_message(
            self.contact,
            [
                decorate_match(
                    match_properties(self.contact, [without_url])[0],
                    language="es",
                )
            ],
            language="es",
        )
        self.assertIn("https://remax.com.ar/listings/RM-CAT-100", with_msg)
        self.assertIn("Av. Cabildo", without_msg)
        self.assertNotIn("http", without_msg)

    def test_incremental_does_not_deactivate(self):
        self._import(CATALOG_CSV.encode("utf-8"))
        result = self._import(PARTIAL_CSV.encode("utf-8"), snapshot=False)
        self.assertEqual(result["deactivated"], 0)
        self.assertFalse(result["snapshot"])
        still_active = {
            item["external_id"]
            for item in list_active_external_listings(self.org, source=SOURCE_REMAX)
        }
        self.assertIn("RM-CAT-100", still_active)
        self.assertIn("RM-CAT-101", still_active)

    def test_full_snapshot_deactivates_missing(self):
        self._import(CATALOG_CSV.encode("utf-8"))
        result = self._import(PARTIAL_CSV.encode("utf-8"), snapshot=True)
        self.assertTrue(result["snapshot"])
        self.assertGreaterEqual(result["deactivated"], 1)
        missing = get_external_listing_by_source_id(
            self.org,
            SOURCE_REMAX,
            "RM-CAT-101",
        )
        self.assertFalse(missing["is_active"])
        kept = get_external_listing_by_source_id(
            self.org,
            SOURCE_REMAX,
            "RM-CAT-100",
        )
        self.assertTrue(kept["is_active"])

    def test_office_and_catalog_modes_stay_separate(self):
        catalog_batch = preview_remax_catalog(
            self.org,
            CATALOG_CSV.encode("utf-8"),
            filename="cat.csv",
        )
        with self.assertRaises(ValueError):
            confirm_remax_export(self.org, catalog_batch["id"])
        office_batch = preview_remax_export(
            self.org,
            OFFICE_CSV.encode("utf-8"),
            agent_id=self.agent_id,
            filename="off.csv",
        )
        with self.assertRaises(RemaxCatalogError):
            confirm_remax_catalog(self.org, office_batch["id"])

    def test_source_capabilities_feed_ui_copy(self):
        capabilities = listing_source_capabilities()
        self.assertEqual(capabilities[SOURCE_INTERNAL]["search"], "enabled")
        self.assertEqual(capabilities[SOURCE_REMAX]["search"], SEARCH_INDEXED)
        self.assertEqual(capabilities[SOURCE_REMAX]["sync"], "manual_import")
        self.assertEqual(
            capabilities[SOURCE_MERCADOLIBRE]["search"],
            SEARCH_NOT_AUTHORIZED,
        )
        self.assertFalse(capabilities[SOURCE_MERCADOLIBRE]["visible_in_match"])
        self.assertEqual(
            capabilities[SOURCE_ZONAPROP]["search"],
            SEARCH_UNSUPPORTED_SEARCH,
        )
        self.assertEqual(
            capabilities[SOURCE_ARGENPROP]["search"],
            SEARCH_UNSUPPORTED_SEARCH,
        )
        self.assertEqual(match_visible_sources(), [SOURCE_INTERNAL, SOURCE_REMAX])

    def test_organization_isolation(self):
        self._import(CATALOG_CSV.encode("utf-8"))
        self.assertEqual(
            list_external_listings(self.other_org, source=SOURCE_REMAX),
            [],
        )
        batch = preview_remax_catalog(
            self.org,
            PARTIAL_CSV.encode("utf-8"),
            filename="partial.csv",
        )
        with self.assertRaises(RemaxCatalogError):
            confirm_remax_catalog(self.other_org, batch["id"])

    def test_admin_only_http_and_preview(self):
        client = app.test_client()
        denied = client.get("/integrations/remax/catalog")
        self.assertIn(denied.status_code, (302, 401, 403))

        self._login_admin(client)
        page = client.get("/integrations/remax/catalog")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Importar catálogo RE/MAX", body)
        self.assertIn("Integración de búsqueda no habilitada", body)
        self.assertIn("Mercado Libre", body)
        self.assertNotIn("✓ Mercado Libre", body)

        settings = client.get("/settings/organization")
        self.assertEqual(settings.status_code, 200)
        settings_body = settings.get_data(as_text=True)
        self.assertIn("Mercado Libre", settings_body)
        self.assertIn("Integración de búsqueda no habilitada", settings_body)

        upload = client.post(
            "/integrations/remax/catalog",
            data={
                "remax_file": (
                    BytesIO(CATALOG_CSV.encode("utf-8")),
                    "red_septiembre.csv",
                ),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(upload.status_code, 200)
        preview_body = upload.get_data(as_text=True)
        self.assertIn("red_septiembre.csv", preview_body)
        self.assertIn("Registros detectados: 2", preview_body)
        self.assertIn("Catálogo externo para búsqueda", preview_body)
        self.assertIn("NO se agregarán al inventario", preview_body)
        self.assertIn("Importar catálogo", preview_body)

        agent_client = app.test_client()
        with agent_client.session_transaction() as sess:
            sess["user_id"] = add_user(
                "catalog_agent_http",
                hash_password(self.password),
                ROLE_AGENT,
                self.org,
                agent_id=self.agent_id,
                email="catalog_agent_http@example.com",
            )
            sess["role"] = ROLE_AGENT
            sess["organization_id"] = self.org
        forbidden = agent_client.get("/integrations/remax/catalog")
        self.assertIn(forbidden.status_code, (302, 401, 403))


if __name__ == "__main__":
    unittest.main()
