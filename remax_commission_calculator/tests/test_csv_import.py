"""
Tests for CSV import → sync engine bridge.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_csv_import.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agents,
    get_properties,
    list_property_external_listings,
)
from modules.database.agents_repository import (
    find_agent_by_external_id,
)
from modules.database.property_external_listings_repository import (
    create_property_external_listing,
)
from modules.database.properties_repository import (
    add_property,
)
from modules.integrations import (
    cancel_csv_upload,
    confirm_csv_upload,
    preview_csv_upload,
)
from modules.integrations.csv_import import (
    parse_csv_text,
)
from web_app import app


SAMPLE_CSV = """agent_external_id,agent_name,agent_email,property_external_id,address,jurisdiction,url,status,price,currency,property_type,listing_purpose,listing_provider
CSV-A1,Nieves Achard,nieves@example.com,CSV-P1,Libertador 1000,CABA,https://www.remax.com.ar/listings/csv-p1,active,120000,USD,apartment,sale,remax_web
CSV-A1,Nieves Achard,,CSV-P2,Cabildo 200,CABA,https://www.remax.com.ar/listings/csv-p2,active,2500,USD,house,rental,remax_web
CSV-A2,Juan Perez,,CSV-P3,Santa Fe 3000,PBA,https://www.remax.com.ar/listings/csv-p3,active,95000,USD,apartment,sale,remax_web
"""


class CsvImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        create_tables()
        cls.org = add_organization("CSV Org")
        cls.other_org = add_organization("CSV Other Org")
        cls.password = "Password1"
        pwd = hash_password(cls.password)
        cls.admin = add_user(
            "csv_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="csv_admin@example.com",
        )
        agent = add_agent("CSV Local Agent", "Alto", cls.org)
        cls.agent_user_id = add_user(
            "csv_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=agent,
            email="csv_agent@example.com",
        )

    def test_parse_requires_jurisdiction(self):
        text = """agent_external_id,agent_name,property_external_id,address,jurisdiction,url
A1,Name,P1,Addr,,https://example.com/x
"""
        result = parse_csv_text(text)
        self.assertTrue(result.has_blocking_errors)
        self.assertEqual(len(result.rows), 0)

    def test_parse_rejects_ars_currency(self):
        text = SAMPLE_CSV.replace(",USD,", ",ARS,")
        result = parse_csv_text(text)
        self.assertTrue(result.has_blocking_errors)

    def test_preview_and_confirm_idempotent(self):
        batch = preview_csv_upload(
            self.org,
            SAMPLE_CSV.encode("utf-8"),
            filename="office.csv",
        )
        preview = batch["preview"]
        self.assertTrue(preview["can_confirm"])
        self.assertEqual(preview["integration_provider"], "csv_upload")
        self.assertIn("remax_web", preview["listing_providers"])
        self.assertEqual(preview["summary"]["agents_new"], 2)
        self.assertEqual(preview["summary"]["properties_new"], 3)

        first = confirm_csv_upload(self.org, batch["id"])
        self.assertEqual(first.status, "ok")
        self.assertEqual(first.agents_created, 2)
        self.assertEqual(first.properties_created, 3)
        self.assertEqual(first.listings_created, 3)
        self.assertEqual(first.listings_deactivated, 0)

        agents = [
            agent
            for agent in get_agents(self.org)
            if agent.get("external_provider") == "csv_upload"
        ]
        self.assertEqual(len(agents), 2)

        nieves = find_agent_by_external_id(
            self.org,
            "csv_upload",
            "CSV-A1",
        )
        self.assertEqual(nieves["name"], "Nieves Achard")

        properties = get_properties(
            self.org,
            include_all_statuses=True,
        )
        self.assertEqual(len(properties), 3)

        for prop in properties:
            self.assertEqual(prop["status"], "approved")
            listings = list_property_external_listings(
                prop["id"],
                self.org,
            )
            self.assertEqual(len(listings), 1)
            self.assertEqual(listings[0]["provider"], "remax_web")

        second_batch = preview_csv_upload(
            self.org,
            SAMPLE_CSV.encode("utf-8"),
            filename="office.csv",
        )
        self.assertEqual(
            second_batch["preview"]["summary"]["agents_new"],
            0,
        )
        self.assertEqual(
            second_batch["preview"]["summary"]["agents_update"],
            2,
        )
        self.assertEqual(
            second_batch["preview"]["summary"]["properties_new"],
            0,
        )

        second = confirm_csv_upload(
            self.org,
            second_batch["id"],
        )
        self.assertEqual(second.agents_created, 0)
        self.assertEqual(second.properties_created, 0)
        self.assertEqual(second.listings_created, 0)
        self.assertEqual(second.agents_updated, 2)
        self.assertEqual(second.properties_updated, 3)
        self.assertEqual(second.listings_updated, 3)
        self.assertEqual(second.listings_deactivated, 0)

        # Partial CSV must not deactivate missing listings
        partial = """agent_external_id,agent_name,property_external_id,address,jurisdiction,url,price,currency,listing_provider
CSV-A1,Nieves Achard,CSV-P1,Libertador 1000,CABA,https://www.remax.com.ar/listings/csv-p1,121000,USD,remax_web
"""
        partial_batch = preview_csv_upload(
            self.org,
            partial.encode("utf-8"),
            filename="partial.csv",
        )
        partial_result = confirm_csv_upload(
            self.org,
            partial_batch["id"],
        )
        self.assertEqual(partial_result.listings_deactivated, 0)
        self.assertEqual(
            len(
                get_properties(
                    self.org,
                    include_all_statuses=True,
                )
            ),
            3,
        )

    def test_cancel_does_not_write(self):
        org = add_organization("CSV Cancel Org")
        before = len(
            get_properties(org, include_all_statuses=True)
        )
        batch = preview_csv_upload(
            org,
            SAMPLE_CSV.encode("utf-8"),
            filename="cancel.csv",
        )
        self.assertTrue(cancel_csv_upload(org, batch["id"]))
        after = len(
            get_properties(org, include_all_statuses=True)
        )
        self.assertEqual(before, after)
        with self.assertRaises(ValueError):
            confirm_csv_upload(org, batch["id"])

    def test_tenant_isolation_on_batch(self):
        batch = preview_csv_upload(
            self.org,
            SAMPLE_CSV.encode("utf-8"),
            filename="tenant.csv",
        )
        from modules.database.csv_import_batches_repository import (
            get_csv_import_batch,
        )

        self.assertIsNone(
            get_csv_import_batch(
                batch["id"],
                self.other_org,
            )
        )

    def test_partial_reimport_does_not_touch_unrelated_listings(
        self,
    ):
        org = add_organization("CSV Stale Safe")
        batch = preview_csv_upload(
            org,
            SAMPLE_CSV.encode("utf-8"),
            filename="base.csv",
        )
        confirm_csv_upload(org, batch["id"])

        agent = find_agent_by_external_id(
            org,
            "csv_upload",
            "CSV-A1",
        )
        stale = add_property(
            "Stale",
            "CABA",
            org,
            agent_id=agent["id"],
            status="approved",
        )
        create_property_external_listing(
            org,
            stale,
            "remax_web",
            "https://www.remax.com.ar/listings/stale",
            "active",
            external_id="STALE-KEEP",
        )

        again = preview_csv_upload(
            org,
            SAMPLE_CSV.encode("utf-8"),
            filename="again.csv",
        )
        result = confirm_csv_upload(org, again["id"])
        self.assertEqual(result.listings_deactivated, 0)

        listings = list_property_external_listings(
            stale,
            org,
        )
        self.assertEqual(listings[0]["status"], "active")

    def test_admin_only_http(self):
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["user_id"] = self.agent_user_id

        denied = client.get("/integrations/csv")
        # app errorhandler(403) redirects to dashboard
        self.assertEqual(denied.status_code, 302)
        self.assertTrue(
            denied.headers.get("Location", "").endswith("/")
            or "dashboard" in denied.headers.get("Location", "")
        )

        with client.session_transaction() as sess:
            sess["user_id"] = self.admin

        ok = client.get("/integrations/csv")
        self.assertEqual(ok.status_code, 200)


if __name__ == "__main__":
    unittest.main()
