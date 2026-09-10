"""FASE 5F.4 — RedREMAX associate → JRH Agent mapping."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from werkzeug.datastructures import FileStorage

_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_redremax_agent_mapping.db")
os.environ.pop("DATABASE_URL", None)

from modules.acm_service import AcmError, create_acm_for_property
from modules.agent_branding import get_agent_branding
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.agents_repository import get_agent_record
from modules.database.properties_repository import (
    ASSIGNMENT_SOURCE_MANUAL,
    get_properties,
    get_property_record,
    update_property,
)
from modules.database.property_sync_hub_repository import (
    get_external_agent_mapping,
    list_external_agent_mappings,
    upsert_external_agent_mapping,
)
from modules.database.tenant import TenantError
from modules.database.users_repository import get_user_by_id
from modules.property_brochure import generate_property_brochure
from modules.property_sync.agent_mapping import (
    list_detected_external_agents,
    organization_agent_choices,
    save_external_agent_mapping,
    save_external_agent_mappings,
    suggest_jrh_agent,
    unlink_external_agent_mapping,
)
from modules.property_sync.agents import resolve_agent_id, suggest_agents_by_name
from modules.property_sync.demo_import_service import (
    confirm_redremax_json_import,
    preview_redremax_json_import,
)
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX
from modules.property_sync.service import update_redremax_office
from tests.redremax_listing_fixture import SANITIZED_LISTING
from web_app import app

OFFICE = "AR.42.27"
ASSOCIATE = "AR.42.27.301"


def load_fixture(**overrides):
    payload = json.loads(json.dumps(SANITIZED_LISTING))
    payload["office"] = OFFICE
    payload["associate"] = ASSOCIATE
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


def listings_envelope(results):
    rows = list(results)
    return {
        "data": {
            "results": rows,
            "page": 1,
            "pageSize": 20,
            "totalItems": len(rows),
            "totalPages": 1,
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
        if (
            row.get("external_id") == external_id
            and row.get("external_source") == PROVIDER_REDREMAX
        ):
            return row
    return None


class RedRemaxAgentMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(
            TESTING=True,
            SECRET_KEY="redremax-agent-mapping",
            SESSION_COOKIE_SECURE=False,
        )
        create_tables()
        cls.org = add_organization("Mapping Org")
        cls.other_org = add_organization("Mapping Other")
        cls.password_hash = hash_password("Password1")
        cls.jose_id = add_agent("José Barreiro", "Alto", cls.org)
        cls.other_agent_id = add_agent("Ana López", "Alto", cls.org)
        cls.foreign_agent_id = add_agent("Other Org Agent", "Alto", cls.other_org)
        cls.admin = add_user(
            "map_admin",
            cls.password_hash,
            ROLE_ADMIN,
            cls.org,
            email="map.admin@example.com",
        )
        cls.jose_user = add_user(
            "map_jose",
            cls.password_hash,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.jose_id,
            email="jose.barreiro@example.com",
            first_name="José",
            last_name="Barreiro",
        )
        cls.ana_user = add_user(
            "map_ana",
            cls.password_hash,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent_id,
            email="ana.lopez@example.com",
            first_name="Ana",
            last_name="López",
        )
        cls.other_admin = add_user(
            "map_other_admin",
            cls.password_hash,
            ROLE_ADMIN,
            cls.other_org,
            email="other.admin@example.com",
        )
        update_redremax_office(cls.org, OFFICE)
        update_redremax_office(cls.other_org, OFFICE)

    def _login(self, user_id):
        self.assertIsNotNone(get_user_by_id(user_id))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        return client

    def _preview(self, files, organization_id=None):
        return preview_redremax_json_import(
            organization_id or self.org,
            files,
            created_by=self.admin,
        )

    def _import(self, files, organization_id=None):
        preview = self._preview(files, organization_id=organization_id)
        return confirm_redremax_json_import(
            organization_id or self.org, preview["confirm_token"]
        )

    def test_01_external_associate_maps_to_jrh_agent(self):
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, ASSOCIATE, self.jose_id
        )
        agent_id, reason = resolve_agent_id(
            self.org,
            PROVIDER_REDREMAX,
            {"external_agent_id": ASSOCIATE},
        )
        self.assertEqual(agent_id, self.jose_id)
        self.assertEqual(reason, "explicit_mapping")

    def test_02_same_external_id_another_org_isolated(self):
        upsert_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, ASSOCIATE, self.jose_id
        )
        agent_id, reason = resolve_agent_id(
            self.other_org,
            PROVIDER_REDREMAX,
            {"external_agent_id": ASSOCIATE},
        )
        self.assertIsNone(agent_id)
        self.assertIsNone(reason)
        self.assertIsNone(
            get_external_agent_mapping(self.other_org, PROVIDER_REDREMAX, ASSOCIATE)
        )

    def test_03_staff_can_create_mapping(self):
        client = self._login(self.admin)
        response = client.post(
            "/settings/integrations/properties/redremax/agents/map",
            data={f"map__{ASSOCIATE}": str(self.jose_id)},
        )
        self.assertEqual(response.status_code, 302)
        mapped = get_external_agent_mapping(self.org, PROVIDER_REDREMAX, ASSOCIATE)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["agent_id"], self.jose_id)

    def test_04_agent_cannot_create_mapping(self):
        before = list_external_agent_mappings(self.org, PROVIDER_REDREMAX)
        client = self._login(self.jose_user)
        response = client.post(
            "/settings/integrations/properties/redremax/agents/map",
            data={"map__AR.42.27.AGENT": str(self.jose_id)},
        )
        self.assertEqual(response.status_code, 302)
        after = list_external_agent_mappings(self.org, PROVIDER_REDREMAX)
        self.assertEqual(len(after), len(before))
        self.assertIsNone(
            get_external_agent_mapping(self.org, PROVIDER_REDREMAX, "AR.42.27.AGENT")
        )

    def test_05_exact_email_suggestion(self):
        before = list_external_agent_mappings(self.org, PROVIDER_REDREMAX)
        suggestions = suggest_jrh_agent(
            self.org, email="jose.barreiro@example.com"
        )
        self.assertEqual(suggestions[0]["strength"], "email_exact")
        self.assertEqual(suggestions[0]["agent_id"], self.jose_id)
        self.assertEqual(
            list_external_agent_mappings(self.org, PROVIDER_REDREMAX), before
        )

    def test_06_fuzzy_does_not_autoassign(self):
        suggestions = suggest_agents_by_name(self.org, "José")
        self.assertTrue(suggestions)
        agent_id, reason = resolve_agent_id(
            self.org,
            PROVIDER_REDREMAX,
            {"external_agent_id": "AR.42.27.FUZZY", "agent_name": "José Barreiro"},
            allow_email=False,
        )
        self.assertIsNone(agent_id)
        self.assertIsNone(reason)
        visual = suggest_jrh_agent(self.org, name="José")
        self.assertTrue(any(item["strength"] == "fuzzy" for item in visual))
        self.assertIsNone(
            get_external_agent_mapping(self.org, PROVIDER_REDREMAX, "AR.42.27.FUZZY")
        )

    def test_07_existing_properties_repaired_after_mapping(self):
        listing_id = "AR.42.27.1.401"
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.REPAIR")]
                    )
                )
            ]
        )
        row = find_by_external_id(self.org, listing_id)
        self.assertIsNotNone(row)
        self.assertIsNone(row.get("agent_id"))
        property_id = row["id"]
        result = save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.REPAIR", self.jose_id
        )
        self.assertGreaterEqual(result["repaired"], 1)
        repaired = get_property_record(property_id, self.org)
        self.assertEqual(repaired["agent_id"], self.jose_id)
        self.assertEqual(repaired["agent_assignment_source"], PROVIDER_REDREMAX)
        self.assertEqual(repaired["id"], property_id)

    def test_08_future_import_assigns_agent(self):
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.FUTURE", self.jose_id
        )
        listing_id = "AR.42.27.1.402"
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.FUTURE")]
                    )
                )
            ]
        )
        row = find_by_external_id(self.org, listing_id)
        self.assertEqual(row["agent_id"], self.jose_id)
        self.assertEqual(row["agent_assignment_source"], PROVIDER_REDREMAX)

    def test_09_no_property_duplicate(self):
        listing_id = "AR.42.27.1.403"
        payload = listings_envelope(
            [load_fixture(id=listing_id, associate="AR.42.27.DUP")]
        )
        self._import([json_upload(payload)])
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.DUP", self.jose_id
        )
        first = find_by_external_id(self.org, listing_id)
        self._import([json_upload(payload)])
        matches = [
            row
            for row in get_properties(self.org, include_all_statuses=True)
            if row.get("external_id") == listing_id
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], first["id"])
        self.assertEqual(matches[0]["agent_id"], self.jose_id)

    def test_10_manual_assignment_not_silently_overwritten(self):
        listing_id = "AR.42.27.1.404"
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.MANUAL")]
                    )
                )
            ]
        )
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.MANUAL", self.jose_id
        )
        row = find_by_external_id(self.org, listing_id)
        update_property(
            row["id"],
            row["address"],
            row["jurisdiction"],
            self.org,
            agent_id=self.other_agent_id,
        )
        manual = get_property_record(row["id"], self.org)
        self.assertEqual(manual["agent_id"], self.other_agent_id)
        self.assertEqual(manual["agent_assignment_source"], ASSIGNMENT_SOURCE_MANUAL)
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.MANUAL", self.jose_id
        )
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.MANUAL")]
                    )
                )
            ]
        )
        again = get_property_record(row["id"], self.org)
        self.assertEqual(again["agent_id"], self.other_agent_id)
        self.assertEqual(again["agent_assignment_source"], ASSIGNMENT_SOURCE_MANUAL)

    def test_11_brochure_uses_mapped_agent(self):
        listing_id = "AR.42.27.1.405"
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.BROCHURE", self.jose_id
        )
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.BROCHURE")]
                    )
                )
            ]
        )
        row = find_by_external_id(self.org, listing_id)
        result = generate_property_brochure(
            row["id"],
            self.org,
            True,
            get_user_by_id(self.admin),
        )
        self.assertEqual(result["agent"]["agent_id"], self.jose_id)
        self.assertIn("José", result["agent"]["name"])

    def test_12_acm_uses_mapped_agent(self):
        listing_id = "AR.42.27.1.406"
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.ACM", self.jose_id
        )
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.ACM")]
                    )
                )
            ]
        )
        row = find_by_external_id(self.org, listing_id)
        view = create_acm_for_property(
            self.org,
            user=get_user_by_id(self.jose_user),
            property_id=row["id"],
        )
        self.assertEqual(view["acm"]["agent_id"], self.jose_id)
        with self.assertRaises(AcmError) as caught:
            create_acm_for_property(
                self.org,
                user=get_user_by_id(self.ana_user),
                property_id=row["id"],
            )
        self.assertEqual(caught.exception.status_code, 403)

    def test_13_agent_branding_correct(self):
        listing_id = "AR.42.27.1.407"
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.BRAND", self.jose_id
        )
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.BRAND")]
                    )
                )
            ]
        )
        row = find_by_external_id(self.org, listing_id)
        branding = get_agent_branding(
            row["agent_id"], self.org, agent_login_only=True
        )
        self.assertEqual(branding["agent_id"], self.jose_id)
        self.assertEqual(branding["email"], "jose.barreiro@example.com")
        self.assertNotEqual(branding["email"], "map.admin@example.com")

    def test_14_unlink_safe(self):
        listing_id = "AR.42.27.1.408"
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.UNLINK", self.jose_id
        )
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.UNLINK")]
                    )
                )
            ]
        )
        row = find_by_external_id(self.org, listing_id)
        unlinked = unlink_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.UNLINK"
        )
        self.assertIsNotNone(unlinked)
        self.assertIsNone(
            get_external_agent_mapping(self.org, PROVIDER_REDREMAX, "AR.42.27.UNLINK")
        )
        kept = get_property_record(row["id"], self.org)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["agent_id"], self.jose_id)
        self.assertIsNotNone(get_agent_record(self.jose_id, self.org))

    def test_15_change_mapping_safe(self):
        listing_id = "AR.42.27.1.409"
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.CHANGE", self.jose_id
        )
        self._import(
            [
                json_upload(
                    listings_envelope(
                        [load_fixture(id=listing_id, associate="AR.42.27.CHANGE")]
                    )
                )
            ]
        )
        row = find_by_external_id(self.org, listing_id)
        save_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.CHANGE", self.other_agent_id
        )
        changed = get_property_record(row["id"], self.org)
        self.assertEqual(changed["id"], row["id"])
        self.assertEqual(changed["agent_id"], self.other_agent_id)
        mapped = get_external_agent_mapping(
            self.org, PROVIDER_REDREMAX, "AR.42.27.CHANGE"
        )
        self.assertEqual(mapped["agent_id"], self.other_agent_id)

    def test_16_another_org_agent_rejected(self):
        with self.assertRaises(TenantError):
            upsert_external_agent_mapping(
                self.org,
                PROVIDER_REDREMAX,
                "AR.42.27.FOREIGN",
                self.foreign_agent_id,
            )
        self.assertIsNone(
            get_external_agent_mapping(self.org, PROVIDER_REDREMAX, "AR.42.27.FOREIGN")
        )
        client = self._login(self.admin)
        response = client.post(
            "/settings/integrations/properties/redremax/agents/map",
            data={"map__AR.42.27.FOREIGN": str(self.foreign_agent_id)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(
            get_external_agent_mapping(self.org, PROVIDER_REDREMAX, "AR.42.27.FOREIGN")
        )

    def test_17_unique_unmapped_and_ui(self):
        payload = listings_envelope(
            [
                load_fixture(id="AR.42.27.1.410", associate="AR.42.27.SHARED"),
                load_fixture(id="AR.42.27.1.411", associate="AR.42.27.SHARED"),
            ]
        )
        preview = self._preview([json_upload(payload)])
        self.assertEqual(preview["unmapped_agents"], 1)
        self.assertEqual(preview["mapped_agents"], 0)
        confirm_redremax_json_import(self.org, preview["confirm_token"])
        detected = list_detected_external_agents(self.org)
        match = next(
            item
            for item in detected["rows"]
            if item["external_agent_id"] == "AR.42.27.SHARED"
        )
        self.assertGreaterEqual(match["property_count"], 2)
        self.assertTrue(match["example_addresses"])
        self.assertFalse(match["mapped"])
        save_external_agent_mappings(
            self.org, PROVIDER_REDREMAX, [("AR.42.27.SHARED", self.jose_id)]
        )
        after = self._preview([json_upload(payload)])
        self.assertEqual(after["unmapped_agents"], 0)
        self.assertGreaterEqual(after["mapped_agents"], 1)
        client = self._login(self.admin)
        page = client.get("/settings/integrations/properties")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Agentes RedREMAX", html)
        self.assertIn("AR.42.27.SHARED", html)
        self.assertIn("José Barreiro", html)
        choices = organization_agent_choices(self.org, query="jose")
        self.assertTrue(any(item["id"] == self.jose_id for item in choices))
        self.assertFalse(
            any(item["id"] == self.foreign_agent_id for item in choices)
        )
        search = client.get(
            "/settings/integrations/properties/redremax/agents/search?q=jose"
        )
        self.assertEqual(search.status_code, 200)
        payload = search.get_json()
        ids = {item["id"] for item in payload["items"]}
        self.assertIn(self.jose_id, ids)
        self.assertNotIn(self.foreign_agent_id, ids)
