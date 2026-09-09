"""FASE 5F.1 — RedREMAX connector. HTTP is mocked. No real network."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_redremax_connector.db")
os.environ.pop("DATABASE_URL", None)
os.environ["REDREMAX_API_BASE_URL"] = "https://redremax.test.invalid"
os.environ["REDREMAX_ACCESS_TOKEN"] = "test-token-not-real"

from modules.auth import hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.external_price_history_repository import list_external_price_history
from modules.database.properties_repository import get_properties, get_property_record
from modules.database.property_media_repository import list_property_media
from modules.database.property_sync_hub_repository import (
    upsert_external_agent_mapping,
)
from modules.property_sync.connector import get_connector, register_connector
from modules.property_sync.redremax.auth import (
    ConfiguredRedRemaxTokenProvider,
    RedRemaxOfficialAuthProvider,
)
from modules.property_sync.redremax.client import (
    RedRemaxClient,
    TransportResponse,
    default_http_transport,
)
from modules.property_sync.redremax.connector import RedRemaxConnector
from modules.property_sync.redremax.errors import RedRemaxAuthError
from modules.property_sync.redremax.filters import RedRemaxSyncFilterConfig
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX
from modules.property_sync.redremax.normalizer import RedRemaxPropertyNormalizer
from modules.property_sync.redremax.privacy import is_external_price_publicly_usable
from modules.property_sync.service import (
    compute_auth_state,
    dry_run_property_sync,
    ensure_redremax_integration,
    run_property_sync,
    test_property_source_connection,
    update_redremax_office,
)
from tests.redremax_listing_fixture import SANITIZED_LISTING
from web_app import app

TOKEN = "test-token-not-real"
OFFICE = "AR.TEST.27"


def load_fixture(**overrides):
    payload = json.loads(json.dumps(SANITIZED_LISTING))
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


def find_by_external_id(organization_id, external_id):
    for row in get_properties(organization_id, include_all_statuses=True):
        if row.get("external_id") == external_id:
            return row
    return None


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


class FakeTransport:
    def __init__(self, pages=None, status_for=None):
        self.pages = pages or {}
        self.status_for = status_for or {}
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        parsed = urlparse(url)
        page = (parse_qs(parsed.query).get("page") or ["1"])[0]
        status = self.status_for.get(int(page), 200)
        if status == 429:
            return TransportResponse(429, b"", {"retry-after": "1"})
        if status in (401, 403):
            return TransportResponse(status, b'{"error":"denied"}', {})
        if status >= 400:
            return TransportResponse(status, b"nope", {})
        payload = self.pages.get(int(page), listings_envelope([]))
        return TransportResponse(200, json.dumps(payload).encode("utf-8"), {})


class SequenceTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers)})
        return self.responses.pop(0)


def forbid_real_http(*args, **kwargs):
    raise AssertionError("real HTTP is forbidden in RedREMAX tests")


class RedRemaxConnectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="redremax-test", SESSION_COOKIE_SECURE=False)
        create_tables()
        cls.org = add_organization("RedREMAX Org")
        cls.other_org = add_organization("RedREMAX Other")
        cls.agent_id = add_agent("Agente Oficina", "Alto", cls.org)
        add_user("rr-admin", hash_password("Password1"), "admin", cls.org, agent_id=None)
        cls.default_connector = get_connector(PROVIDER_REDREMAX)

    def setUp(self):
        self.logs = []
        self.handler = logging.Handler()
        self.handler.emit = lambda record: self.logs.append(record.getMessage())
        logging.getLogger().addHandler(self.handler)
        self.addCleanup(logging.getLogger().removeHandler, self.handler)
        self.addCleanup(lambda: register_connector(self.default_connector))

    def _install(self, transport, *, filters=None, token=TOKEN):
        client = RedRemaxClient(
            auth_provider=ConfiguredRedRemaxTokenProvider(token),
            transport=transport,
            base_url="https://redremax.test.invalid",
            sleeper=lambda _seconds: None,
        )
        connector = RedRemaxConnector(client=client, filters=filters or RedRemaxSyncFilterConfig())
        register_connector(connector)
        update_redremax_office(self.org, OFFICE)
        return connector

    def _query(self, url):
        return parse_qs(urlparse(url).query)

    def test_first_page_and_mapping_contract(self):
        fixture = load_fixture(
            id="AR.TEST.27.251.203",
            associate="AR.TEST.27.UNMAPPED",
        )
        transport = FakeTransport({1: listings_envelope([fixture], total_items=1)})
        self._install(transport)
        run = run_property_sync(self.org, PROVIDER_REDREMAX)
        self.assertGreaterEqual(run["created"] + run["updated"] + run["unchanged"], 1)
        self.assertEqual(run["stats"]["source_total"], 1)
        self.assertEqual(run["stats"]["pages_fetched"], 1)

        query = self._query(transport.calls[0]["url"])
        self.assertEqual(query.get("byoffice"), [OFFICE])
        self.assertNotIn("agent", query)
        self.assertEqual(query.get("withClients"), ["false"])

        row = find_by_external_id(self.org, "AR.TEST.27.251.203")
        self.assertEqual(row["external_source"], "redremax")
        self.assertEqual(row["external_id"], "AR.TEST.27.251.203")
        self.assertEqual(row["title"], "Propiedad de prueba")
        self.assertEqual(row["description"], "Descripción de prueba")
        self.assertEqual(row["listing_purpose"], "sale")
        self.assertEqual(row["property_type"], "land")
        self.assertEqual(row["listing_price"], 8000000)
        self.assertEqual(row["listing_currency"], "USD")
        self.assertEqual(row["longitude"], -58.79)
        self.assertEqual(row["latitude"], -34.43)
        self.assertEqual(row["location_source"], "external_redremax")
        self.assertEqual(row["total_m2"], 3537)
        self.assertIsNone(row["covered_m2"])
        self.assertEqual(row["bedrooms"], 0)
        self.assertEqual(row["bathrooms"], 0)
        self.assertEqual(row["rooms"], 0)
        self.assertEqual(row["commercial_status"], "available")

        metadata = json.loads(row["external_metadata_json"])
        self.assertEqual(metadata["mlsid"], "MLS-TEST-203")
        self.assertEqual(metadata["associate"], "AR.TEST.27.UNMAPPED")
        self.assertEqual(metadata["associate_qrid"], "QR-TEST-251")
        self.assertEqual(metadata["external_price_exposure"], "private")
        self.assertEqual(metadata["external_feature_ids"], [35, 41])
        self.assertTrue(metadata["has_blueprints"])
        self.assertNotIn("clients", metadata)
        self.assertNotIn("documents", metadata)
        dumped = json.dumps(metadata)
        self.assertNotIn("should-not-store", dumped)
        self.assertNotIn("escritura", dumped)
        self.assertNotIn("not for JRH", dumped)
        self.assertFalse(is_external_price_publicly_usable(row))

        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 2)
        cover = [item for item in media if item["is_cover"]]
        self.assertEqual(len(cover), 1)
        self.assertEqual(
            cover[0]["original_url"],
            "https://redremax-images.s3.amazonaws.com/test/203-0.jpg",
        )
        for item in media:
            self.assertNotIn("X-Amz-", item.get("original_url") or "")

        history = list_external_price_history(self.org, row["id"], "redremax")
        self.assertEqual(len(history), 1)
        self.assertIsNone(row["agent_id"])
        self.assertGreaterEqual(run["warnings"], 1)

    def test_pagination_all_pages(self):
        first = load_fixture(id="AR.TEST.27.251.page1")
        second = load_fixture(id="AR.TEST.27.251.page2", title="Segunda")
        transport = FakeTransport(
            {
                1: listings_envelope([first], page=1, total_items=2, total_pages=2),
                2: listings_envelope([second], page=2, total_items=2, total_pages=2),
            }
        )
        self._install(transport)
        run = run_property_sync(self.org, PROVIDER_REDREMAX)
        self.assertEqual(run["created"], 2)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(run["stats"]["pages_fetched"], 2)

    def test_agent_filter_only_when_explicit(self):
        transport = FakeTransport({1: listings_envelope([])})
        connector = self._install(transport)
        connector.fetch_office_listings(OFFICE)
        self.assertNotIn("agent", self._query(transport.calls[0]["url"]))
        connector.fetch_office_listings(OFFICE, agent="AR.TEST.27.251")
        self.assertEqual(self._query(transport.calls[1]["url"]).get("agent"), ["AR.TEST.27.251"])

    def test_unknown_property_type_fallback(self):
        payload = load_fixture(propertyType="Tipo inventado")
        hub, warnings = RedRemaxPropertyNormalizer().normalize(payload, expected_office_id=OFFICE)
        self.assertEqual(hub["property_type"], "other")
        self.assertIn("redremax_warn_unknown_property_type", warnings)
        self.assertEqual(hub["external_property_type"], "Tipo inventado")

    def test_rooms_list_is_not_counted(self):
        payload = load_fixture(totalRooms=4, rooms=[{}, {}, {}])
        hub, _warnings = RedRemaxPropertyNormalizer().normalize(payload, expected_office_id=OFFICE)
        self.assertEqual(hub["rooms"], 4)

    def test_price_history_idempotent_and_price_update(self):
        fixture = load_fixture(id="AR.TEST.27.251.hist")
        transport = FakeTransport({1: listings_envelope([fixture], total_items=1)})
        self._install(transport)
        run_property_sync(self.org, PROVIDER_REDREMAX)
        run_property_sync(self.org, PROVIDER_REDREMAX)
        row = find_by_external_id(self.org, "AR.TEST.27.251.hist")
        self.assertEqual(len(list_external_price_history(self.org, row["id"], "redremax")), 1)
        self.assertEqual(len(list_property_media(self.org, row["id"])), 2)

        changed = dict(fixture)
        changed["price"] = {"value": 9000000, "currency": "USD", "exposure": "private"}
        transport.pages[1] = listings_envelope([changed], total_items=1)
        run = run_property_sync(self.org, PROVIDER_REDREMAX)
        self.assertGreaterEqual(run["updated"], 1)
        self.assertEqual(get_property_record(row["id"], self.org)["listing_price"], 9000000)

    def test_other_office_rejected(self):
        payload = load_fixture(id="AR.OTHER.99.1", office="AR.OTHER.99")
        transport = FakeTransport({1: listings_envelope([payload], total_items=1)})
        self._install(transport)
        before = len(get_properties(self.org))
        run = run_property_sync(self.org, PROVIDER_REDREMAX)
        self.assertEqual(len(get_properties(self.org)), before)
        self.assertGreaterEqual(run["warnings"], 1)

    def test_no_fuzzy_agent_assignment(self):
        payload = load_fixture(id="AR.TEST.27.251.fuzzy", associate="AR.TEST.27.999")
        transport = FakeTransport({1: listings_envelope([payload], total_items=1)})
        self._install(transport)
        run_property_sync(self.org, PROVIDER_REDREMAX)
        row = find_by_external_id(self.org, payload["id"])
        self.assertIsNone(row["agent_id"])

    def test_explicit_agent_mapping(self):
        upsert_external_agent_mapping(
            self.org, "redremax", "AR.TEST.27.251", self.agent_id
        )
        mapped = load_fixture(id="AR.TEST.27.251.mapped")
        transport = FakeTransport({1: listings_envelope([mapped], total_items=1)})
        self._install(transport)
        run_property_sync(self.org, PROVIDER_REDREMAX)
        row = find_by_external_id(self.org, "AR.TEST.27.251.mapped")
        self.assertEqual(row["agent_id"], self.agent_id)

    def test_auth_failures_do_not_archive(self):
        fixture = load_fixture(id="AR.TEST.27.251.auth")
        transport = FakeTransport({1: listings_envelope([fixture], total_items=1)})
        self._install(transport)
        run_property_sync(self.org, PROVIDER_REDREMAX)
        row = find_by_external_id(self.org, "AR.TEST.27.251.auth")
        self.assertEqual(row["commercial_status"], "available")

        for status in (401, 403):
            failing = FakeTransport(status_for={1: status})
            self._install(failing)
            run = run_property_sync(self.org, PROVIDER_REDREMAX)
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_summary"], "redremax_err_auth")
            current = get_property_record(row["id"], self.org)
            self.assertEqual(current["commercial_status"], "available")
            self.assertEqual(current["external_id"], row["external_id"])

    def test_429_retries_then_succeeds(self):
        slept = []
        payload = listings_envelope([load_fixture(id="AR.TEST.27.251.rate")], total_items=1)
        transport = SequenceTransport(
            [
                TransportResponse(429, b"", {"retry-after": "2"}),
                TransportResponse(200, json.dumps(payload).encode("utf-8"), {}),
            ]
        )
        client = RedRemaxClient(
            auth_provider=ConfiguredRedRemaxTokenProvider(TOKEN),
            transport=transport,
            base_url="https://redremax.test.invalid",
            sleeper=slept.append,
        )
        page = client.get_listings(office_id=OFFICE, page=1, page_size=1)
        self.assertEqual(len(page["results"]), 1)
        self.assertEqual(slept, [2])

    def test_partial_pagination_does_not_archive(self):
        transport = FakeTransport({1: listings_envelope([load_fixture(id="AR.TEST.27.251.keep")], total_items=1)})
        self._install(transport)
        run_property_sync(self.org, PROVIDER_REDREMAX)
        existing = find_by_external_id(self.org, "AR.TEST.27.251.keep")

        first = load_fixture(id="AR.TEST.27.251.300", title="Pagina uno")
        mixed = FakeTransport(
            {
                1: listings_envelope([first], page=1, total_items=2, total_pages=2),
            },
            status_for={2: 500},
        )
        self._install(mixed)
        run = run_property_sync(self.org, PROVIDER_REDREMAX)
        self.assertEqual(run["status"], "partial")
        still = get_property_record(existing["id"], self.org)
        self.assertEqual(still["commercial_status"], "available")

    def test_organization_isolation_and_manual_untouched(self):
        manual_id = add_property(
            "Manual Excel 999",
            "CABA",
            self.org,
            property_type="house",
            listing_purpose="sale",
            listing_price=100,
            listing_currency="USD",
        )
        other_id = add_property(
            "Otra org",
            "CABA",
            self.other_org,
            property_type="house",
            listing_purpose="sale",
        )
        transport = FakeTransport({1: listings_envelope([load_fixture(id="AR.TEST.27.251.iso")], total_items=1)})
        self._install(transport)
        run_property_sync(self.org, PROVIDER_REDREMAX)
        self.assertIsNone(get_property_record(manual_id, self.org).get("external_source"))
        self.assertIsNone(get_property_record(other_id, self.other_org).get("external_id"))
        self.assertGreaterEqual(run_property_sync(self.org, PROVIDER_REDREMAX)["unchanged"], 0)

    def test_dry_run_writes_nothing(self):
        before = len(get_properties(self.org))
        transport = FakeTransport({1: listings_envelope([load_fixture(id="AR.TEST.27.251.dry")], total_items=1)})
        self._install(transport)
        summary = dry_run_property_sync(self.org, PROVIDER_REDREMAX)
        self.assertEqual(summary["found"], 1)
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["unmapped_agents"], 1)
        self.assertFalse(summary["wrote"])
        self.assertEqual(len(get_properties(self.org)), before)

    def test_test_connection_and_auth_states(self):
        transport = FakeTransport({1: listings_envelope([load_fixture()], total_items=7)})
        self._install(transport)
        result = test_property_source_connection(self.org, PROVIDER_REDREMAX)
        self.assertTrue(result["connected"])
        self.assertEqual(result["office_id"], OFFICE)
        self.assertEqual(result["source_total_items"], 7)
        self.assertNotIn("token", json.dumps(result))
        integration = ensure_redremax_integration(self.org)
        integration["status"] = "connected"
        integration["config"] = {"external_office_id": OFFICE, "last_connection_ok": True}
        self.assertEqual(compute_auth_state(integration), "connected")

        pending = {
            "provider": "redremax",
            "status": "disconnected",
            "config": {"external_office_id": OFFICE},
            "last_error": None,
        }
        self.assertEqual(compute_auth_state(pending), "authentication_pending")

    def test_no_token_in_logs_or_errors_and_no_real_http(self):
        original = default_http_transport
        import modules.property_sync.redremax.client as client_mod

        client_mod.default_http_transport = forbid_real_http
        self.addCleanup(lambda: setattr(client_mod, "default_http_transport", original))

        transport = FakeTransport(status_for={1: 401})
        self._install(transport)
        with self.assertRaises(RedRemaxAuthError):
            get_connector(PROVIDER_REDREMAX).test_connection(
                {"config": {"external_office_id": OFFICE}}
            )
        blob = " ".join(self.logs)
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("Bearer", blob)

    def test_official_auth_is_not_implemented(self):
        provider = RedRemaxOfficialAuthProvider()
        self.assertFalse(provider.is_configured())
        self.assertFalse(provider.is_production_ready())
        with self.assertRaises(NotImplementedError):
            provider.get_access_token()

    def test_configured_token_disabled_when_deployed(self):
        from modules.property_sync.redremax import auth as auth_mod

        original = auth_mod.is_deployed
        auth_mod.is_deployed = lambda: True
        self.addCleanup(lambda: setattr(auth_mod, "is_deployed", original))
        provider = ConfiguredRedRemaxTokenProvider(TOKEN)
        self.assertFalse(provider.is_configured())
        self.assertIsNone(provider.get_access_token())

    def test_sensitive_payload_stripped_by_normalizer(self):
        payload = load_fixture()
        hub, _warnings = RedRemaxPropertyNormalizer().normalize(payload, expected_office_id=OFFICE)
        encoded = json.dumps(hub)
        self.assertNotIn("should-not-store", encoded)
        self.assertNotIn("hidden@example.invalid", encoded)
        self.assertNotIn("escritura", encoded)
        self.assertNotIn("not for JRH", encoded)
        self.assertTrue(all("X-Amz-" not in (item.get("original_url") or "") for item in hub["media"]))


if __name__ == "__main__":
    unittest.main()
