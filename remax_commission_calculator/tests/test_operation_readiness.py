"""
Tests for operation readiness validation and submission blocking.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_operation_readiness.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_property,
    add_user,
    create_tables,
)
from modules.database.connection import get_connection
from modules.database.organizations_repository import add_organization
from modules.operation_documents import upload_or_replace_vat_document
from modules.operation_readiness import (
    OperationNotReadyError,
    submit_operation_for_approval,
    validate_operation_readiness,
)
from modules.operations import save_calculated_operation
from modules.workflow import STATUS_DRAFT
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


class OperationReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()

        cls.org_id = add_organization("Org Readiness")
        cls.agent_id = add_agent("Agent R", "Alto", cls.org_id)
        cls.property_id = add_property(
            "Av. Corrientes 123",
            "CABA",
            cls.org_id,
            agent_id=cls.agent_id,
            property_type="apartment",
        )
        pwd = hash_password("Password1")
        cls.admin = add_user(
            "admin_readiness",
            pwd,
            ROLE_ADMIN,
            cls.org_id,
            is_active=True,
            email="admin_readiness@example.com",
        )
        cls.agent_user_id = add_user(
            "agent_readiness",
            pwd,
            ROLE_AGENT,
            cls.org_id,
            agent_id=cls.agent_id,
            is_active=True,
            email="agent_readiness@example.com",
        )

    def _create_draft_operation(
        self,
        invoice_full_commission="no",
        was_invoiced="no",
    ):
        operation = {
            "date": "01/01/2026",
            "agent": "Agent R",
            "agent_type": "Alto",
            "property": "Av. Corrientes 123",
            "jurisdiction": "CABA",
            "was_invoiced": was_invoiced,
            "invoice_full_commission": invoice_full_commission,
            "vat_amount": 210 if was_invoiced == "yes" else 0,
            "sale_price": 100000,
            "commission_rate": 3,
            "total_commission": 3000,
            "commission_after_abao": 3000,
            "abao": 0,
            "martillero": 120,
            "agent_payment": 1728,
            "office_payment": 1152,
            "office_total": 1152,
            "currency": "USD",
            "original_amount": 100000,
            "exchange_rate": 1,
        }
        operation_id, _saved = save_calculated_operation(
            self.agent_id,
            self.property_id,
            self.org_id,
            operation,
            status=STATUS_DRAFT,
            created_by_user_id=self.agent_user_id,
            require_property_owner=True,
        )
        return operation_id

    def _upload_required_docs(
        self,
        operation_id,
        *,
        include_agent_invoice=True,
        include_invoice_docs=True,
    ):
        _doc, err = upload_or_replace_vat_document(
            organization_id=self.org_id,
            operation_id=operation_id,
            doc_type="uif_form",
            file_storage=FakeStorage(
                "uif_form.pdf",
                _pdf_bytes(),
            ),
            uploaded_by_user_id=self.agent_user_id,
        )
        self.assertIsNone(err)

        if not include_invoice_docs:
            return

        for doc_type in ("martillero_client",):
            _doc, err = upload_or_replace_vat_document(
                organization_id=self.org_id,
                operation_id=operation_id,
                doc_type=doc_type,
                file_storage=FakeStorage(
                    f"{doc_type}.pdf",
                    _pdf_bytes(),
                ),
                uploaded_by_user_id=self.agent_user_id,
            )
            self.assertIsNone(err)
        if include_agent_invoice:
            _doc, err = upload_or_replace_vat_document(
                organization_id=self.org_id,
                operation_id=operation_id,
                doc_type="agent_client",
                file_storage=FakeStorage(
                    "agent_client.pdf",
                    _pdf_bytes(),
                ),
                uploaded_by_user_id=self.agent_user_id,
            )
            self.assertIsNone(err)

    def test_readiness_blocks_without_documents_when_invoiced(self):
        operation_id = self._create_draft_operation(
            was_invoiced="yes",
            invoice_full_commission="no",
        )

        readiness = validate_operation_readiness(
            operation_id,
            self.org_id,
        )

        self.assertFalse(readiness["is_ready"])
        self.assertIn("uif_form", readiness["missing_keys"])
        self.assertIn("martillero_client", readiness["missing_keys"])
        self.assertIn("agent_client", readiness["missing_keys"])

    def test_readiness_skips_invoice_docs_when_not_invoiced(self):
        operation_id = self._create_draft_operation(
            was_invoiced="no",
            invoice_full_commission="no",
        )

        readiness = validate_operation_readiness(
            operation_id,
            self.org_id,
        )

        self.assertTrue(readiness["is_ready"])
        self.assertNotIn("uif_form", readiness["missing_keys"])
        self.assertNotIn("martillero_client", readiness["missing_keys"])
        self.assertNotIn("agent_client", readiness["missing_keys"])

        required_keys = {
            item["key"]
            for item in readiness["requirements"]
            if item["required"]
        }
        self.assertNotIn("uif_form", required_keys)
        self.assertNotIn("martillero_client", required_keys)
        self.assertNotIn("agent_client", required_keys)

    def test_readiness_clears_invoice_docs_after_switching_to_not_invoiced(
        self,
    ):
        operation_id = self._create_draft_operation(
            was_invoiced="yes",
            invoice_full_commission="no",
        )

        readiness_invoiced = validate_operation_readiness(
            operation_id,
            self.org_id,
        )
        self.assertIn(
            "uif_form",
            readiness_invoiced["missing_keys"],
        )
        self.assertIn(
            "martillero_client",
            readiness_invoiced["missing_keys"],
        )
        self.assertIn(
            "agent_client",
            readiness_invoiced["missing_keys"],
        )

        readiness_not_invoiced = validate_operation_readiness(
            operation_id,
            self.org_id,
            pending_values={
                "was_invoiced": "no",
                "vat_amount": 0,
            },
        )

        self.assertNotIn(
            "uif_form",
            readiness_not_invoiced["missing_keys"],
        )
        self.assertNotIn(
            "martillero_client",
            readiness_not_invoiced["missing_keys"],
        )
        self.assertNotIn(
            "agent_client",
            readiness_not_invoiced["missing_keys"],
        )

    def test_readiness_requires_agent_invoice_when_split_billing(self):
        operation_id = self._create_draft_operation(
            was_invoiced="yes",
            invoice_full_commission="no",
        )
        self._upload_required_docs(
            operation_id,
            include_agent_invoice=False,
        )

        readiness = validate_operation_readiness(
            operation_id,
            self.org_id,
        )

        self.assertFalse(readiness["is_ready"])
        self.assertIn("agent_client", readiness["missing_keys"])

    def test_readiness_skips_agent_invoice_when_full_commission(self):
        operation_id = self._create_draft_operation(
            was_invoiced="yes",
            invoice_full_commission="yes",
        )
        self._upload_required_docs(
            operation_id,
            include_agent_invoice=False,
        )

        readiness = validate_operation_readiness(
            operation_id,
            self.org_id,
        )

        self.assertTrue(readiness["is_ready"])

    def test_submit_raises_when_not_ready(self):
        operation_id = self._create_draft_operation(
            was_invoiced="yes",
            invoice_full_commission="no",
        )

        with self.assertRaises(OperationNotReadyError):
            submit_operation_for_approval(
                operation_id,
                self.org_id,
            )

    def test_submit_succeeds_when_ready(self):
        operation_id = self._create_draft_operation(
            was_invoiced="no",
            invoice_full_commission="yes",
        )

        submit_operation_for_approval(
            operation_id,
            self.org_id,
        )

        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT status FROM operations WHERE id = ?",
            (operation_id,),
        )
        status = cursor.fetchone()[0]
        connection.close()

        self.assertEqual(status, "pending")

    def test_property_without_type_is_not_ready(self):
        property_id = add_property(
            "Sin tipo 456",
            "CABA",
            self.org_id,
            agent_id=self.agent_id,
            property_type=None,
        )
        operation = {
            "date": "02/01/2026",
            "agent": "Agent R",
            "agent_type": "Alto",
            "property": "Sin tipo 456",
            "jurisdiction": "CABA",
            "was_invoiced": "no",
            "invoice_full_commission": "yes",
            "vat_amount": 0,
            "sale_price": 50000,
            "commission_rate": 3,
            "total_commission": 1500,
            "commission_after_abao": 1500,
            "abao": 0,
            "martillero": 60,
            "agent_payment": 864,
            "office_payment": 576,
            "office_total": 576,
            "currency": "USD",
            "original_amount": 50000,
            "exchange_rate": 1,
        }
        operation_id, _saved = save_calculated_operation(
            self.agent_id,
            property_id,
            self.org_id,
            operation,
            status=STATUS_DRAFT,
            created_by_user_id=self.agent_user_id,
            require_property_owner=True,
        )

        readiness = validate_operation_readiness(
            operation_id,
            self.org_id,
        )

        self.assertFalse(readiness["is_ready"])
        self.assertIn("property", readiness["missing_keys"])


if __name__ == "__main__":
    unittest.main()
