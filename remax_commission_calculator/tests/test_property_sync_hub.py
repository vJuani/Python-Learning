"""FASE 5F — Property Sync Hub. Mock only. No external HTTP."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_property_sync_hub.db")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.users_repository import get_user_by_id
from modules.database.properties_repository import (
    get_properties,
    get_property_record,
    list_match_candidates,
    update_property_status,
)
from modules.database.property_media_repository import list_property_media
from modules.database.property_sync_hub_migration import migrate_property_sync_hub_sqlite
from modules.database.property_sync_hub_repository import (
    PROVIDER_MOCK_NETWORK,
    STATUS_SYNCING,
    finish_integration_state,
    get_property_integration,
    try_begin_sync,
    upsert_external_agent_mapping,
    upsert_property_integration,
)
from modules.property_brochure import generate_property_brochure
from modules.property_sync.agents import resolve_agent_id, suggest_agents_by_name
from modules.property_sync.connector import get_connector
from modules.property_sync.media import get_property_media_for_generation
from modules.property_sync.mock import (
    MockPropertySourceConnector,
    remove_mock_photo,
    reset_mock_catalog,
    set_mock_raise_on_list,
    update_mock_property,
)
from modules.property_sync.normalize import normalize_external_property
from modules.property_sync.security import is_safe_media_url
from modules.property_sync.service import (
    PropertySyncError,
    SyncInProgressError,
    link_external_identity,
    preview_source_conflicts,
    resolve_conflict,
    run_property_sync,
    sync_external_property,
)
from modules.database.property_sync_hub_repository import list_open_conflicts
from web_app import app


class PropertySyncHubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(
            TESTING=True,
            SECRET_KEY="sync-hub-test",
            SESSION_COOKIE_SECURE=False,
        )
        create_tables()
        create_tables()
        cls.org = add_organization("Sync Hub Org")
        cls.other_org = add_organization("Sync Hub Other")
        cls.password_hash = hash_password("Password1")
        cls.agent_id = add_agent("Martín Gómez", "Alto", cls.org)
        cls.other_agent_id = add_agent("Other Agent", "Alto", cls.other_org)
        cls.admin = add_user(
            "sync_admin",
            cls.password_hash,
            ROLE_ADMIN,
            cls.org,
            email="admin.sync@example.com",
        )
        cls.agent_user = add_user(
            "sync_agent",
            cls.password_hash,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            email="martin.mock@example.com",
        )
        cls.other_admin = add_user(
            "sync_other_admin",
            cls.password_hash,
            ROLE_ADMIN,
            cls.other_org,
        )

    def setUp(self):
        reset_mock_catalog()

    def _fresh_org(self):
        return add_organization(f"Sync {self.id()}")

    def _login(self, user_id):
        self.assertIsNotNone(get_user_by_id(user_id))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        return client

    def _by_external(self, organization_id, external_id):
        for row in get_properties(organization_id):
            if row.get("external_id") == external_id and row.get("external_source") == PROVIDER_MOCK_NETWORK:
                return row
        return None

    def test_mock_connector_lists_properties(self):
        connector = get_connector(PROVIDER_MOCK_NETWORK)
        self.assertIsInstance(connector, MockPropertySourceConnector)
        items = connector.list_properties({})
        self.assertGreaterEqual(len(items), 3)
        self.assertTrue(all(item.get("external_id") for item in items))

    def test_first_sync_creates(self):
        org = self._fresh_org()
        result = run_property_sync(org)
        self.assertGreaterEqual(result["created"], 5)
        self.assertEqual(result["updated"], 0)
        italia = self._by_external(org, "MOCK-001")
        self.assertIsNotNone(italia)
        self.assertEqual(italia["listing_price"], 250000)
        self.assertEqual(len(list_property_media(org, italia["id"])), 4)

    def test_second_identical_sync_creates_zero(self):
        org = self._fresh_org()
        run_property_sync(org)
        second = run_property_sync(org)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertGreaterEqual(second["unchanged"], 5)

    def test_changed_price_updates_existing(self):
        org = self._fresh_org()
        run_property_sync(org)
        update_mock_property("MOCK-001", price=245000)
        third = run_property_sync(org)
        self.assertEqual(third["created"], 0)
        self.assertEqual(third["updated"], 1)
        italia = self._by_external(org, "MOCK-001")
        self.assertEqual(italia["listing_price"], 245000)

    def test_same_external_id_different_org_does_not_collide(self):
        org_a = self._fresh_org()
        org_b = self._fresh_org()
        run_property_sync(org_a)
        run_property_sync(org_b)
        first = self._by_external(org_a, "MOCK-001")
        second = self._by_external(org_b, "MOCK-001")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["id"], second["id"])

    def test_manual_property_unaffected(self):
        org = self._fresh_org()
        manual_id = add_property("Manual Only 99", "CABA", org)
        run_property_sync(org)
        manual = get_property_record(manual_id, org)
        self.assertIsNone(manual.get("external_source"))
        self.assertEqual(manual["address"], "Manual Only 99")

    def test_external_deleted_does_not_physical_delete(self):
        org = self._fresh_org()
        run_property_sync(org)
        italia = self._by_external(org, "MOCK-001")
        update_mock_property("MOCK-001", status="deleted", deleted=True)
        run_property_sync(org)
        still = get_property_record(italia["id"], org)
        self.assertIsNotNone(still)
        self.assertEqual(still["commercial_status"], "withdrawn")

    def test_internal_fields_survive_external_update(self):
        org = self._fresh_org()
        run_property_sync(org)
        italia = self._by_external(org, "MOCK-001")
        update_property_status(
            italia["id"],
            org,
            "approved",
            rejection_reason="keep-me",
        )
        update_mock_property("MOCK-001", price=240000)
        run_property_sync(org)
        again = get_property_record(italia["id"], org)
        self.assertEqual(again["rejection_reason"], "keep-me")
        self.assertEqual(again["status"], "approved")
        self.assertEqual(again["listing_price"], 240000)

    def test_media_first_sync_creates_and_second_does_not_duplicate(self):
        org = self._fresh_org()
        run_property_sync(org)
        italia = self._by_external(org, "MOCK-001")
        first = list_property_media(org, italia["id"])
        self.assertEqual(len(first), 4)
        run_property_sync(org)
        second = list_property_media(org, italia["id"])
        self.assertEqual(len(second), 4)
        self.assertEqual({item["external_media_id"] for item in first}, {item["external_media_id"] for item in second})

    def test_removed_media_safely_marked(self):
        org = self._fresh_org()
        run_property_sync(org)
        italia = self._by_external(org, "MOCK-001")
        remove_mock_photo("MOCK-001", "MOCK-001-p3")
        run_property_sync(org)
        active = list_property_media(org, italia["id"])
        all_items = list_property_media(org, italia["id"], include_removed=True)
        self.assertEqual(len(active), 3)
        removed = [item for item in all_items if item["status"] != "active"]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["external_media_id"], "MOCK-001-p3")
        self.assertIsNotNone(get_property_record(italia["id"], org))

    def test_agent_explicit_mapping(self):
        upsert_external_agent_mapping(
            self.org, PROVIDER_MOCK_NETWORK, "mock-agent-1", self.agent_id
        )
        agent_id, reason = resolve_agent_id(
            self.org,
            PROVIDER_MOCK_NETWORK,
            {"external_agent_id": "mock-agent-1"},
        )
        self.assertEqual(agent_id, self.agent_id)
        self.assertEqual(reason, "explicit_mapping")

    def test_agent_exact_email_mapping(self):
        agent_id, reason = resolve_agent_id(
            self.org,
            PROVIDER_MOCK_NETWORK,
            {"email": "martin.mock@example.com"},
        )
        self.assertEqual(agent_id, self.agent_id)
        self.assertEqual(reason, "email")

    def test_fuzzy_name_never_auto_assigns(self):
        suggestions = suggest_agents_by_name(self.org, "Martín")
        self.assertTrue(suggestions)
        agent_id, reason = resolve_agent_id(
            self.org,
            PROVIDER_MOCK_NETWORK,
            {"agent_name": "Martín Gómez"},
            allow_email=False,
        )
        self.assertIsNone(agent_id)
        self.assertIsNone(reason)

    def test_source_conflict_preview_and_link(self):
        org = self._fresh_org()
        agent_id = add_agent("Conflict Agent", "Alto", org)
        manual_id = add_property("Italia 1341", "GBA Norte", org, agent_id=agent_id)
        connector = get_connector(PROVIDER_MOCK_NETWORK)
        raw = connector.get_property({}, "MOCK-001")
        normalized = normalize_external_property(raw, source=PROVIDER_MOCK_NETWORK)
        preview = preview_source_conflicts(org, [normalized], PROVIDER_MOCK_NETWORK)
        self.assertEqual(len(preview), 1)
        result = run_property_sync(org)
        self.assertGreaterEqual(result["conflicts"], 1)
        conflicts = list_open_conflicts(org, PROVIDER_MOCK_NETWORK)
        italia_conflict = next(
            item for item in conflicts if item["external_id"] == "MOCK-001"
        )
        resolve_conflict(org, italia_conflict["id"], "link")
        linked = get_property_record(manual_id, org)
        self.assertEqual(linked["external_id"], "MOCK-001")
        self.assertEqual(linked["external_source"], PROVIDER_MOCK_NETWORK)

    def test_another_org_cannot_link(self):
        org = self._fresh_org()
        run_property_sync(org)
        local = self._by_external(org, "MOCK-002")
        with self.assertRaises(Exception):
            link_external_identity(
                self.other_org,
                local["id"],
                PROVIDER_MOCK_NETWORK,
                "FAKE-999",
            )

    def test_failed_item_does_not_cancel_batch(self):
        from modules.property_sync import mock as mock_mod

        mock_mod.get_mock_catalog().append(
            {
                "external_id": "MOCK-BAD",
                "address": "Falla 1",
                "price": 100,
                "currency": "EUR",
                "status": "available",
            }
        )
        org = self._fresh_org()
        result = run_property_sync(org)
        self.assertGreaterEqual(result["failed"], 1)
        self.assertGreaterEqual(result["created"], 5)
        self.assertIsNotNone(self._by_external(org, "MOCK-001"))

    def test_failed_full_provider_does_not_archive(self):
        org = self._fresh_org()
        run_property_sync(org)
        italia = self._by_external(org, "MOCK-001")
        set_mock_raise_on_list(True)
        result = run_property_sync(org)
        self.assertEqual(result["status"], "failed")
        still = get_property_record(italia["id"], org)
        self.assertEqual(still["commercial_status"], "available")
        set_mock_raise_on_list(False)

    def test_sync_run_stats_correct(self):
        org = self._fresh_org()
        first = run_property_sync(org)
        self.assertEqual(first["created"] + first["unchanged"] + first["updated"], first["created"])
        self.assertEqual(first["failed"], 0)
        second = run_property_sync(org)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["unchanged"], first["created"])

    def test_simultaneous_sync_protected(self):
        org = self._fresh_org()
        upsert_property_integration(org, PROVIDER_MOCK_NETWORK)
        claimed = try_begin_sync(org, PROVIDER_MOCK_NETWORK)
        self.assertIsNotNone(claimed)
        self.assertEqual(get_property_integration(org, PROVIDER_MOCK_NETWORK)["status"], STATUS_SYNCING)
        with self.assertRaises(SyncInProgressError):
            run_property_sync(org)
        finish_integration_state(
            org, PROVIDER_MOCK_NETWORK, status="connected", success=True
        )

    def test_search_includes_synced(self):
        org = self._fresh_org()
        run_property_sync(org)
        rows = get_properties(org)
        self.assertTrue(any(row.get("external_id") == "MOCK-001" for row in rows))

    def test_acm_can_read_synced_property(self):
        org = self._fresh_org()
        run_property_sync(org)
        italia = self._by_external(org, "MOCK-001")
        candidates = list_match_candidates(org)
        self.assertTrue(any(item["id"] == italia["id"] for item in candidates))
        self.assertAlmostEqual(italia["latitude"], -34.4939, places=4)

    def test_brochure_sees_property_media(self):
        org = self._fresh_org()
        admin_id = add_user(
            f"bro_{self.id()}",
            self.password_hash,
            ROLE_ADMIN,
            org,
        )
        run_property_sync(org)
        italia = self._by_external(org, "MOCK-001")
        media = get_property_media_for_generation(italia)
        self.assertGreaterEqual(len(media), 1)
        brochure = generate_property_brochure(
            italia["id"],
            org,
            False,
            {"id": admin_id, "role": ROLE_ADMIN, "organization_id": org},
        )
        self.assertTrue(brochure["pdf_bytes"].startswith(b"%PDF"))
        self.assertIsNotNone(brochure.get("pdf_bytes"))

    def test_maps_lat_lng_preserved(self):
        org = self._fresh_org()
        run_property_sync(org)
        italia = self._by_external(org, "MOCK-001")
        self.assertEqual(italia["location_source"], "external")
        self.assertIsNotNone(italia["latitude"])
        run_property_sync(org)
        again = get_property_record(italia["id"], org)
        self.assertAlmostEqual(again["latitude"], italia["latitude"], places=5)

    def test_migration_idempotent_and_create_tables_twice(self):
        migrate_property_sync_hub_sqlite()
        migrate_property_sync_hub_sqlite()
        create_tables()

    def test_agent_cannot_configure_staff_can(self):
        from modules.property_sync.service import require_sync_admin

        with self.assertRaises(PropertySyncError) as caught:
            require_sync_admin(get_user_by_id(self.agent_user))
        self.assertEqual(caught.exception.status_code, 403)
        require_sync_admin(get_user_by_id(self.admin))
        agent_client = self._login(self.agent_user)
        denied = agent_client.get("/settings/integrations/properties")
        self.assertEqual(denied.status_code, 302)
        self.assertTrue(
            denied.headers.get("Location", "").endswith("/")
            or "dashboard" in denied.headers.get("Location", "")
        )
        post = agent_client.post(
            "/settings/integrations/properties/sync",
            data={"provider": PROVIDER_MOCK_NETWORK},
        )
        self.assertEqual(post.status_code, 302)
        admin_client = self._login(self.admin)
        allowed = admin_client.get("/settings/integrations/properties")
        self.assertEqual(allowed.status_code, 200)
        self.assertIn("Mock Network", allowed.get_data(as_text=True))

    def test_no_external_http_in_unit_paths(self):
        from modules.property_sync import media as media_mod
        from modules.property_sync import mock as mock_mod
        from modules.property_sync import service as service_mod

        for module in (media_mod, mock_mod, service_mod):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("urllib.request", source)
            self.assertNotIn("requests.", source)
            self.assertNotIn("googleapis", source)
        self.assertFalse(is_safe_media_url("http://127.0.0.1/secret"))
        self.assertFalse(is_safe_media_url("https://localhost/photo.jpg"))

    def test_unsafe_url_rejected(self):
        self.assertFalse(is_safe_media_url("https://example.com/x?token=abc"))
        self.assertTrue(is_safe_media_url("https://example.com/stable/photo.jpg"))
