"""FASE 5F.2 — RedREMAX demo JSON import. No live RedREMAX HTTP."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_redremax_demo_import.db")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.contacts_repository import list_contacts
from modules.database.external_price_history_repository import list_external_price_history
from modules.database.properties_repository import get_properties, get_property_record
from modules.database.property_media_repository import list_property_media
from modules.database.property_sync_hub_repository import (
    get_property_integration,
    list_open_conflicts,
    upsert_external_agent_mapping,
)
from modules.database.redremax_demo_import_repository import (
    STATUS_CONFIRMED,
    get_demo_import_by_token,
    session_contains_forbidden_text,
)
from modules.database.users_repository import get_user_by_id
from modules.property_sync.demo_import_service import (
    confirm_redremax_json_import,
    preview_redremax_json_import,
)
from modules.property_sync.redremax.demo_import import (
    INGESTION_METHOD_MANUAL_JSON,
    MAX_JSON_FILE_BYTES,
)
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX
from modules.property_sync.service import (
    PropertySyncError,
    resolve_conflict,
    update_redremax_office,
)
from tests.redremax_listing_fixture import SANITIZED_LISTING
from web_app import app

OFFICE = "AR.42.27"


def load_fixture(**overrides):
    payload = json.loads(json.dumps(SANITIZED_LISTING))
    payload["office"] = OFFICE
    if "id" in overrides:
        street_id = str(overrides["id"]).split(".")[-1]
        address = dict(payload.get("address") or {})
        address["displayAddress"] = f"Calle Ensayo {street_id}"
        address["streetNumber"] = street_id
        payload["address"] = address
        photos = []
        for index, photo in enumerate(payload.get("photos") or []):
            photo = dict(photo)
            photo["cdn"] = (
                f"https://redremax-images.s3.amazonaws.com/test/{street_id}-{index}.jpg"
            )
            photos.append(photo)
        payload["photos"] = photos
    payload.update(overrides)
    return payload


def listings_envelope(results, *, page=1, page_size=20, total_items=None, total_pages=1):
    rows = list(results)
    return {
        "data": {
            "results": rows,
            "page": page,
            "pageSize": page_size,
            "totalItems": total_items if total_items is not None else len(rows),
            "totalPages": total_pages,
        }
    }


def json_upload(payload, name="page-1.json"):
    raw = json.dumps(payload).encode("utf-8")
    return FileStorage(
        stream=io.BytesIO(raw),
        filename=name,
        content_type="application/json",
    )


def find_by_external_id(organization_id, external_id):
    for row in get_properties(organization_id, include_all_statuses=True):
        if row.get("external_id") == external_id and row.get("external_source") == PROVIDER_REDREMAX:
            return row
    return None


def count_rows(table_name, organization_id):
    connection = get_connection()
    try:
        row = connection.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE organization_id = ?",
            (organization_id,),
        ).fetchone()
    except Exception:
        return 0
    finally:
        connection.close()
    return int(row[0] if row else 0)


class RedRemaxDemoImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(
            TESTING=True,
            SECRET_KEY="redremax-demo-import",
            SESSION_COOKIE_SECURE=False,
        )
        create_tables()
        cls.org = add_organization("Demo Import Org")
        cls.other_org = add_organization("Demo Import Other")
        cls.agent_id = add_agent("Agente Demo", "Alto", cls.org)
        cls.password_hash = hash_password("Password1")
        cls.admin = add_user(
            "demo_import_admin",
            cls.password_hash,
            ROLE_ADMIN,
            cls.org,
            email="demo.admin@example.com",
        )
        cls.agent_user = add_user(
            "demo_import_agent",
            cls.password_hash,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            email="demo.agent@example.com",
        )
        update_redremax_office(cls.org, OFFICE)
        update_redremax_office(cls.other_org, OFFICE)

    def _login(self, user_id):
        self.assertIsNotNone(get_user_by_id(user_id))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        return client

    def _preview(self, files, organization_id=None, created_by=None):
        return preview_redremax_json_import(
            organization_id or self.org,
            files,
            created_by=created_by or self.admin,
        )

    def _import(self, files, organization_id=None):
        preview = self._preview(files, organization_id=organization_id)
        return confirm_redremax_json_import(
            organization_id or self.org, preview["confirm_token"]
        )

    def test_01_accepts_redremax_envelope(self):
        fixture = load_fixture(id="AR.42.27.1.101")
        preview = self._preview([json_upload(listings_envelope([fixture]))])
        self.assertEqual(preview["found"], 1)
        self.assertEqual(preview["valid"], 1)
        self.assertFalse(preview["wrote"])

    def test_02_accepts_results_only(self):
        fixture = load_fixture(id="AR.42.27.1.102")
        preview = self._preview([json_upload({"results": [fixture]}, "results.json")])
        self.assertEqual(preview["found"], 1)
        self.assertEqual(preview["valid"], 1)

    def test_03_invalid_json_rejected(self):
        broken = FileStorage(
            stream=io.BytesIO(b"{not-json"),
            filename="broken.json",
            content_type="application/json",
        )
        with self.assertRaises(PropertySyncError) as raised:
            self._preview([broken])
        self.assertEqual(raised.exception.message_key, "redremax_err_import_invalid_json")

    def test_04_wrong_office_skipped(self):
        other = load_fixture(id="AR.99.1.1.1", office="AR.99.1")
        preview = self._preview([json_upload(listings_envelope([other]))])
        self.assertEqual(preview["found"], 1)
        self.assertEqual(preview["valid"], 0)
        self.assertGreaterEqual(preview["warnings"], 1)
        confirm_redremax_json_import(self.org, preview["confirm_token"])
        self.assertIsNone(find_by_external_id(self.org, "AR.99.1.1.1"))

    def test_05_clients_ignored(self):
        before = len(list_contacts(self.org, include_archived=True, limit=500))
        fixture = load_fixture(id="AR.42.27.1.105")
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.105")
        metadata = json.loads(row["external_metadata_json"])
        self.assertNotIn("clients", metadata)
        self.assertEqual(
            len(list_contacts(self.org, include_archived=True, limit=500)),
            before,
        )

    def test_06_clients_data_ignored(self):
        fixture = load_fixture(id="AR.42.27.1.106")
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.106")
        dumped = json.dumps(json.loads(row["external_metadata_json"]))
        self.assertNotIn("clientsData", dumped)
        self.assertNotIn("hidden@example.invalid", dumped)

    def test_07_documents_ignored(self):
        fixture = load_fixture(id="AR.42.27.1.107")
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.107")
        metadata = json.loads(row["external_metadata_json"])
        self.assertNotIn("documents", metadata)
        self.assertEqual(count_rows("property_documents", self.org), 0)

    def test_08_private_notes_ignored(self):
        fixture = load_fixture(id="AR.42.27.1.108")
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.108")
        dumped = json.dumps(row)
        self.assertNotIn("not for JRH", dumped)
        self.assertNotIn("privateNotes", json.dumps(json.loads(row["external_metadata_json"])))

    def test_09_photos_imported(self):
        fixture = load_fixture(id="AR.42.27.1.109")
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.109")
        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 2)
        self.assertTrue(all(item["storage_strategy"] == "remote_reference" for item in media))
        self.assertTrue(all(item.get("storage_key") is None for item in media))

    def test_preview_includes_photo_counts(self):
        fixture = load_fixture(id="AR.42.27.1.130")
        preview = self._preview([json_upload(listings_envelope([fixture]))])
        self.assertEqual(preview["photos_detected"], 2)
        self.assertEqual(preview["photos_valid"], 2)
        self.assertEqual(preview["photos_new"], 2)
        self.assertEqual(preview["photos_existing"], 0)
        self.assertEqual(preview["photos_rejected"], 0)
        result = confirm_redremax_json_import(self.org, preview["confirm_token"])
        self.assertEqual(result["photos_created"], 2)
        second = self._preview([json_upload(listings_envelope([fixture]), "again.json")])
        self.assertEqual(second["photos_existing"], 2)
        self.assertEqual(second["photos_new"], 0)

    def test_10_primary_photo_correct(self):
        fixture = load_fixture(id="AR.42.27.1.110")
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.110")
        media = list_property_media(self.org, row["id"])
        covers = [item for item in media if item.get("is_cover")]
        self.assertEqual(len(covers), 1)
        self.assertIn("110-0.jpg", covers[0]["original_url"])

    def test_11_location_order_correct(self):
        fixture = load_fixture(id="AR.42.27.1.111")
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.111")
        self.assertEqual(row["longitude"], -58.79)
        self.assertEqual(row["latitude"], -34.43)
        self.assertEqual(row["location_source"], "external_redremax")

    def test_12_price_mapped(self):
        fixture = load_fixture(id="AR.42.27.1.112")
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.112")
        self.assertEqual(row["listing_price"], 8000000)
        self.assertEqual(row["listing_currency"], "USD")
        history = list_external_price_history(self.org, row["id"], PROVIDER_REDREMAX)
        self.assertGreaterEqual(len(history), 1)

    def test_13_agent_mapping(self):
        associate = "AR.42.27.MAPPED"
        upsert_external_agent_mapping(self.org, PROVIDER_REDREMAX, associate, self.agent_id)
        fixture = load_fixture(id="AR.42.27.1.113", associate=associate)
        self._import([json_upload(listings_envelope([fixture]))])
        row = find_by_external_id(self.org, "AR.42.27.1.113")
        self.assertEqual(row["agent_id"], self.agent_id)

    def test_14_missing_agent_safe(self):
        fixture = load_fixture(id="AR.42.27.1.114", associate="AR.42.27.UNMAPPED")
        preview = self._preview([json_upload(listings_envelope([fixture]))])
        self.assertEqual(preview["unmapped_agents"], 1)
        confirm_redremax_json_import(self.org, preview["confirm_token"])
        row = find_by_external_id(self.org, "AR.42.27.1.114")
        self.assertIsNotNone(row)
        self.assertIsNone(row.get("agent_id"))

    def test_15_excel_manual_property_not_auto_merged(self):
        add_property("Italia 1341", "CABA", self.org, agent_id=self.agent_id)
        fixture = load_fixture(id="AR.42.27.1.115")
        address = dict(fixture["address"])
        address["displayAddress"] = "Italia 1341"
        fixture["address"] = address
        preview = self._preview([json_upload(listings_envelope([fixture]))])
        self.assertEqual(preview["conflicts"], 1)
        self.assertEqual(preview["created"], 0)
        result = confirm_redremax_json_import(self.org, preview["confirm_token"])
        self.assertEqual(result["conflicts"], 1)
        self.assertIsNone(find_by_external_id(self.org, "AR.42.27.1.115"))
        conflicts = list_open_conflicts(self.org, PROVIDER_REDREMAX)
        match = next(item for item in conflicts if item["external_id"] == "AR.42.27.1.115")
        resolve_conflict(self.org, match["id"], "create")
        created = find_by_external_id(self.org, "AR.42.27.1.115")
        self.assertIsNotNone(created)
        self.assertNotEqual(created["id"], match["existing_property_id"])

    def test_16_dry_run_writes_nothing(self):
        before = count_rows("properties", self.org)
        fixture = load_fixture(id="AR.42.27.1.116")
        preview = self._preview([json_upload(listings_envelope([fixture]))])
        self.assertFalse(preview["wrote"])
        self.assertEqual(count_rows("properties", self.org), before)
        self.assertIsNone(find_by_external_id(self.org, "AR.42.27.1.116"))

    def test_17_confirm_writes(self):
        fixture = load_fixture(id="AR.42.27.1.117")
        preview = self._preview([json_upload(listings_envelope([fixture]))])
        result = confirm_redremax_json_import(self.org, preview["confirm_token"])
        self.assertEqual(result["created"], 1)
        row = find_by_external_id(self.org, "AR.42.27.1.117")
        self.assertEqual(row["external_source"], PROVIDER_REDREMAX)
        metadata = json.loads(row["external_metadata_json"])
        self.assertEqual(metadata["ingestion_method"], INGESTION_METHOD_MANUAL_JSON)

    def test_18_confirm_idempotent(self):
        fixture = load_fixture(id="AR.42.27.1.118")
        preview = self._preview([json_upload(listings_envelope([fixture]))])
        confirm_redremax_json_import(self.org, preview["confirm_token"])
        with self.assertRaises(PropertySyncError) as raised:
            confirm_redremax_json_import(self.org, preview["confirm_token"])
        self.assertEqual(raised.exception.message_key, "redremax_err_import_already")
        self.assertEqual(
            len(
                [
                    row
                    for row in get_properties(self.org, include_all_statuses=True)
                    if row.get("external_id") == "AR.42.27.1.118"
                ]
            ),
            1,
        )

    def test_19_same_json_second_import_unchanged(self):
        fixture = load_fixture(id="AR.42.27.1.119")
        files = [json_upload(listings_envelope([fixture]), "same.json")]
        first = self._import(files)
        self.assertEqual(first["created"], 1)
        second = self._import([json_upload(listings_envelope([fixture]), "same.json")])
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["unchanged"], 1)

    def test_20_absent_listing_not_archived(self):
        first = load_fixture(id="AR.42.27.1.120")
        second = load_fixture(id="AR.42.27.1.220")
        self._import([json_upload(listings_envelope([first, second]))])
        kept = find_by_external_id(self.org, "AR.42.27.1.220")
        status_before = kept["commercial_status"]
        self._import([json_upload(listings_envelope([first]), "only-first.json")])
        still = find_by_external_id(self.org, "AR.42.27.1.220")
        self.assertIsNotNone(still)
        self.assertEqual(still["commercial_status"], status_before)
        self.assertNotEqual(still.get("commercial_status"), "withdrawn")

    def test_21_multi_page_files_deduped(self):
        shared = load_fixture(id="AR.42.27.1.121")
        only_a = load_fixture(id="AR.42.27.1.221")
        only_b = load_fixture(id="AR.42.27.1.321")
        page_one = listings_envelope([shared, only_a], page=1)
        page_two = listings_envelope([shared, only_b], page=2)
        preview = self._preview(
            [
                json_upload(page_one, "page-1.json"),
                json_upload(page_two, "page-2.json"),
            ]
        )
        self.assertEqual(preview["found"], 3)
        self.assertEqual(preview["valid"], 3)
        result = confirm_redremax_json_import(self.org, preview["confirm_token"])
        self.assertEqual(result["created"], 3)
        self.assertIsNotNone(find_by_external_id(self.org, "AR.42.27.1.121"))
        self.assertIsNotNone(find_by_external_id(self.org, "AR.42.27.1.221"))
        self.assertIsNotNone(find_by_external_id(self.org, "AR.42.27.1.321"))

    def test_22_another_org_isolated(self):
        fixture = load_fixture(id="AR.42.27.1.122")
        self._import([json_upload(listings_envelope([fixture]))])
        self.assertIsNotNone(find_by_external_id(self.org, "AR.42.27.1.122"))
        self.assertIsNone(find_by_external_id(self.other_org, "AR.42.27.1.122"))
        preview = self._preview(
            [json_upload(listings_envelope([load_fixture(id="AR.42.27.1.222")]))]
        )
        with self.assertRaises(PropertySyncError):
            confirm_redremax_json_import(self.other_org, preview["confirm_token"])
        self.assertIsNone(find_by_external_id(self.other_org, "AR.42.27.1.222"))

    def test_23_oversized_file_rejected(self):
        fixture = load_fixture(id="AR.42.27.1.123")
        with patch(
            "modules.property_sync.redremax.demo_import.MAX_JSON_FILE_BYTES",
            40,
        ):
            with self.assertRaises(PropertySyncError) as raised:
                self._preview([json_upload(listings_envelope([fixture]), "huge.json")])
        self.assertEqual(raised.exception.message_key, "redremax_err_import_file_too_large")
        self.assertGreater(MAX_JSON_FILE_BYTES, 40)

    def test_24_no_token_header_persisted(self):
        secret_payload = {
            "Authorization": "Bearer eyJsecret.token.value",
            "data": {"results": [load_fixture(id="AR.42.27.1.124")]},
        }
        with self.assertRaises(PropertySyncError) as raised:
            self._preview([json_upload(secret_payload, "with-auth.json")])
        self.assertEqual(raised.exception.message_key, "redremax_err_import_secrets")
        self.assertFalse(
            session_contains_forbidden_text(
                self.org, ["Bearer eyJ", "JSESSIONID", "Authorization"]
            )
        )

        fixture = load_fixture(id="AR.42.27.1.125")
        preview = self._preview([json_upload(listings_envelope([fixture]))])
        confirm_redremax_json_import(self.org, preview["confirm_token"])
        session = get_demo_import_by_token(self.org, preview["confirm_token"])
        self.assertEqual(session["status"], STATUS_CONFIRMED)
        self.assertEqual(session["listings"], [])
        integration = get_property_integration(self.org, PROVIDER_REDREMAX)
        dumped = json.dumps(integration.get("config") or {})
        self.assertNotIn("Bearer", dumped)
        self.assertNotIn("JSESSIONID", dumped)
        self.assertNotIn("eyJsecret", dumped)

    def test_staff_ui_preview_and_demo_copy(self):
        client = self._login(self.admin)
        page = client.get("/settings/integrations/properties")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Modo demo", html)
        self.assertIn("DEMO /", html)
        self.assertIn("json_files", html)
        self.assertIn("oficial", html)
        self.assertNotIn("Sincronizacion automatica", html)
        self.assertNotIn("Sincronización automática", html)
        fixture = load_fixture(id="AR.42.27.1.199")
        response = client.post(
            "/settings/integrations/properties/redremax/import/preview",
            data={"json_files": json_upload(listings_envelope([fixture]), "ui.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(find_by_external_id(self.org, "AR.42.27.1.199"))

    def test_agent_cannot_import(self):
        client = self._login(self.agent_user)
        denied = client.post(
            "/settings/integrations/properties/redremax/import/preview",
            data={"json_files": json_upload(listings_envelope([load_fixture()]), "nope.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(denied.status_code, 302)
