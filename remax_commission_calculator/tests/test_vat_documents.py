"""
Smoke tests for private VAT document permissions and validation.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

# Isolate storage/DB before importing app modules that read env.
_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_vat_docs.db"
)

from modules.access_codes import hash_access_secret
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.guest_access_repository import (
    create_guest_access,
    get_guest_access_by_token_hash,
)
from modules.database.organizations_repository import add_organization
from modules.vat_documents import (
    DOC_TYPE_AGENT_CLIENT,
    DOC_TYPE_MARTILLERO_CLIENT,
    absolute_document_path,
    list_vat_documents_for_operation,
    remove_vat_document,
    upload_or_replace_vat_document,
    validate_vat_upload,
)
from web_app import app


def _pdf_bytes(size=200):
    payload = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    payload += b"1 0 obj<<>>endobj\ntrailer<<>>\n"
    if size > len(payload):
        payload += b"0" * (size - len(payload))
    return payload


class FakeStorage:
    def __init__(self, filename, data, mimetype="application/pdf"):
        self.filename = filename
        self.stream = io.BytesIO(data)
        self.mimetype = mimetype

    def save(self, path):
        Path(path).write_bytes(self.stream.getvalue())
        self.stream.seek(0)


class VatDocumentsSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()

        cls.org_a = add_organization("Org A Docs")
        cls.org_b = add_organization("Org B Docs")

        cls.agent_a1 = add_agent("Agent A1", "Alto", cls.org_a)
        cls.agent_a2 = add_agent("Agent A2", "Alto", cls.org_a)
        cls.agent_b1 = add_agent("Agent B1", "Alto", cls.org_b)

        pwd = hash_password("Password1")

        cls.admin_a = add_user(
            "admin_a_docs",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
            is_active=True,
            email="admin_a_docs@example.com",
        )
        cls.user_a1 = add_user(
            "agent_a1_docs",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a1,
            is_active=True,
            email="agent_a1_docs@example.com",
        )
        cls.user_a2 = add_user(
            "agent_a2_docs",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a2,
            is_active=True,
            email="agent_a2_docs@example.com",
        )
        cls.admin_b = add_user(
            "admin_b_docs",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
            is_active=True,
            email="admin_b_docs@example.com",
        )

        cls.op_a1 = cls._insert_operation(cls.org_a, cls.agent_a1)
        cls.op_a2 = cls._insert_operation(cls.org_a, cls.agent_a2)
        cls.op_b1 = cls._insert_operation(cls.org_b, cls.agent_b1)

        cls.guest_token_hash = hash_access_secret("guest-docs-token")
        create_guest_access(
            cls.org_a,
            cls.guest_token_hash,
            cls.admin_a,
            label="docs-guest",
        )

    @classmethod
    def tearDownClass(cls):
        _TEST_TMP.cleanup()

    @classmethod
    def _insert_operation(cls, organization_id, agent_id):
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO properties (
                address,
                jurisdiction,
                organization_id,
                agent_id,
                status
            )
            VALUES (?, 'CABA', ?, ?, 'approved')
            """,
            (
                f"Address {organization_id}-{agent_id}",
                organization_id,
                agent_id,
            ),
        )
        property_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO operations (
                operation_date,
                agent_id,
                property_id,
                organization_id,
                was_invoiced,
                vat_amount,
                sale_price,
                commission_rate,
                total_commission,
                commission_after_abao,
                abao,
                martillero,
                agent_payment,
                office_payment,
                office_total,
                currency,
                original_amount,
                exchange_rate,
                status
            )
            VALUES (
                '01/01/2026', ?, ?, ?, 'no',
                0, 100000, 3, 3000, 2850,
                150, 300, 2000, 850, 1000,
                'USD', 100000, 1, 'approved'
            )
            """,
            (agent_id, property_id, organization_id),
        )
        operation_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return operation_id

    def _client_as(self, user_id=None, guest=False):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess.clear()
            if guest:
                access = get_guest_access_by_token_hash(
                    self.guest_token_hash
                )
                sess["guest_access_id"] = access["id"]
                sess["guest_organization_id"] = access[
                    "organization_id"
                ]
                sess["guest_token_hash"] = self.guest_token_hash
            elif user_id is not None:
                sess["user_id"] = user_id
        return client

    def test_01_admin_upload_view_replace_delete_both(self):
        client = self._client_as(self.admin_a)

        for doc_type in (
            DOC_TYPE_MARTILLERO_CLIENT,
            DOC_TYPE_AGENT_CLIENT,
        ):
            response = client.post(
                f"/operations/{self.op_a1}/vat-documents/{doc_type}",
                data={
                    "document": (
                        io.BytesIO(_pdf_bytes()),
                        "factura.pdf",
                    )
                },
                content_type="multipart/form-data",
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)

        docs = list_vat_documents_for_operation(
            self.org_a,
            self.op_a1,
        )
        self.assertEqual(len(docs), 2)

        first = docs[0]
        path1 = absolute_document_path(first)
        self.assertTrue(path1.is_file())

        response = client.get(f"/vat-documents/{first['id']}")
        self.assertEqual(response.status_code, 200)

        response = client.post(
            f"/operations/{self.op_a1}/vat-documents/{first['doc_type']}",
            data={
                "document": (
                    io.BytesIO(_pdf_bytes(320)),
                    "factura2.pdf",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(path1.exists())

        refreshed = list_vat_documents_for_operation(
            self.org_a,
            self.op_a1,
        )
        self.assertEqual(len(refreshed), 2)

        for doc in refreshed:
            response = client.post(
                f"/vat-documents/{doc['id']}/delete"
            )
            self.assertEqual(response.status_code, 302)
            self.assertFalse(absolute_document_path(doc).exists())

        self.assertEqual(
            list_vat_documents_for_operation(
                self.org_a,
                self.op_a1,
            ),
            [],
        )

    def test_02_agent_own_operation(self):
        client = self._client_as(self.user_a1)
        response = client.post(
            f"/operations/{self.op_a1}/vat-documents/"
            f"{DOC_TYPE_MARTILLERO_CLIENT}",
            data={
                "document": (
                    io.BytesIO(_pdf_bytes()),
                    "own.pdf",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)

        docs = list_vat_documents_for_operation(
            self.org_a,
            self.op_a1,
        )
        self.assertEqual(len(docs), 1)
        doc = docs[0]

        response = client.get(f"/vat-documents/{doc['id']}")
        self.assertEqual(response.status_code, 200)

        response = client.post(
            f"/vat-documents/{doc['id']}/delete"
        )
        self.assertEqual(response.status_code, 302)

    def test_03_agent_cannot_access_other_agent_doc(self):
        storage = FakeStorage("a2.pdf", _pdf_bytes())
        doc, err = upload_or_replace_vat_document(
            organization_id=self.org_a,
            operation_id=self.op_a2,
            doc_type=DOC_TYPE_MARTILLERO_CLIENT,
            file_storage=storage,
            uploaded_by_user_id=self.user_a2,
        )
        self.assertIsNone(err)

        client = self._client_as(self.user_a1)
        response = client.get(f"/vat-documents/{doc['id']}")
        # App maps abort(403) -> redirect dashboard.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/")
        self.assertNotEqual(
            response.mimetype,
            "application/pdf",
        )

    def test_04_admin_org_a_cannot_access_org_b(self):
        storage = FakeStorage("b.pdf", _pdf_bytes())
        doc, err = upload_or_replace_vat_document(
            organization_id=self.org_b,
            operation_id=self.op_b1,
            doc_type=DOC_TYPE_MARTILLERO_CLIENT,
            file_storage=storage,
            uploaded_by_user_id=self.admin_b,
        )
        self.assertIsNone(err)

        client = self._client_as(self.admin_a)
        response = client.get(f"/vat-documents/{doc['id']}")
        # App maps abort(404) -> redirect dashboard.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/")
        self.assertNotEqual(
            response.mimetype,
            "application/pdf",
        )

    def test_05_guest_blocked(self):
        storage = FakeStorage("g.pdf", _pdf_bytes())
        doc, err = upload_or_replace_vat_document(
            organization_id=self.org_a,
            operation_id=self.op_a1,
            doc_type=DOC_TYPE_MARTILLERO_CLIENT,
            file_storage=storage,
            uploaded_by_user_id=self.admin_a,
        )
        self.assertIsNone(err)

        client = self._client_as(guest=True)
        response = client.get(f"/vat-documents/{doc['id']}")
        # Guest hits abort(403) -> redirect dashboard.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/")
        self.assertNotEqual(
            response.mimetype,
            "application/pdf",
        )

    def test_06_file_too_large(self):
        big = _pdf_bytes(10 * 1024 * 1024 + 50)
        storage = FakeStorage("big.pdf", big)
        parsed, err = validate_vat_upload(storage)
        self.assertIsNone(parsed)
        self.assertEqual(err, "err_vat_doc_too_large")

    def test_07_invalid_extension(self):
        storage = FakeStorage("bad.exe", b"MZ" + b"0" * 100)
        parsed, err = validate_vat_upload(storage)
        self.assertIsNone(parsed)
        self.assertEqual(err, "err_vat_doc_invalid_type")

    def test_08_replace_no_orphan(self):
        storage = FakeStorage("one.pdf", _pdf_bytes())
        doc, err = upload_or_replace_vat_document(
            organization_id=self.org_a,
            operation_id=self.op_a1,
            doc_type=DOC_TYPE_AGENT_CLIENT,
            file_storage=storage,
            uploaded_by_user_id=self.admin_a,
        )
        self.assertIsNone(err)
        old_path = absolute_document_path(doc)

        storage2 = FakeStorage("two.pdf", _pdf_bytes(400))
        doc2, err2 = upload_or_replace_vat_document(
            organization_id=self.org_a,
            operation_id=self.op_a1,
            doc_type=DOC_TYPE_AGENT_CLIENT,
            file_storage=storage2,
            uploaded_by_user_id=self.admin_a,
        )
        self.assertIsNone(err2)
        self.assertFalse(old_path.exists())
        self.assertTrue(absolute_document_path(doc2).is_file())
        docs = [
            item
            for item in list_vat_documents_for_operation(
                self.org_a,
                self.op_a1,
            )
            if item["doc_type"] == DOC_TYPE_AGENT_CLIENT
        ]
        self.assertEqual(len(docs), 1)

    def test_09_manual_calculator_has_no_docs_ui(self):
        client = self._client_as(self.admin_a)
        response = client.get("/vat-calculator")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("vat-doc-row", html)
        self.assertNotIn("Factura martillero al cliente", html)


if __name__ == "__main__":
    unittest.main()
