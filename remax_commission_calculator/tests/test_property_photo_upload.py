"""Local property photos stay on the office inventory and in property_media."""

from __future__ import annotations

import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "property_photos.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["APP_ENV"] = "development"

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config, get_private_upload_root
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.database.property_media_repository import list_property_media
from modules.property_sync.media import get_property_original_media
from modules.property_sync.security import MAX_MEDIA_BYTES
from modules.public_share import ensure_property_link, resolve_property
from web_app import app


def _image_bytes(fmt, color):
    buffer = BytesIO()
    Image.new("RGB", (12, 8), color).save(buffer, format=fmt)
    return buffer.getvalue()


def _file(payload, name, content_type):
    return (BytesIO(payload), name, content_type)


class PropertyPhotoUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="photo-upload")
        create_tables()
        cls.org = add_organization("Achard Propiedades QA")
        cls.other_org = add_organization("Otra Oficina")
        cls.owner = add_agent("Emilio", "Alto", cls.org)
        cls.other_agent = add_agent("Ajeno", "Alto", cls.org)
        cls.foreign_agent = add_agent("Externo", "Alto", cls.other_org)
        password = hash_password("Password1")
        cls.owner_user = add_user(
            "emilio",
            password,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.owner,
            email="emilio@photos.test",
        )
        cls.other_user = add_user(
            "ajeno",
            password,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
            email="ajeno@photos.test",
        )
        cls.admin = add_user(
            "admin_photos",
            password,
            ROLE_ADMIN,
            cls.org,
            email="admin@photos.test",
        )
        cls.foreign_user = add_user(
            "foreign_photos",
            password,
            ROLE_AGENT,
            cls.other_org,
            agent_id=cls.foreign_agent,
            email="foreign@photos.test",
        )
        cls.property_id = add_property(
            "Calle Test 1234",
            "Buenos Aires",
            cls.org,
            agent_id=cls.owner,
            property_type="apartment",
            listing_purpose="sale",
            listing_price=100000,
            listing_currency="USD",
        )
        cls.other_property = add_property(
            "Calle Ajena 9",
            "Buenos Aires",
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.foreign_property = add_property(
            "Calle Otra Org 1",
            "Buenos Aires",
            cls.other_org,
            agent_id=cls.foreign_agent,
        )
        cls.png = _image_bytes("PNG", (20, 40, 60))
        cls.jpg = _image_bytes("JPEG", (80, 20, 20))
        cls.webp = _image_bytes("WEBP", (20, 80, 40))
        cls.client = app.test_client()

    def _login(self, user_id, organization_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["organization_id"] = organization_id

    def _upload(self, property_id, *files):
        payload = {"files": list(files)} if len(files) > 1 else {"files": files[0]}
        return self.client.post(
            f"/properties/{property_id}/photos",
            data=payload,
            content_type="multipart/form-data",
        )

    def test_detail_shows_local_file_picker(self):
        self._login(self.owner_user, self.org)
        page = self.client.get(f"/properties/{self.property_id}")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Agregar fotos", body)
        self.assertIn('accept="image/jpeg,image/png,image/webp"', body)
        self.assertIn("multiple", body)
        self.assertIn('type="file"', body)

    def test_owner_uploads_png_jpg_and_webp(self):
        self._login(self.owner_user, self.org)
        one = self._upload(
            self.property_id,
            _file(self.png, "living.png", "image/png"),
        )
        self.assertEqual(one.status_code, 302)
        several = self._upload(
            self.property_id,
            _file(self.jpg, "cocina.jpg", "image/jpeg"),
            _file(self.webp, "patio.webp", "image/webp"),
        )
        self.assertEqual(several.status_code, 302)
        rows = list_property_media(self.org, self.property_id)
        types = {row["content_type"] for row in rows}
        self.assertEqual(types, {"image/png", "image/jpeg", "image/webp"})
        self.assertTrue(all(row["source"] == "manual" for row in rows))
        self.assertTrue(all(row["organization_id"] == self.org for row in rows))
        self.assertTrue(all(row["storage_key"].startswith("organizations/") for row in rows))
        root = get_private_upload_root()
        for row in rows:
            stored = root / row["storage_key"]
            self.assertTrue(stored.is_file())
            self.assertNotIn("tmp", stored.parts)
        cover = [row for row in rows if row["is_cover"]]
        self.assertEqual(len(cover), 1)
        self.assertEqual(cover[0]["content_type"], "image/png")
        originals = get_property_original_media(self.property_id, self.org)
        self.assertEqual(len(originals), 3)
        self.assertTrue(all(item["source"] == "manual" for item in originals))

    def test_invalid_empty_and_oversized_files_are_rejected(self):
        self._login(self.owner_user, self.org)
        before = len(list_property_media(self.org, self.property_id))
        invalid = self._upload(
            self.property_id,
            _file(b"not-an-image", "fake.png", "image/png"),
        )
        empty = self._upload(
            self.property_id,
            _file(b"", "empty.jpg", "image/jpeg"),
        )
        huge = self._upload(
            self.property_id,
            _file(b"\x00" * (MAX_MEDIA_BYTES + 1), "big.png", "image/png"),
        )
        self.assertEqual(invalid.status_code, 302)
        self.assertEqual(empty.status_code, 302)
        self.assertEqual(huge.status_code, 302)
        self.assertEqual(len(list_property_media(self.org, self.property_id)), before)
        invalid_page = self.client.get(invalid.headers["Location"])
        self.assertIn("no es una imagen", invalid_page.get_data(as_text=True))

    def test_other_agent_and_other_office_cannot_upload(self):
        self._login(self.other_user, self.org)
        before = len(list_property_media(self.org, self.property_id))
        denied = self._upload(
            self.property_id,
            _file(self.png, "intruso.png", "image/png"),
        )
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.headers.get("Location"), "/")
        self.assertEqual(len(list_property_media(self.org, self.property_id)), before)
        self._login(self.foreign_user, self.other_org)
        missing = self._upload(
            self.property_id,
            _file(self.png, "otra.png", "image/png"),
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            list_property_media(self.other_org, self.foreign_property),
            [],
        )

    def test_admin_can_upload_reorder_cover_and_delete(self):
        self._login(self.admin, self.org)
        created = self._upload(
            self.other_property,
            _file(self.png, "uno.png", "image/png"),
            _file(self.jpg, "dos.jpg", "image/jpeg"),
        )
        self.assertEqual(created.status_code, 302)
        rows = list_property_media(self.org, self.other_property)
        self.assertEqual(len(rows), 2)
        first, second = sorted(rows, key=lambda row: row["position"])
        self.assertTrue(first["is_cover"])
        moved = self.client.post(
            f"/properties/{self.other_property}/photos/{first['id']}/move",
            data={"direction": "later"},
        )
        self.assertEqual(moved.status_code, 302)
        ordered = sorted(
            list_property_media(self.org, self.other_property),
            key=lambda row: row["position"],
        )
        self.assertEqual(ordered[0]["id"], second["id"])
        self.assertEqual(ordered[1]["id"], first["id"])
        cover = self.client.post(
            f"/properties/{self.other_property}/photos/{first['id']}/cover",
        )
        self.assertEqual(cover.status_code, 302)
        current = list_property_media(self.org, self.other_property)
        covers = [row for row in current if row["is_cover"]]
        self.assertEqual([row["id"] for row in covers], [first["id"]])
        removed = self.client.post(
            f"/properties/{self.other_property}/photos/{first['id']}/delete",
        )
        self.assertEqual(removed.status_code, 302)
        left = list_property_media(self.org, self.other_property)
        self.assertEqual([row["id"] for row in left], [second["id"]])
        self.assertTrue(left[0]["is_cover"])
        stored = get_private_upload_root() / first["storage_key"]
        self.assertFalse(stored.exists())

    def test_public_share_shows_manual_photo_without_external_source(self):
        from modules.database.properties_repository import get_property_record

        record = get_property_record(self.property_id, self.org)
        self.assertFalse(record.get("external_id"))
        self._login(self.owner_user, self.org)
        page = self.client.get(f"/properties/{self.property_id}")
        self.assertIn("/gallery/", page.get_data(as_text=True))
        link = ensure_property_link(self.org, self.property_id, agent_id=self.owner)
        status, payload = resolve_property(link["token"], base_url="https://app.test")
        self.assertEqual(status, "ok")
        self.assertTrue(payload["photos"])
        self.assertIn("/photo/0", payload["photos"][0]["src"])
        guest = app.test_client()
        photo = guest.get(f"/p/{link['token']}/photo/0")
        self.assertEqual(photo.status_code, 200)
        self.assertTrue(photo.data.startswith(b"\x89PNG"))
        public_page = guest.get(f"/p/{link['token']}")
        self.assertIn(f"/p/{link['token']}/photo/0", public_page.get_data(as_text=True))
