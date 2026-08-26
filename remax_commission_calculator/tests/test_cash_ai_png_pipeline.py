"""
Cash AI PNG pipeline diagnostics (no live OpenAI calls).
"""

from __future__ import annotations

import base64
import io
import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
_TEST_ROOT = Path(_TEST_TMP.name)
os.environ["DATABASE_PATH"] = str(_TEST_ROOT / "test_cash_ai_png.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_TEST_ROOT / "uploads")
os.environ["CASH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.cash_ai_provider import (
    CashAiProviderError,
    build_multimodal_user_content,
    get_cash_ai_config_status,
)
from modules.cash_ai_service import start_ai_analysis
from modules.cash_receipts import (
    prepare_image_for_ai,
    save_receipt_bytes,
    validate_receipt_upload,
)
from modules.config import apply_config, get_private_upload_root
from modules.database import (
    add_organization,
    add_user,
    create_tables,
)
from modules.auth import ROLE_ADMIN, hash_password
from web_app import app


FIXTURE_PNG = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "cash_receipt_sample.png"
)


class _FakeUpload:
    def __init__(self, filename, raw, mimetype):
        self.filename = filename
        self.mimetype = mimetype
        self.stream = io.BytesIO(raw)
        self._raw = raw

    def read(self):
        return self._raw


class CashAiPngPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()
        cls.org = add_organization("PNG Pipeline Org")
        cls.admin = add_user(
            "png_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            cls.org,
        )
        cls.png_bytes = FIXTURE_PNG.read_bytes()

    def test_fixture_is_valid_png(self):
        self.assertTrue(FIXTURE_PNG.is_file())
        self.assertTrue(
            self.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        )
        self.assertGreater(len(self.png_bytes), 0)

    def test_validate_accepts_png_with_wrong_declared_mime(self):
        upload = _FakeUpload(
            "Comprobante Mercado Pago.png",
            self.png_bytes,
            "image/x-png",
        )
        payload = validate_receipt_upload(upload)
        self.assertEqual(payload["content_type"], "image/png")
        self.assertEqual(payload["size"], len(self.png_bytes))
        self.assertTrue(payload["image_info"]["pillow_ok"])
        self.assertEqual(payload["image_info"]["format"], "PNG")

    def test_save_and_reread_from_private_root(self):
        upload = _FakeUpload(
            "ticket.png",
            self.png_bytes,
            "image/png",
        )
        payload = validate_receipt_upload(upload)
        saved = save_receipt_bytes(
            self.org,
            payload=payload,
            draft_id=99,
        )
        absolute = (
            get_private_upload_root() / saved["relative_path"]
        )
        self.assertTrue(absolute.is_file())
        self.assertEqual(
            absolute.read_bytes(),
            self.png_bytes,
        )

    def test_prepare_image_returns_bytes_not_path(self):
        prepared, mime = prepare_image_for_ai(
            self.png_bytes,
            "image/png",
        )
        self.assertIsInstance(prepared, (bytes, bytearray))
        self.assertFalse(
            isinstance(prepared, str)
        )
        self.assertTrue(mime.startswith("image/"))
        # After Pillow path we expect JPEG bytes for AI.
        self.assertIn(mime, ("image/jpeg", "image/png"))

    def test_multimodal_payload_uses_data_url_not_path(self):
        prepared, mime = prepare_image_for_ai(
            self.png_bytes,
            "image/png",
        )
        content = build_multimodal_user_content(
            user_context_text="Limpieza oficina",
            image_bytes=prepared,
            image_content_type=mime,
        )
        self.assertEqual(len(content), 2)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        url = content[1]["image_url"]["url"]
        self.assertTrue(url.startswith(f"data:{mime};base64,"))
        self.assertNotIn("/uploads/", url)
        self.assertNotIn("organizations/", url)
        b64 = url.split(",", 1)[1]
        decoded = base64.b64decode(b64)
        self.assertEqual(decoded, prepared)

    def test_multimodal_rejects_filesystem_path_string(self):
        with self.assertRaises(CashAiProviderError) as ctx:
            build_multimodal_user_content(
                user_context_text="x",
                image_bytes="/data/uploads/ticket.png",
                image_content_type="image/png",
            )
        self.assertEqual(
            str(ctx.exception),
            "image_path_not_allowed",
        )

    def test_full_mock_pipeline_with_png(self):
        upload = _FakeUpload(
            "mpago.png",
            self.png_bytes,
            "image/png",
        )
        draft = start_ai_analysis(
            self.org,
            user_id=self.admin,
            user_context_text="Productos de limpieza, efectivo",
            file_storage=upload,
        )
        self.assertEqual(draft["status"], "review")
        self.assertTrue(draft["attachment_path"])
        self.assertTrue(draft["attachment_hash"])
        self.assertEqual(
            draft["draft_payload"]["payment_method"],
            "cash",
        )

    def test_text_only_mock_pipeline(self):
        draft = start_ai_analysis(
            self.org,
            user_id=self.admin,
            user_context_text=(
                "Egreso de 25000 pesos por productos de "
                "limpieza, pagado por transferencia."
            ),
        )
        self.assertEqual(draft["status"], "review")
        payload = draft["draft_payload"]
        self.assertEqual(payload["movement_type"], "expense")
        self.assertEqual(payload["category"], "cleaning")
        self.assertEqual(payload["payment_method"], "transfer")

    def test_config_status_reports_key_absence_safely(self):
        status = get_cash_ai_config_status()
        self.assertIn("openai_api_key_present", status)
        self.assertFalse(status["openai_api_key_present"])
        self.assertEqual(status["provider"], "mock")


if __name__ == "__main__":
    unittest.main()
