"""
Tests for RE/MAX Red export → sync engine bridge.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_remax_export.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_properties,
    list_property_external_listings,
)
from modules.integrations import (
    confirm_remax_export,
    preview_remax_export,
    resolve_remax_export_preview,
)
from modules.integrations.remax_export import (
    normalize_property_type,
    normalize_status,
    parse_percent,
    parse_remax_export_bytes,
    resolve_jurisdiction,
)
from web_app import app


SAMPLE_CSV = """MLSID,Dirección,Altura,Código postal,Localidad,Status Listing,Tipo de Operación,Tipo de Propiedad,% al Comprador,% al Vendedor,Precio,Tipo de moneda
RM-IDEM-100,Libertador,1000,C1425ABA,Palermo,Activa,Venta,Departamento Estándar,4,3,145000,USD
RM-IDEM-101,Cabildo,200,B1636,Olivos,Reservada,Alquiler,Casa,4,15,2500,USD
RM-IDEM-102,Santa Fe,3000,,La Plata,Negociación,Venta,PH,4.15,3,900000,ARS
"""


class RemaxExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        create_tables()
        cls.org = add_organization("Remax Org")
        cls.other_org = add_organization("Remax Other")
        cls.password = "Password1"
        pwd = hash_password(cls.password)
        cls.admin = add_user(
            "remax_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="remax_admin@example.com",
        )
        cls.agent_id = add_agent(
            "Tomas Pasman",
            "Alto",
            cls.org,
        )
        cls.other_agent = add_agent(
            "Other Agent",
            "Alto",
            cls.other_org,
        )
        add_user(
            "remax_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            email="remax_agent@example.com",
        )

    def test_normalize_status_and_types(self):
        self.assertEqual(normalize_status("Negociación"), "negotiation")
        self.assertEqual(normalize_status("Activa"), "active")
        self.assertEqual(
            normalize_property_type("Departamento SemiPiso"),
            "apartment",
        )
        self.assertEqual(
            normalize_property_type("Casa Dúplex"),
            "house",
        )
        self.assertIsNone(
            normalize_property_type("Espacio raro desconocido")
        )

    def test_parse_percent_decimal_rules(self):
        value, error = parse_percent("4,15")
        self.assertEqual(value, Decimal("4.15"))
        self.assertIsNone(error)

        value, error = parse_percent("415")
        self.assertIsNone(value)
        self.assertEqual(error, "percent_out_of_range")

        value, error = parse_percent("4")
        self.assertEqual(value, Decimal("4"))
        self.assertIsNone(error)

    def test_jurisdiction_rules(self):
        jurisdiction, source = resolve_jurisdiction(
            "Palermo",
            "C1425ABA",
        )
        self.assertEqual(jurisdiction, "CABA")
        self.assertEqual(source, "postal_code")

        jurisdiction, source = resolve_jurisdiction(
            "Olivos",
            "B1636",
        )
        self.assertEqual(jurisdiction, "PBA")

        jurisdiction, source = resolve_jurisdiction(
            "Ciudad desconocida",
            "",
        )
        self.assertIsNone(jurisdiction)
        self.assertEqual(source, "ambiguous")

        jurisdiction, source = resolve_jurisdiction(
            "Palermo",
            "B1636",
        )
        self.assertIsNone(jurisdiction)
        self.assertEqual(source, "conflict")

        jurisdiction, source = resolve_jurisdiction(
            "X",
            "",
            override="CABA",
        )
        self.assertEqual(jurisdiction, "CABA")
        self.assertEqual(source, "override")

    def test_parse_blocks_unknown_type(self):
        text = SAMPLE_CSV.replace(
            "Departamento Estándar",
            "Tipo Inventado XYZ",
        )
        result = parse_remax_export_bytes(
            text.encode("utf-8"),
            filename="tomas.csv",
        )
        self.assertTrue(result.has_blocking_errors)
        blocked = [
            row
            for row in result.rows
            if row.mlsid == "RM-IDEM-100"
        ][0]
        self.assertFalse(blocked.is_valid)

    def test_parse_allows_ars_and_null_url(self):
        result = parse_remax_export_bytes(
            SAMPLE_CSV.encode("utf-8"),
            filename="tomas.csv",
        )
        by_id = {row.mlsid: row for row in result.rows}
        self.assertEqual(by_id["RM-IDEM-102"].currency, "ARS")
        self.assertEqual(by_id["RM-IDEM-102"].price, 900000.0)
        self.assertEqual(
            by_id["RM-IDEM-100"].status,
            "active",
        )
        self.assertEqual(
            by_id["RM-IDEM-102"].status,
            "negotiation",
        )
        self.assertIsNone(by_id["RM-IDEM-100"].url)

    def test_preview_confirm_idempotent_no_deactivate(self):
        batch = preview_remax_export(
            self.org,
            SAMPLE_CSV.encode("utf-8"),
            agent_id=self.agent_id,
            filename="tomas.csv",
        )
        preview = batch["preview"]
        self.assertTrue(preview["can_confirm"])
        self.assertEqual(preview["agent"]["id"], self.agent_id)
        self.assertEqual(preview["summary"]["properties_new"], 3)

        first = confirm_remax_export(self.org, batch["id"])
        self.assertEqual(first.status, "ok")
        self.assertEqual(first.properties_created, 3)
        self.assertEqual(first.listings_created, 3)
        self.assertEqual(first.agents_created, 0)
        self.assertEqual(first.listings_deactivated, 0)

        idem_props = [
            prop
            for prop in get_properties(self.org)
            if any(
                listing.get("external_id", "").startswith(
                    "RM-IDEM-"
                )
                for listing in list_property_external_listings(
                    prop["id"],
                    self.org,
                )
            )
        ]
        self.assertEqual(len(idem_props), 3)

        for prop in idem_props:
            self.assertEqual(prop["agent_id"], self.agent_id)
            listings = list_property_external_listings(
                prop["id"],
                self.org,
            )
            self.assertEqual(len(listings), 1)
            listing = listings[0]
            self.assertEqual(listing["provider"], "remax_web")
            self.assertIsNone(listing["url"])
            self.assertIn(
                listing["listing_currency"],
                ("USD", "ARS"),
            )
            self.assertIsNotNone(
                listing["buyer_side_commission_percent"]
            )

        again = preview_remax_export(
            self.org,
            SAMPLE_CSV.encode("utf-8"),
            agent_id=self.agent_id,
            filename="tomas.csv",
        )
        self.assertEqual(
            again["preview"]["summary"]["properties_new"],
            0,
        )
        self.assertEqual(
            again["preview"]["summary"]["properties_update"],
            3,
        )
        second = confirm_remax_export(self.org, again["id"])
        self.assertEqual(second.properties_created, 0)
        self.assertEqual(second.listings_created, 0)
        self.assertEqual(second.properties_updated, 3)
        self.assertEqual(second.listings_updated, 3)
        self.assertEqual(second.listings_deactivated, 0)

    def test_partial_reimport_does_not_deactivate(self):
        seed = """MLSID,Dirección,Altura,Código postal,Localidad,Status Listing,Tipo de Operación,Tipo de Propiedad,% al Comprador,% al Vendedor,Precio,Tipo de moneda
RM-SEED-1,Libertador,1000,C1425ABA,Palermo,Activa,Venta,Departamento Estándar,4,3,145000,USD
RM-SEED-2,Cabildo,200,B1636,Olivos,Activa,Venta,Casa,4,3,200000,USD
"""
        seed_batch = preview_remax_export(
            self.org,
            seed.encode("utf-8"),
            agent_id=self.agent_id,
            filename="seed.csv",
        )
        confirm_remax_export(self.org, seed_batch["id"])

        partial = """MLSID,Dirección,Altura,Código postal,Localidad,Status Listing,Tipo de Operación,Tipo de Propiedad,% al Comprador,% al Vendedor,Precio,Tipo de moneda
RM-SEED-1,Libertador,1000,C1425ABA,Palermo,Activa,Venta,Departamento Estándar,4,3,150000,USD
"""
        batch = preview_remax_export(
            self.org,
            partial.encode("utf-8"),
            agent_id=self.agent_id,
            filename="partial.csv",
        )
        result = confirm_remax_export(self.org, batch["id"])
        self.assertEqual(result.listings_deactivated, 0)
        self.assertEqual(result.listings_updated, 1)
        self.assertEqual(result.listings_created, 0)

    def test_jurisdiction_override_in_preview(self):
        ambiguous = """MLSID,Dirección,Altura,Localidad,Status Listing,Tipo de Operación,Tipo de Propiedad,Precio,Tipo de moneda
RM-AMB,Calle Falsa,123,Pueblo Raro,Activa,Venta,Casa,100000,USD
"""
        batch = preview_remax_export(
            self.org,
            ambiguous.encode("utf-8"),
            agent_id=self.agent_id,
            filename="amb.csv",
        )
        self.assertFalse(batch["preview"]["can_confirm"])

        updated = resolve_remax_export_preview(
            self.org,
            batch["id"],
            {"RM-AMB": {"jurisdiction": "PBA"}},
        )
        self.assertTrue(updated["preview"]["can_confirm"])
        prop = updated["preview"]["properties"][0]
        self.assertEqual(prop["jurisdiction"], "PBA")

    def test_admin_only_http(self):
        client = app.test_client()
        denied = client.get("/integrations/remax")
        self.assertIn(denied.status_code, (302, 401, 403))

        with client.session_transaction() as sess:
            sess["user_id"] = self.admin
            sess["role"] = ROLE_ADMIN
            sess["organization_id"] = self.org

        ok = client.get("/integrations/remax")
        self.assertEqual(ok.status_code, 200)
        self.assertIn(b"Tomas Pasman", ok.data)

    def test_rejects_other_org_agent(self):
        with self.assertRaises(ValueError):
            preview_remax_export(
                self.org,
                SAMPLE_CSV.encode("utf-8"),
                agent_id=self.other_agent,
                filename="x.csv",
            )


if __name__ == "__main__":
    unittest.main()
