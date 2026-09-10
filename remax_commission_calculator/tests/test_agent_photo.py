"""Agent professional photo + AgentBranding isolation."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_agent_photo.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_branding import can_edit_agent_photo, get_agent_branding
from modules.agent_photo import (
    AgentPhotoError,
    image_has_alpha,
    resolve_agent_photo_path,
    save_agent_profile_photo,
)
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.agents_repository import get_agent_record
from web_app import app


def _png_bytes(*, alpha=True, size=(120, 160), color=(12, 80, 200, 180)):
    mode = "RGBA" if alpha else "RGB"
    image = Image.new(mode, size, color if alpha else color[:3])
    if alpha:
        pixels = image.load()
        for x in range(20):
            for y in range(20):
                pixels[x, y] = (0, 0, 0, 0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes(size=(120, 160)):
    image = Image.new("RGB", size, (30, 40, 50))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


class AgentPhotoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="agent-photo-tests")
        create_tables()
        cls.org = add_organization("Photo Org")
        other = add_organization("Photo Other")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana Foto", "Alto", cls.org)
        cls.other_agent = add_agent("Otra Foto", "Alto", cls.org)
        cls.foreign_agent = add_agent("Foreign Foto", "Alto", other)
        cls.agent_user = add_user(
            "photo_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            email="ana.foto@jrh.test",
            first_name="Ana",
            last_name="Foto",
            phone="1144442222",
        )
        cls.other_user = add_user(
            "photo_other",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
            email="otra@jrh.test",
        )
        cls.admin = add_user(
            "photo_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            agent_id=cls.agent_id,
            email="admin.foto@jrh.test",
        )

    def _login(self, user_id, role, org):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["role"] = role
            sess["organization_id"] = org
        return client

    def test_01_agent_can_upload_png(self):
        agent = save_agent_profile_photo(self.org, self.agent_id, _png_bytes(), content_type="image/png")
        self.assertTrue(agent["profile_photo_key"])
        self.assertEqual(agent["profile_photo_mime"], "image/png")

    def test_02_transparency_preserved(self):
        save_agent_profile_photo(self.org, self.agent_id, _png_bytes(), content_type="image/png")
        agent = get_agent_record(self.agent_id, self.org)
        path = resolve_agent_photo_path(agent)
        self.assertTrue(image_has_alpha(path))

    def test_03_jpg_works(self):
        agent = save_agent_profile_photo(self.org, self.agent_id, _jpeg_bytes(), content_type="image/jpeg")
        self.assertEqual(agent["profile_photo_mime"], "image/jpeg")
        self.assertFalse(image_has_alpha(resolve_agent_photo_path(agent)))

    def test_04_invalid_mime_rejected(self):
        with self.assertRaises(AgentPhotoError) as caught:
            save_agent_profile_photo(self.org, self.agent_id, b"not-an-image", content_type="text/plain")
        self.assertEqual(caught.exception.message_key, "agent_photo_err_mime")

    def test_05_oversized_rejected(self):
        from modules import agent_photo as photo_mod

        original = photo_mod.MAX_MEDIA_BYTES
        photo_mod.MAX_MEDIA_BYTES = 80
        try:
            with self.assertRaises(AgentPhotoError) as caught:
                save_agent_profile_photo(self.org, self.agent_id, _png_bytes(), content_type="image/png")
            self.assertEqual(caught.exception.message_key, "agent_photo_err_too_large")
        finally:
            photo_mod.MAX_MEDIA_BYTES = original

    def test_06_agent_sees_own_photo_page(self):
        save_agent_profile_photo(self.org, self.agent_id, _png_bytes(), content_type="image/png")
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        page = client.get("/profile/photo")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Foto profesional", page.get_data(as_text=True))
        photo = client.get(f"/agents/{self.agent_id}/profile-photo")
        self.assertEqual(photo.status_code, 200)
        self.assertIn("image/", photo.mimetype)

    def test_07_admin_can_edit_agent_photo(self):
        agent = get_agent_record(self.agent_id, self.org)
        admin = {"id": self.admin, "role": ROLE_ADMIN, "organization_id": self.org, "agent_id": self.agent_id}
        self.assertTrue(can_edit_agent_photo(admin, agent))
        client = self._login(self.admin, ROLE_ADMIN, self.org)
        self.assertEqual(client.get(f"/agents/{self.agent_id}/photo").status_code, 200)

    def test_08_other_agent_cannot_edit(self):
        client = self._login(self.other_user, ROLE_AGENT, self.org)
        self.assertEqual(client.get(f"/agents/{self.agent_id}/photo").status_code, 403)
        self.assertEqual(client.post(f"/agents/{self.agent_id}/photo", data={"action": "delete"}).status_code, 403)

    def test_09_branding_returns_correct_agent(self):
        branding = get_agent_branding(self.agent_id, self.org, language="es")
        self.assertEqual(branding["agent_id"], self.agent_id)
        self.assertEqual(branding["name"], "Ana Foto")
        self.assertEqual(branding["email"], "ana.foto@jrh.test")
        self.assertNotEqual(branding["email"], "admin.foto@jrh.test")
        self.assertEqual(branding["user_role"], ROLE_AGENT)

    def test_10_branding_does_not_mix_admin_email(self):
        branding = get_agent_branding(self.agent_id, self.org)
        self.assertEqual(branding["phone"], "1144442222")
        self.assertNotEqual(branding["email"], "admin.foto@jrh.test")


if __name__ == "__main__":
    unittest.main()
