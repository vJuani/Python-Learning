"""FASE 5F.3 — RedREMAX property photos. No live HTTP except mocked brochure fetch."""

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
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_property_photos.db")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_ADMIN, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.properties_repository import get_properties
from modules.database.property_media_repository import (
    STRATEGY_COPY,
    STRATEGY_REMOTE,
    list_property_media,
    upsert_property_media,
)
from modules.database.users_repository import get_user_by_id
from modules.property_brochure import generate_property_brochure
from modules.property_sync.demo_import_service import (
    confirm_redremax_json_import,
    preview_redremax_json_import,
)
from modules.property_sync.media import (
    csp_allows_redremax_images,
    describe_property_media,
    get_property_cover_media,
    get_property_media_for_generation,
    get_property_media_url,
    media_display_src,
)
from modules.property_sync.public_listing import PublicListingMediaProvider
from modules.property_sync.redremax.normalizer import RedRemaxPropertyNormalizer
from modules.property_sync.redremax.photos import (
    BETA_PHOTO_LIMIT,
    inspect_photos,
    map_photos,
    resolve_photo_source_url,
)
from modules.property_sync.service import update_redremax_office
from tests.redremax_listing_fixture import SANITIZED_LISTING
from web_app import app

OFFICE = "AR.42.27"


def signed_photo(index, *, primary=False):
    path = f"https://redremax-images.s3.amazonaws.com/listings/photo-{index}.jpg"
    query = (
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=AKIAEXAMPLE"
        "&X-Amz-Signature=deadbeef"
        "&X-Amz-Expires=3600"
    )
    return {
        "cdn": path + query,
        "cloudfront": None,
        "prefix": path + query,
        "primary": primary,
    }


def photo(index, *, primary=False, host="redremax-images.s3.amazonaws.com", scheme="https"):
    return {
        "cdn": f"{scheme}://{host}/listings/photo-{index}.jpg",
        "primary": primary,
    }


def load_listing(*, photo_count=10, primary_index=0, **overrides):
    payload = json.loads(json.dumps(SANITIZED_LISTING))
    payload["office"] = OFFICE
    listing_id = str(overrides.get("id") or payload.get("id") or "listing")
    street = listing_id.rsplit(".", 1)[-1]
    address = dict(payload.get("address") or {})
    address["displayAddress"] = f"Italia Photo {street}"
    address["streetNumber"] = street
    payload["address"] = address
    payload["photos"] = [
        photo(index, primary=(index == primary_index))
        for index in range(photo_count)
    ]
    payload.update(overrides)
    return payload


def listings_envelope(results):
    return {"data": {"results": list(results), "page": 1, "pageSize": 20, "totalItems": len(results), "totalPages": 1}}


def json_upload(payload, name="page-1.json"):
    return FileStorage(
        stream=io.BytesIO(json.dumps(payload).encode("utf-8")),
        filename=name,
        content_type="application/json",
    )


def find_by_external(organization_id, external_id):
    for row in get_properties(organization_id, include_all_statuses=True):
        if row.get("external_id") == external_id:
            return row
    return None


class PropertyPhotoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="photos-test", SESSION_COOKIE_SECURE=False)
        create_tables()
        cls.org = add_organization("Photos Org")
        cls.other_org = add_organization("Photos Other")
        cls.agent_id = add_agent("Foto Agente", "Alto", cls.org)
        cls.admin = add_user(
            "photos_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            cls.org,
            email="photos.admin@example.com",
        )
        update_redremax_office(cls.org, OFFICE)
        update_redremax_office(cls.other_org, OFFICE)

    def _login(self):
        self.assertIsNotNone(get_user_by_id(self.admin))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.admin
        return client

    def _import(self, listing, organization_id=None):
        org = organization_id or self.org
        preview = preview_redremax_json_import(
            org,
            [json_upload(listings_envelope([listing]))],
            created_by=self.admin,
        )
        confirm_redremax_json_import(org, preview["confirm_token"])
        return find_by_external(org, listing["id"])

    def test_01_max_five_photos_from_ten(self):
        listing = load_listing(id="AR.42.27.1.301", photo_count=10, primary_index=0)
        mapped = map_photos(listing["photos"])
        self.assertEqual(len(mapped), BETA_PHOTO_LIMIT)
        row = self._import(listing)
        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 5)

    def test_02_primary_included_when_not_first(self):
        listing = load_listing(id="AR.42.27.1.302", photo_count=10, primary_index=3)
        mapped = map_photos(listing["photos"])
        self.assertEqual(len(mapped), 5)
        covers = [item for item in mapped if item["is_cover"]]
        self.assertEqual(len(covers), 1)
        self.assertIn("photo-3.jpg", covers[0]["original_url"])

    def test_03_primary_becomes_cover(self):
        listing = load_listing(id="AR.42.27.1.303", photo_count=6, primary_index=2)
        row = self._import(listing)
        cover = get_property_cover_media(row)
        self.assertIn("photo-2.jpg", cover["original_url"])
        self.assertTrue(cover["is_cover"])

    def test_04_no_primary_uses_first_visual_fallback(self):
        listing = load_listing(id="AR.42.27.1.304", photo_count=4, primary_index=-1)
        row = self._import(listing)
        cover = get_property_cover_media(row)
        self.assertIsNotNone(cover)
        self.assertFalse(cover["is_cover"])
        self.assertIn("photo-0.jpg", cover["original_url"])

    def test_05_second_sync_does_not_duplicate(self):
        listing = load_listing(id="AR.42.27.1.305", photo_count=7, primary_index=0)
        row = self._import(listing)
        first = list_property_media(self.org, row["id"])
        self._import(listing)
        second = list_property_media(self.org, row["id"])
        self.assertEqual(len(first), 5)
        self.assertEqual(len(second), 5)
        self.assertEqual(
            {item["external_media_id"] for item in first},
            {item["external_media_id"] for item in second},
        )

    def test_06_order_change_does_not_duplicate(self):
        listing = load_listing(id="AR.42.27.1.306", photo_count=6, primary_index=0)
        row = self._import(listing)
        listing["photos"] = list(reversed(listing["photos"]))
        listing["photos"][0]["primary"] = False
        listing["photos"][-1]["primary"] = True
        self._import(listing)
        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 5)

    def test_07_another_org_isolated(self):
        listing = load_listing(id="AR.42.27.1.307", photo_count=3, primary_index=0)
        row = self._import(listing)
        self.assertTrue(list_property_media(self.org, row["id"]))
        self.assertEqual(list_property_media(self.other_org, row["id"]), [])

    def test_08_documents_not_imported(self):
        mapped = map_photos(load_listing()["photos"])
        self.assertTrue(all("documents" not in item for item in mapped))
        listing = load_listing(id="AR.42.27.1.308", photo_count=2)
        row = self._import(listing)
        dumped = json.dumps(list_property_media(self.org, row["id"]))
        self.assertNotIn("escritura", dumped)

    def test_09_blueprints_not_imported(self):
        listing = load_listing(id="AR.42.27.1.309", photo_count=2)
        hub, warnings = RedRemaxPropertyNormalizer().normalize(
            listing, expected_office_id=OFFICE
        )
        self.assertTrue(any("blueprint" in item for item in warnings))
        self.assertTrue(all("X-Amz-" not in (item.get("original_url") or "") for item in hub["media"]))

    def test_10_clients_data_ignored(self):
        listing = load_listing(id="AR.42.27.1.310", photo_count=2)
        row = self._import(listing)
        dumped = json.dumps(list_property_media(self.org, row["id"]))
        self.assertNotIn("hidden@example.invalid", dumped)

    def test_11_invalid_host_rejected(self):
        mapped = map_photos([photo(1, host="evil.example")])
        self.assertEqual(mapped, [])

    def test_12_http_url_rejected(self):
        mapped = map_photos([photo(1, scheme="http")])
        self.assertEqual(mapped, [])

    def test_13_broken_remote_image_has_fallback_hook(self):
        listing = load_listing(id="AR.42.27.1.313", photo_count=2, primary_index=0)
        self._import(listing)
        client = self._login()
        page = client.get("/properties?q=AR.42.27.1.313")
        html = page.get_data(as_text=True)
        self.assertIn("data-media-fallback", html)
        self.assertIn("property-cover", html)

    def test_14_property_list_shows_cover(self):
        listing = load_listing(id="AR.42.27.1.314", photo_count=3, primary_index=1)
        self._import(listing)
        client = self._login()
        html = client.get("/properties?q=AR.42.27.1.314").get_data(as_text=True)
        self.assertIn("photo-1.jpg", html)

    def test_15_property_detail_shows_gallery(self):
        listing = load_listing(id="AR.42.27.1.315", photo_count=6, primary_index=0)
        row = self._import(listing)
        client = self._login()
        html = client.get(f"/properties/{row['id']}").get_data(as_text=True)
        self.assertIn("property-gallery", html)
        self.assertIn("photo-0.jpg", html)

    def test_16_no_media_shows_placeholder(self):
        add_property("Sin Foto 99", "CABA", self.org, agent_id=self.agent_id)
        client = self._login()
        html = client.get("/properties?q=Sin+Foto+99").get_data(as_text=True)
        self.assertIn("Sin fotos sincronizadas", html)

    def test_17_acm_gets_cover(self):
        listing = load_listing(id="AR.42.27.1.317", photo_count=3, primary_index=0)
        row = self._import(listing)
        from modules.acm_service import _attach_acm_photos

        view = {"subject": {}}
        _attach_acm_photos(view, self.org, row, [])
        self.assertIn("photo-0.jpg", view["photo_url"] or "")

    def test_18_brochure_survives_failed_image(self):
        listing = load_listing(id="AR.42.27.1.318", photo_count=2, primary_index=0)
        row = self._import(listing)
        with patch(
            "modules.property_sync.remote_media.fetch_allowed_image_bytes",
            return_value=None,
        ):
            brochure = generate_property_brochure(
                row["id"],
                self.org,
                False,
                get_user_by_id(self.admin),
            )
        self.assertTrue(brochure["pdf_bytes"].startswith(b"%PDF"))

    def test_19_generation_helper_cover_first(self):
        listing = load_listing(id="AR.42.27.1.319", photo_count=6, primary_index=4)
        row = self._import(listing)
        items = get_property_media_for_generation(row, limit=5)
        self.assertIn("photo-4.jpg", items[0]["original_url"])

    def test_20_generation_limit_five(self):
        listing = load_listing(id="AR.42.27.1.320", photo_count=10, primary_index=0)
        row = self._import(listing)
        items = get_property_media_for_generation(row, limit=5)
        self.assertEqual(len(items), 5)

    def test_21_manual_media_preserved(self):
        listing = load_listing(id="AR.42.27.1.321", photo_count=3, primary_index=0)
        row = self._import(listing)
        upsert_property_media(
            self.org,
            row["id"],
            source="manual",
            external_media_id="manual-cover",
            original_url="https://example.invalid/manual.jpg",
            storage_strategy=STRATEGY_REMOTE,
            is_cover=True,
            position=0,
        )
        self._import(listing)
        sources = {item["source"] for item in list_property_media(self.org, row["id"])}
        self.assertIn("manual", sources)
        self.assertIn("redremax", sources)
        cover = get_property_cover_media(row)
        self.assertEqual(cover["source"], "manual")

    def test_22_json_import_uses_same_pipeline(self):
        listing = load_listing(id="AR.42.27.1.322", photo_count=8, primary_index=5)
        hub, _warnings = RedRemaxPropertyNormalizer().normalize(
            listing, expected_office_id=OFFICE
        )
        self.assertEqual(len(hub["media"]), 5)
        row = self._import(listing)
        cover = get_property_cover_media(row)
        self.assertIn("photo-5.jpg", cover["original_url"])

    def test_23_public_fallback_disabled(self):
        provider = PublicListingMediaProvider()
        self.assertFalse(provider.is_enabled())
        self.assertEqual(provider.list_media({"external_url": "https://www.remax.com.ar/x"}), [])

    def test_display_src_rejects_unknown_host(self):
        src = media_display_src(
            {
                "source": "redremax",
                "storage_strategy": STRATEGY_REMOTE,
                "original_url": "https://evil.example/photo.jpg",
            }
        )
        self.assertIsNone(src)

    def test_cdn_saved_as_remote_url(self):
        mapped = map_photos([photo(8, primary=True)])
        self.assertTrue(mapped[0]["original_url"])
        self.assertEqual(mapped[0]["storage_strategy"], "remote_reference")
        info = describe_property_media({**mapped[0], "source": "redremax", "status": "active"})
        self.assertTrue(info["has_remote_url"])
        self.assertEqual(info["hostname"], "redremax-images.s3.amazonaws.com")
        self.assertNotIn("https://", str(info["hostname"]))

    def test_resolver_returns_remote_url(self):
        mapped = map_photos([photo(9, primary=True)])[0]
        mapped["source"] = "redremax"
        self.assertEqual(get_property_media_url(mapped), mapped["original_url"])

    def test_cover_and_gallery_use_same_url(self):
        listing = load_listing(id="AR.42.27.1.330", photo_count=4, primary_index=2)
        row = self._import(listing)
        cover = get_property_cover_media(row)
        cover_url = get_property_media_url(cover, row["id"])
        self.assertIn("photo-2.jpg", cover_url)
        client = self._login()
        html = client.get(f"/properties/{row['id']}").get_data(as_text=True)
        self.assertIn(f'data-media-url="{cover_url}"', html)
        self.assertIn("property-gallery__hero", html)
        self.assertIn("referrerpolicy=\"no-referrer\"", html)
        self.assertIn("__jrhMediaFallback", html)
        self.assertIn("redremax-images.s3.amazonaws.com", html)
        self.assertNotIn("filter: brightness(0)", html)

    def test_empty_gallery_uses_clear_placeholder(self):
        row = add_property("Sin Galeria 77", "CABA", self.org, agent_id=self.agent_id)
        client = self._login()
        html = client.get(f"/properties/{row}").get_data(as_text=True)
        self.assertIn("Sin fotos sincronizadas", html)
        self.assertIn("property-media-placeholder", html)

    def test_csp_allows_redremax_host(self):
        self.assertTrue(csp_allows_redremax_images(None))
        self.assertTrue(
            csp_allows_redremax_images(
                "default-src 'self'; img-src 'self' data: blob: https://redremax-images.s3.amazonaws.com"
            )
        )
        self.assertTrue(
            csp_allows_redremax_images(
                "default-src 'self'; img-src 'self' data: blob: https://redremax-images.s3-us-west-1.amazonaws.com"
            )
        )
        self.assertFalse(
            csp_allows_redremax_images("default-src 'self'; img-src 'self' data:")
        )
        client = self._login()
        page = client.get("/properties")
        self.assertTrue(
            csp_allows_redremax_images(page.headers.get("Content-Security-Policy"))
        )

    def test_local_media_still_resolves(self):
        property_id = add_property("Local Media", "CABA", self.org, agent_id=self.agent_id)
        media = upsert_property_media(
            self.org,
            property_id,
            source="manual",
            external_media_id="local-1",
            storage_key="organizations/1/media/local.png",
            storage_strategy=STRATEGY_COPY,
            is_cover=True,
            position=0,
        )
        with app.test_request_context():
            src = get_property_media_url(media, property_id)
        self.assertTrue(src)
        self.assertTrue(src.startswith("/"))
        self.assertNotIn("redremax-images", src)

    def test_prefix_used_only_when_cdn_missing(self):
        prefix_url = "https://redremax-images.s3.amazonaws.com/listings/from-prefix.jpg"
        chosen = resolve_photo_source_url(
            {"cdn": "", "prefix": prefix_url, "fileName": "x.jpg", "path": "/x.jpg"}
        )
        self.assertEqual(chosen, prefix_url)
        cdn_url = "https://redremax-images.s3.amazonaws.com/listings/from-cdn.jpg"
        self.assertEqual(
            resolve_photo_source_url({"cdn": cdn_url, "prefix": prefix_url}),
            cdn_url,
        )
        self.assertIsNone(resolve_photo_source_url({"fileName": "x.jpg", "path": "/x.jpg"}))

    def test_css_does_not_paint_photos_black(self):
        css = (Path(__file__).resolve().parents[1] / "static/css/properties-page.css").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("brightness(0)", css)
        self.assertNotIn("mix-blend-mode: multiply", css)
        self.assertIn("mix-blend-mode: normal", css)
        self.assertIn("background: #243044", css)
        self.assertNotIn("background: #000", css)
        self.assertNotIn("background:#000", css)

    def test_mock_example_url_is_not_renderable(self):
        item = {
            "source": "mock_network",
            "storage_strategy": STRATEGY_REMOTE,
            "original_url": "https://example.com/mock/MOCK-001/0.png",
            "is_cover": True,
        }
        self.assertIsNone(get_property_media_url(item))
        info = describe_property_media(item)
        self.assertEqual(info["hostname"], "example.com")
        self.assertFalse(info["renderable"])

    def test_mock_property_uses_placeholder(self):
        from modules.property_sync.mock import reset_mock_catalog
        from modules.property_sync.service import ensure_mock_integration, run_property_sync

        reset_mock_catalog()
        ensure_mock_integration(self.org)
        run_property_sync(self.org)
        italia = find_by_external(self.org, "MOCK-001")
        self.assertIsNotNone(italia)
        self.assertEqual(get_property_media_for_generation(italia), [])
        client = self._login()
        html = client.get(f"/properties/{italia['id']}").get_data(as_text=True)
        self.assertIn("Sin fotos sincronizadas", html)
        self.assertNotIn("example.com/mock", html)
        list_html = client.get("/properties?q=Italia+1341").get_data(as_text=True)
        self.assertIn("Sin fotos sincronizadas", list_html)
        self.assertNotIn("example.com/mock", list_html)

    def test_redremax_s3_media_is_renderable(self):
        mapped = map_photos([photo(11, primary=True)])[0]
        mapped["source"] = "redremax"
        self.assertTrue(describe_property_media(mapped)["renderable"])
        self.assertIn(
            "redremax-images.s3.amazonaws.com",
            get_property_media_url(mapped),
        )

    def test_cdn_creates_property_media(self):
        listing = load_listing(id="AR.42.27.1.401", photo_count=1, primary_index=0)
        row = self._import(listing)
        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["source"], "redremax")
        self.assertEqual(media[0]["media_type"], "photo")
        self.assertEqual(media[0]["status"], "active")
        self.assertTrue(media[0]["is_cover"])
        self.assertIn("photo-0.jpg", media[0]["original_url"])
        self.assertEqual(media[0]["storage_strategy"], "remote_reference")
        self.assertIsNone(media[0]["storage_key"])

    def test_twenty_photos_saves_max_five(self):
        listing = load_listing(id="AR.42.27.1.402", photo_count=20, primary_index=0)
        self.assertEqual(len(map_photos(listing["photos"])), 5)
        row = self._import(listing)
        self.assertEqual(len(list_property_media(self.org, row["id"])), 5)

    def test_first_five_invalid_sixth_valid_is_kept(self):
        photos = [photo(index, host="evil.example") for index in range(5)]
        photos.append(photo(5, primary=True))
        mapped = map_photos(photos)
        self.assertEqual(len(mapped), 1)
        self.assertIn("photo-5.jpg", mapped[0]["original_url"])
        listing = load_listing(id="AR.42.27.1.403", photo_count=0)
        listing["photos"] = photos
        row = self._import(listing)
        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 1)
        self.assertIn("photo-5.jpg", media[0]["original_url"])
        self.assertTrue(media[0]["is_cover"])

    def test_signed_cdn_is_stored_canonical(self):
        listing = load_listing(id="AR.42.27.1.404", photo_count=0)
        listing["photos"] = [signed_photo(0, primary=True), signed_photo(1)]
        mapped = map_photos(listing["photos"])
        self.assertEqual(len(mapped), 2)
        self.assertTrue(all("X-Amz-" not in item["original_url"] for item in mapped))
        self.assertTrue(all("?" not in item["original_url"] for item in mapped))
        row = self._import(listing)
        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 2)
        for item in media:
            self.assertNotIn("X-Amz-", item["original_url"] or "")
            self.assertNotIn("?", item["original_url"] or "")
            self.assertIn("redremax-images.s3.amazonaws.com", item["original_url"])
            self.assertIsNone(item["storage_key"])

    def test_cloudfront_used_when_cdn_missing(self):
        cloudfront_url = "https://redremax-images.s3.amazonaws.com/listings/from-cloudfront.jpg"
        chosen = resolve_photo_source_url(
            {
                "cdn": None,
                "cloudfront": cloudfront_url,
                "prefix": "https://redremax-images.s3.amazonaws.com/listings/from-prefix.jpg",
            }
        )
        self.assertEqual(chosen, cloudfront_url)

    def test_unknown_photo_host_is_recorded_not_allowlisted(self):
        audit = inspect_photos(
            [{"cdn": "https://d111111abcdef8.cloudfront.net/listings/x.jpg", "primary": True}]
        )
        self.assertEqual(audit["selected"], [])
        self.assertEqual(audit["unknown_hosts"], ["d111111abcdef8.cloudfront.net"])
        self.assertEqual(audit["reject_reasons"].get("invalid_host"), 1)

    def test_reimport_repairs_missing_media_without_duplicate_property(self):
        listing = load_listing(id="AR.42.27.1.405", photo_count=0)
        row = self._import(listing)
        self.assertEqual(list_property_media(self.org, row["id"]), [])
        listing = load_listing(id="AR.42.27.1.405", photo_count=6, primary_index=0)
        repaired = self._import(listing)
        self.assertEqual(row["id"], repaired["id"])
        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 5)
        matches = [
            item
            for item in get_properties(self.org, include_all_statuses=True)
            if item.get("external_id") == "AR.42.27.1.405"
        ]
        self.assertEqual(len(matches), 1)
        self._import(listing)
        self.assertEqual(len(list_property_media(self.org, row["id"])), 5)
        self.assertEqual(
            len(
                [
                    item
                    for item in get_properties(self.org, include_all_statuses=True)
                    if item.get("external_id") == "AR.42.27.1.405"
                ]
            ),
            1,
        )

    def test_dedupe_is_isolated_per_property(self):
        listing_a = load_listing(id="AR.42.27.1.406", photo_count=1, primary_index=0)
        listing_b = load_listing(id="AR.42.27.1.407", photo_count=1, primary_index=0)
        row_a = self._import(listing_a)
        row_b = self._import(listing_b)
        media_a = list_property_media(self.org, row_a["id"])
        media_b = list_property_media(self.org, row_b["id"])
        self.assertEqual(len(media_a), 1)
        self.assertEqual(len(media_b), 1)
        self.assertEqual(media_a[0]["original_url"], media_b[0]["original_url"])
        self.assertEqual(media_a[0]["external_media_id"], media_b[0]["external_media_id"])
        self.assertNotEqual(media_a[0]["id"], media_b[0]["id"])

    def test_redremax_s3_host_accepted_invalid_host_rejected(self):
        allowed = map_photos([photo(12, primary=True)])
        self.assertEqual(len(allowed), 1)
        self.assertIn("redremax-images.s3.amazonaws.com", allowed[0]["original_url"])
        self.assertEqual(map_photos([photo(12, host="evil.example")]), [])

    def test_preview_reports_photo_stats_and_missing_photos(self):
        listing = load_listing(id="AR.42.27.1.408", photo_count=3, primary_index=0)
        preview = preview_redremax_json_import(
            self.org,
            [json_upload(listings_envelope([listing]))],
            created_by=self.admin,
        )
        self.assertEqual(preview["photos_detected"], 3)
        self.assertEqual(preview["photos_valid"], 3)
        self.assertEqual(preview["photos_new"], 3)
        self.assertEqual(preview["photos_rejected"], 0)
        empty = load_listing(id="AR.42.27.1.409", photo_count=0)
        empty_preview = preview_redremax_json_import(
            self.org,
            [json_upload(listings_envelope([empty]))],
            created_by=self.admin,
        )
        self.assertEqual(empty_preview["photos_detected"], 0)
        self.assertEqual(empty_preview["photo_notes"][0]["code"], "no_photos")
        rejected = load_listing(id="AR.42.27.1.410", photo_count=0)
        rejected["photos"] = [photo(1, host="evil.example")]
        rejected_preview = preview_redremax_json_import(
            self.org,
            [json_upload(listings_envelope([rejected]))],
            created_by=self.admin,
        )
        self.assertEqual(rejected_preview["photos_detected"], 1)
        self.assertEqual(rejected_preview["photos_rejected"], 1)
        self.assertEqual(rejected_preview["photo_notes"][0]["code"], "photos_rejected")
        self.assertEqual(rejected_preview["unknown_hosts"], ["evil.example"])

    def test_classic_and_west1_hosts_are_allowed(self):
        classic = map_photos([photo(1, host="redremax-images.s3.amazonaws.com", primary=True)])
        west = map_photos(
            [photo(1, host="redremax-images.s3-us-west-1.amazonaws.com", primary=True)]
        )
        self.assertEqual(len(classic), 1)
        self.assertEqual(len(west), 1)
        self.assertIn("redremax-images.s3.amazonaws.com", classic[0]["original_url"])
        self.assertIn("redremax-images.s3-us-west-1.amazonaws.com", west[0]["original_url"])

    def test_foreign_s3_and_generic_amazonaws_hosts_are_rejected(self):
        self.assertEqual(
            map_photos([photo(1, host="fake-bucket.s3-us-west-1.amazonaws.com")]),
            [],
        )
        self.assertEqual(map_photos([photo(1, host="other-bucket.s3.amazonaws.com")]), [])
        self.assertEqual(map_photos([photo(1, host="s3.amazonaws.com")]), [])
        self.assertEqual(map_photos([photo(1, host="amazonaws.com")]), [])
        sneak = map_photos(
            [
                {
                    "cdn": "https://evil.example/listings/x.jpg?next=https://redremax-images.s3.amazonaws.com/x.jpg",
                    "primary": True,
                }
            ]
        )
        self.assertEqual(sneak, [])

    def test_unchanged_property_repairs_west1_media_without_duplicate(self):
        listing = load_listing(id="AR.42.27.301.206", photo_count=0)
        row = self._import(listing)
        self.assertEqual(list_property_media(self.org, row["id"]), [])
        listing = load_listing(id="AR.42.27.301.206", photo_count=0)
        listing["photos"] = [
            photo(
                index,
                primary=(index == 0),
                host="redremax-images.s3-us-west-1.amazonaws.com",
            )
            for index in range(8)
        ]
        preview = preview_redremax_json_import(
            self.org,
            [json_upload(listings_envelope([listing]))],
            created_by=self.admin,
        )
        self.assertEqual(preview["created"], 0)
        self.assertEqual(preview["updated"], 0)
        self.assertEqual(preview["unchanged"], 1)
        self.assertEqual(preview["photos_detected"], 8)
        self.assertEqual(preview["photos_valid"], 8)
        self.assertEqual(preview["photos_new"], 5)
        self.assertEqual(preview["photos_rejected"], 0)
        result = confirm_redremax_json_import(self.org, preview["confirm_token"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(result["photos_created"], 5)
        media = list_property_media(self.org, row["id"])
        self.assertEqual(len(media), 5)
        self.assertTrue(
            all(
                "redremax-images.s3-us-west-1.amazonaws.com" in (item["original_url"] or "")
                for item in media
            )
        )
        second = preview_redremax_json_import(
            self.org,
            [json_upload(listings_envelope([listing]), "again.json")],
            created_by=self.admin,
        )
        confirm_redremax_json_import(self.org, second["confirm_token"])
        self.assertEqual(len(list_property_media(self.org, row["id"])), 5)
        self.assertEqual(
            len(
                [
                    item
                    for item in get_properties(self.org, include_all_statuses=True)
                    if item.get("external_id") == "AR.42.27.301.206"
                ]
            ),
            1,
        )
