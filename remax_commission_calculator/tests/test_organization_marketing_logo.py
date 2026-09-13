"""Organization marketing logo persistence. Isolated temp DB + private volume."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from werkzeug.datastructures import FileStorage

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_marketing_logo.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_ADMIN, hash_password
from modules.config import apply_config, get_private_upload_root
from modules.database import add_organization, add_user, create_tables
from modules.database.organization_settings_repository import (
    get_organization_settings,
    update_organization_marketing_fields,
)
from modules.marketing_branding import (
    get_organization_marketing_branding,
    resolve_marketing_branding,
)
from modules.organization_marketing_logo import (
    MarketingLogoError,
    apply_marketing_logo_form,
    normalize_marketing_logo_url,
    resolve_stored_logo_file,
)
from web_app import app


def _png_bytes():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), (200, 16, 46, 255)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()


def _png_file(name="office.png"):
    return FileStorage(stream=io.BytesIO(_png_bytes()), filename=name, content_type="image/png")


class OrganizationMarketingLogoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="marketing-logo-tests")
        create_tables()
        cls.org = add_organization("Logo Org")
        cls.admin_id = add_user(
            "logo_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            cls.org,
        )
        update_organization_marketing_fields(
            cls.org,
            marketing_brand_name="RE/MAX Data House",
            legal_broker_name="Mauro Marvisi",
            legal_broker_license="CUCICBA 1762",
        )

    def _client(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.admin_id
            session["role"] = ROLE_ADMIN
            session["organization_id"] = self.org
        return client

    def _settings(self):
        return get_organization_settings(self.org)

    def _save_logo(self, form, files=None):
        fields = apply_marketing_logo_form(
            self.org,
            form,
            files or {},
            self._settings(),
        )
        update_organization_marketing_fields(self.org, **fields)
        return fields

    def setUp(self):
        self._save_logo({"remove_marketing_logo": "yes"})

    def test_case_a_upload_png_persists(self):
        self._save_logo(
            {"marketing_logo_source": "upload"},
            {"marketing_logo_file": _png_file()},
        )
        settings = self._settings()
        self.assertTrue(settings["marketing_logo_path"])
        self.assertEqual(settings["marketing_logo_source"], "upload")
        stored = get_private_upload_root() / settings["marketing_logo_path"]
        self.assertTrue(stored.is_file())
        self.assertNotIn("static", str(stored).replace("\\", "/"))
        branding = get_organization_marketing_branding(self.org)
        self.assertEqual(branding["logo_source"], "upload")
        self.assertTrue(Path(branding["logo_path"]).is_file())
        resolved = resolve_marketing_branding(settings)
        self.assertTrue(resolved["has_logo"])
        self.assertEqual(resolved["logo_source"], "upload")
        client = self._client()
        page = client.get("/settings/organization")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"/settings/organization/marketing-logo", page.data)
        preview = client.get("/settings/organization/marketing-logo")
        self.assertEqual(preview.status_code, 200)
        self.assertGreater(len(preview.data), 20)

    def test_case_b_https_url_persists(self):
        url = "https://cdn.example.com/office-logo.png"
        self._save_logo({"marketing_logo_source": "url", "marketing_logo_url": url})
        settings = self._settings()
        self.assertEqual(settings["marketing_logo_url"], url)
        self.assertEqual(settings["marketing_logo_source"], "url")
        branding = get_organization_marketing_branding(self.org)
        self.assertEqual(branding["logo_source"], "url")
        self.assertEqual(branding["logo_url"], url)
        client = self._client()
        page = client.get("/settings/organization")
        self.assertEqual(page.status_code, 200)
        self.assertIn(url.encode("utf-8"), page.data)

    def test_case_c_new_upload_replaces_file(self):
        first = self._save_logo(
            {"marketing_logo_source": "upload"},
            {"marketing_logo_file": _png_file("one.png")},
        )
        first_path = get_private_upload_root() / first["marketing_logo_path"]
        first_mtime = first_path.stat().st_mtime
        self._save_logo(
            {"marketing_logo_source": "upload"},
            {"marketing_logo_file": _png_file("two.png")},
        )
        settings = self._settings()
        stored = get_private_upload_root() / settings["marketing_logo_path"]
        self.assertTrue(stored.is_file())
        self.assertGreaterEqual(stored.stat().st_mtime, first_mtime)
        branding = get_organization_marketing_branding(self.org)
        self.assertEqual(Path(branding["logo_path"]).name, stored.name)

    def test_case_d_remove_falls_back_to_name(self):
        self._save_logo(
            {"marketing_logo_source": "upload"},
            {"marketing_logo_file": _png_file()},
        )
        self._save_logo({"remove_marketing_logo": "yes"})
        settings = self._settings()
        self.assertFalse(settings.get("marketing_logo_path"))
        self.assertFalse(settings.get("marketing_logo_url"))
        branding = get_organization_marketing_branding(self.org)
        self.assertEqual(branding["logo_source"], "none")
        self.assertIsNone(branding["logo_path"])
        self.assertEqual(branding["brand_name"], "RE/MAX Data House")

    def test_invalid_url_is_rejected(self):
        with self.assertRaises(MarketingLogoError):
            normalize_marketing_logo_url("C:\\fakepath\\logo.png")
        with self.assertRaises(MarketingLogoError):
            self._save_logo(
                {"marketing_logo_source": "url", "marketing_logo_url": "not-a-url"}
            )
        settings = self._settings()
        self.assertNotEqual(settings.get("marketing_logo_url"), "not-a-url")

    def test_http_multipart_upload_survives_reload(self):
        client = self._client()
        settings = self._settings()
        payload = {
            "display_name": settings.get("display_name") or "Logo Org",
            "default_language": "es",
            "default_currency": "USD",
            "timezone": settings.get("timezone") or "America/Argentina/Buenos_Aires",
            "marketing_brand_name": "RE/MAX Data House",
            "legal_broker_name": "Mauro Marvisi",
            "legal_broker_license": "CUCICBA 1762",
            "marketing_logo_source": "upload",
            "marketing_logo_file": (io.BytesIO(_png_bytes()), "office.png"),
        }
        saved = client.post(
            "/settings/organization",
            data=payload,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        stored = self._settings()
        self.assertEqual(stored["marketing_logo_source"], "upload")
        self.assertTrue(stored["marketing_logo_path"])
        reload_page = client.get("/settings/organization")
        self.assertIn(b"/settings/organization/marketing-logo", reload_page.data)

    def test_http_url_is_not_treated_as_local_path(self):
        self.assertIsNone(
            resolve_stored_logo_file("https://cdn.example.com/office-logo.png")
        )

    def test_case_e_file_lives_on_private_volume(self):
        self._save_logo(
            {"marketing_logo_source": "upload"},
            {"marketing_logo_file": _png_file()},
        )
        settings = self._settings()
        stored = resolve_stored_logo_file(settings["marketing_logo_path"])
        self.assertIsNotNone(stored)
        self.assertTrue(str(stored).startswith(str(get_private_upload_root())))


if __name__ == "__main__":
    unittest.main()
