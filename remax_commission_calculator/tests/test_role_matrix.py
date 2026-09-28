"""Authorization against existing records. Permissions stay as implemented.

HTML 403 becomes a redirect to ``/`` via ``handle_forbidden``.
A cross-organization id is looked up inside the caller's organization, so
the response is 404 and the other office's row stays unchanged.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = ""
os.environ["APP_ENV"] = "development"
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "roles.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["OPENAI_API_KEY"] = "sk-role-matrix-secret"
os.environ["INVOICE_PROVIDER"] = "arca"
os.environ["ARCA_ENV"] = "homologation"

from modules.access_codes import hash_access_secret
from modules.agent_account import create_movement
from modules.agent_account_charges import VAT_MODE_ADD
from modules.agent_tasks import create_task
from modules.arca.config import get_arca_environment
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact, load_contact
from modules.database import (
    add_agent,
    add_operation,
    add_property,
    add_user,
    create_tables,
    ensure_parties_for_operation,
    get_invoice,
    get_operation_record,
    get_property_record,
    set_operation_party_client_fields,
    upsert_agent_billing_profile,
    upsert_billing_issuer_profile,
)
from modules.database.agent_billing_profiles_repository import (
    get_by_agent as get_agent_billing_profile,
)
from modules.database.agent_tasks_repository import get_agent_task
from modules.database.arca_connections_repository import get_arca_connection
from modules.database.billing_issuer_profiles_repository import (
    get_profile as get_billing_issuer_profile,
)
from modules.database.connection import get_connection
from modules.database.guest_access_repository import (
    create_guest_access,
    get_guest_access_by_token_hash,
)
from modules.database.organizations_repository import add_organization
from modules.database.treasury_accounts_repository import (
    create_treasury_account,
    get_treasury_account,
)
from modules.invoicing import (
    ISSUER_MODE_AGENT,
    ISSUER_MODE_OFFICE,
    SIDE_BUYER,
    confirm_draft,
    create_draft_for_charge,
    create_draft_for_side,
    set_party_invoice_amount,
)
from web_app import app


class RoleMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()
        password = hash_password("Password1")

        cls.org = add_organization("Role Matrix Org")
        cls.org_b = add_organization("Role Matrix Org B")
        cls.agent = add_agent("Owner Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Other Agent", "Puro", cls.org)
        cls.agent_b = add_agent("Org B Agent", "Alto", cls.org_b)

        cls.admin = add_user(
            "role_admin",
            password,
            ROLE_ADMIN,
            cls.org,
            email="role_admin@example.com",
        )
        cls.owner_user = add_user(
            "role_owner",
            password,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
            email="role_owner@example.com",
        )
        cls.other_user = add_user(
            "role_other",
            password,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
            email="role_other@example.com",
        )
        cls.admin_b = add_user(
            "role_admin_b",
            password,
            ROLE_ADMIN,
            cls.org_b,
            email="role_admin_b@example.com",
        )
        cls.guest_hash = hash_access_secret("role-guest")
        create_guest_access(
            cls.org,
            cls.guest_hash,
            cls.admin,
            label="role-guest",
        )

        cls.owner = {
            "id": cls.owner_user,
            "role": ROLE_AGENT,
            "agent_id": cls.agent,
        }
        cls.admin_actor = {
            "id": cls.admin,
            "role": ROLE_ADMIN,
            "agent_id": None,
        }

        cls.pending_property = add_property(
            "Pendiente 100",
            "CABA",
            cls.org,
            agent_id=cls.agent,
            status="pending",
            property_type="apartment",
            listing_purpose="sale",
        )
        cls.pending_property_b = add_property(
            "Pendiente B 100",
            "CABA",
            cls.org_b,
            agent_id=cls.agent_b,
            status="pending",
            property_type="apartment",
            listing_purpose="sale",
        )
        cls.editable_property = add_property(
            "Editable 200",
            "CABA",
            cls.org,
            agent_id=cls.agent,
            status="approved",
            property_type="apartment",
            listing_purpose="sale",
            neighborhood="Belgrano",
            listing_currency="USD",
            listing_price=180000,
        )
        cls.editable_property_b = add_property(
            "Editable B 200",
            "CABA",
            cls.org_b,
            agent_id=cls.agent_b,
            status="approved",
            property_type="apartment",
            listing_purpose="sale",
            neighborhood="Palermo",
        )

        cls.contact = create_agent_contact(
            cls.org,
            cls.agent,
            {"name": "Contacto Dueño", "status": "lead", "source": "manual"},
        )
        cls.contact_b = create_agent_contact(
            cls.org_b,
            cls.agent_b,
            {"name": "Contacto B", "status": "lead", "source": "manual"},
        )

        cls.task = create_task(
            cls.org,
            cls.agent,
            {
                "title": "Llamar al dueño",
                "task_type": "call",
                "priority": "normal",
                "due_date": "2026-10-01",
                "due_time": "10:00",
            },
            created_by_user_id=cls.owner_user,
        )
        cls.task_b = create_task(
            cls.org_b,
            cls.agent_b,
            {
                "title": "Llamar oficina B",
                "task_type": "call",
                "priority": "normal",
                "due_date": "2026-10-01",
                "due_time": "11:00",
            },
            created_by_user_id=cls.admin_b,
        )

        cls.draft_operation = cls._operation(
            cls.org, cls.agent, cls.editable_property, status="draft"
        )
        cls.pending_operation = cls._operation(
            cls.org, cls.agent, cls.editable_property, status="pending"
        )
        cls.pending_operation_b = cls._operation(
            cls.org_b, cls.agent_b, cls.editable_property_b, status="pending"
        )

        upsert_agent_billing_profile(
            cls.org,
            cls.agent,
            legal_name="Owner Agent",
            tax_id="20-30123456-7",
            tax_condition="monotributo",
            fiscal_address="Domicilio agente",
            email="role_owner@example.com",
        )
        cls.issuer = upsert_billing_issuer_profile(
            cls.org,
            issuer_type="organization",
            display_name="Oficina",
            legal_name="Oficina Legal SA",
            tax_id="30-71234567-8",
            tax_condition="responsable_inscripto",
            fiscal_address="Oficina 1",
            email="oficina@example.com",
            is_default=True,
        )
        cls.issuer_b = upsert_billing_issuer_profile(
            cls.org_b,
            issuer_type="organization",
            display_name="Oficina B",
            legal_name="Oficina B SA",
            tax_id="30-70000000-1",
            tax_condition="responsable_inscripto",
            fiscal_address="Oficina B 1",
            email="oficinab@example.com",
            is_default=True,
        )
        cls.invoice = cls._ready_invoice(cls.org, cls.pending_operation, cls.owner)
        cls.invoice_b = cls._ready_invoice(
            cls.org_b,
            cls.pending_operation_b,
            {"id": cls.admin_b, "role": ROLE_ADMIN, "agent_id": None},
        )
        charge = create_movement(
            cls.org,
            cls.agent,
            {
                "charge_category": "fee",
                "currency": "USD",
                "amount": "65",
                "vat_mode": VAT_MODE_ADD,
                "vat_rate": "21",
                "billing_period": "Septiembre 2026",
                "movement_date": "2026-09-01",
            },
            created_by_user_id=cls.admin,
        )
        charge_draft = create_draft_for_charge(
            cls.org,
            charge["id"],
            cls.admin_actor,
            issuer_mode=ISSUER_MODE_OFFICE,
            issuer_profile_id=cls.issuer["id"],
        )
        cls.charge_invoice = confirm_draft(
            cls.org,
            charge_draft["id"],
            cls.admin_actor,
        )

        cls.treasury = create_treasury_account(
            cls.org,
            name="Caja roles",
            account_type="cash",
            currency="ARS",
            created_by_user_id=cls.admin,
        )
        cls.treasury_b = create_treasury_account(
            cls.org_b,
            name="Caja B",
            account_type="cash",
            currency="ARS",
            created_by_user_id=cls.admin_b,
        )

    @classmethod
    def tearDownClass(cls):
        _TMP.cleanup()

    @classmethod
    def _operation(cls, organization_id, agent_id, property_id, *, status):
        return add_operation(
            "15/09/2026",
            agent_id,
            property_id,
            "no",
            0,
            200000,
            3,
            6000,
            5400,
            600,
            2700,
            2700,
            0,
            2700,
            organization_id,
            status=status,
            created_by_user_id=cls.admin if organization_id == cls.org else cls.admin_b,
        )

    @classmethod
    def _ready_invoice(cls, organization_id, operation_id, user):
        ensure_parties_for_operation(organization_id, operation_id)
        set_operation_party_client_fields(
            organization_id,
            operation_id,
            SIDE_BUYER,
            client_legal_name="Cliente Real",
            client_tax_id="20-99887766-5",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Cliente 123",
            client_email="cliente@example.com",
        )
        set_party_invoice_amount(
            organization_id,
            operation_id,
            SIDE_BUYER,
            "1000",
            "ARS",
            "1",
            user["id"],
            notify=False,
        )
        draft = create_draft_for_side(
            organization_id,
            operation_id,
            SIDE_BUYER,
            user,
            issuer_mode=ISSUER_MODE_AGENT if user.get("agent_id") else ISSUER_MODE_OFFICE,
            issuer_profile_id=None if user.get("agent_id") else (
                cls.issuer["id"] if organization_id == cls.org else cls.issuer_b["id"]
            ),
        )
        return confirm_draft(organization_id, draft["id"], user)

    def _client(self, role):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess.clear()
            sess["language"] = "es"
            if role == "guest":
                access = get_guest_access_by_token_hash(self.guest_hash)
                sess["guest_access_id"] = access["id"]
                sess["guest_organization_id"] = access["organization_id"]
                sess["guest_token_hash"] = self.guest_hash
            elif role == "admin":
                sess["user_id"] = self.admin
            elif role == "owner":
                sess["user_id"] = self.owner_user
            elif role == "other":
                sess["user_id"] = self.other_user
            elif role == "org_b":
                sess["user_id"] = self.admin_b
        return client

    def _post(self, role, path, data=None):
        return self._client(role).post(path, data=data or {})

    def _location(self, response):
        if response.status_code != 302:
            return ""
        return urlparse(response.location).path

    def _assert_denied_home(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._location(response), "/")

    def _property_form(self, **extra):
        data = {
            "address": "Editable 200",
            "jurisdiction": "CABA",
            "property_type": "apartment",
            "listing_price": "180000",
            "listing_purpose": "sale",
            "agent_id": str(self.agent),
            "listing_currency": "USD",
            "commercial_status": "available",
            "neighborhood": "Belgrano",
        }
        data.update(extra)
        return data

    def _contact_name(self, organization_id, contact_id, agent_id):
        return load_contact(
            organization_id,
            contact_id,
            agent_id=agent_id,
        )["name"]

    def test_existing_property_approval_and_edit(self):
        approve = f"/approvals/properties/{self.pending_property}/approve"
        for role in ("owner", "other", "guest"):
            with self.subTest(action="approve", role=role):
                self._assert_denied_home(self._post(role, approve))
                self.assertEqual(
                    get_property_record(self.pending_property, self.org)["status"],
                    "pending",
                )
        cross = self._post("org_b", approve)
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(
            get_property_record(self.pending_property, self.org)["status"],
            "pending",
        )
        self.assertEqual(
            get_property_record(self.pending_property_b, self.org_b)["status"],
            "pending",
        )
        blocked_b = self._post(
            "admin",
            f"/approvals/properties/{self.pending_property_b}/approve",
        )
        self.assertEqual(blocked_b.status_code, 404)
        self.assertEqual(
            get_property_record(self.pending_property_b, self.org_b)["status"],
            "pending",
        )

        allowed = self._post("admin", approve)
        self.assertEqual(allowed.status_code, 302)
        self.assertNotEqual(self._location(allowed), "/")
        self.assertEqual(
            get_property_record(self.pending_property, self.org)["status"],
            "approved",
        )

        edit = f"/properties/{self.editable_property}/edit"
        for role in ("other", "guest"):
            with self.subTest(action="edit", role=role):
                self._assert_denied_home(
                    self._post(role, edit, self._property_form(neighborhood="Nunez"))
                )
                self.assertEqual(
                    get_property_record(self.editable_property, self.org)["neighborhood"],
                    "Belgrano",
                )
        cross_edit = self._post(
            "org_b",
            edit,
            self._property_form(neighborhood="Nunez"),
        )
        self.assertEqual(cross_edit.status_code, 404)
        self.assertEqual(
            get_property_record(self.editable_property, self.org)["neighborhood"],
            "Belgrano",
        )
        blocked_edit = self._post(
            "admin",
            f"/properties/{self.editable_property_b}/edit",
            self._property_form(neighborhood="Nunez"),
        )
        self.assertEqual(blocked_edit.status_code, 404)
        self.assertEqual(
            get_property_record(self.editable_property_b, self.org_b)["neighborhood"],
            "Palermo",
        )

        owner_edit = self._post(
            "owner",
            edit,
            self._property_form(neighborhood="Palermo"),
        )
        self.assertEqual(owner_edit.status_code, 302)
        self.assertEqual(
            get_property_record(self.editable_property, self.org)["neighborhood"],
            "Palermo",
        )
        admin_edit = self._post(
            "admin",
            edit,
            self._property_form(neighborhood="Palermo", rooms="4"),
        )
        self.assertEqual(admin_edit.status_code, 302)
        self.assertEqual(
            get_property_record(self.editable_property, self.org)["rooms"],
            4,
        )

    def test_existing_contact_edit(self):
        path = f"/contacts/{self.contact['id']}/edit"
        payload = {"name": "Contacto Cambiado", "status": "lead", "source": "manual"}
        for role in ("admin", "other", "guest"):
            with self.subTest(role=role):
                response = self._post(role, path, payload)
                if role == "other":
                    self.assertEqual(response.status_code, 404)
                else:
                    self._assert_denied_home(response)
                self.assertEqual(
                    self._contact_name(self.org, self.contact["id"], self.agent),
                    "Contacto Dueño",
                )
        cross = self._post("org_b", path, payload)
        self._assert_denied_home(cross)
        self.assertEqual(
            self._contact_name(self.org, self.contact["id"], self.agent),
            "Contacto Dueño",
        )
        blocked = self._post(
            "admin",
            f"/contacts/{self.contact_b['id']}/edit",
            {"name": "No tocar B", "status": "lead", "source": "manual"},
        )
        self._assert_denied_home(blocked)
        self.assertEqual(
            self._contact_name(self.org_b, self.contact_b["id"], self.agent_b),
            "Contacto B",
        )

        allowed = self._post("owner", path, payload)
        self.assertEqual(allowed.status_code, 302)
        self.assertIn(f"/contacts/{self.contact['id']}", self._location(allowed))
        self.assertEqual(
            self._contact_name(self.org, self.contact["id"], self.agent),
            "Contacto Cambiado",
        )

    def test_existing_task_complete(self):
        path = f"/agenda/{self.task['id']}/complete"
        for role in ("admin", "other", "guest"):
            with self.subTest(role=role):
                response = self._post(role, path)
                if role == "other":
                    self.assertNotEqual(
                        self._location(response),
                        f"/agenda/{self.task['id']}/visit-close",
                    )
                else:
                    self._assert_denied_home(response)
                self.assertEqual(
                    get_agent_task(self.task["id"], self.org)["status"],
                    "pending",
                )
        cross = self._post("org_b", path)
        self.assertEqual(
            get_agent_task(self.task["id"], self.org)["status"],
            "pending",
        )
        blocked = self._post("owner", f"/agenda/{self.task_b['id']}/complete")
        self.assertEqual(
            get_agent_task(self.task_b["id"], self.org_b)["status"],
            "pending",
        )
        self.assertNotEqual(blocked.status_code, 200)

        allowed = self._post("owner", path)
        self.assertEqual(allowed.status_code, 302)
        self.assertNotEqual(self._location(allowed), "/")
        self.assertEqual(
            get_agent_task(self.task["id"], self.org)["status"],
            "completed",
        )

    def test_existing_operation_submit_and_approve(self):
        submit = f"/operations/{self.draft_operation}/submit"
        for role in ("admin", "other", "guest"):
            with self.subTest(action="submit", role=role):
                self._assert_denied_home(self._post(role, submit))
                self.assertEqual(
                    get_operation_record(self.draft_operation, self.org)["status"],
                    "draft",
                )
        cross_submit = self._post(
            "org_b",
            f"/operations/{self.draft_operation}/submit",
        )
        self._assert_denied_home(cross_submit)
        self.assertEqual(
            get_operation_record(self.draft_operation, self.org)["status"],
            "draft",
        )

        submitted = self._post("owner", submit)
        self.assertEqual(submitted.status_code, 302)
        self.assertNotEqual(self._location(submitted), "/")
        self.assertEqual(
            get_operation_record(self.draft_operation, self.org)["status"],
            "pending",
        )

        approve = f"/operations/{self.pending_operation}/approve"
        for role in ("owner", "other", "guest"):
            with self.subTest(action="approve", role=role):
                self._assert_denied_home(self._post(role, approve))
                self.assertEqual(
                    get_operation_record(self.pending_operation, self.org)["status"],
                    "pending",
                )
        cross = self._post("org_b", approve)
        self.assertEqual(cross.status_code, 404)
        blocked = self._post(
            "admin",
            f"/operations/{self.pending_operation_b}/approve",
        )
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(
            get_operation_record(self.pending_operation_b, self.org_b)["status"],
            "pending",
        )

        allowed = self._post("admin", approve)
        self.assertEqual(allowed.status_code, 302)
        self.assertNotEqual(self._location(allowed), "/")
        self.assertEqual(
            get_operation_record(self.pending_operation, self.org)["status"],
            "approved",
        )

    def _flashes(self, client):
        with client.session_transaction() as sess:
            return [message for _category, message in sess.get("_flashes", [])]

    def test_existing_invoice_issue_arca(self):
        path = f"/billing/{self.invoice['id']}/issue-arca"
        for role in ("admin", "owner"):
            client = self._client(role)
            response = client.post(path)
            self.assertEqual(response.status_code, 302, role)
            self.assertEqual(
                self._location(response),
                f"/billing/{self.invoice['id']}",
            )
            self.assertNotIn("access_denied", self._flashes(client))
            self.assertEqual(
                get_invoice(self.org, self.invoice["id"])["status"],
                "ready_to_issue",
            )

        for role in ("other", "guest"):
            with self.subTest(role=role):
                self._assert_denied_home(self._post(role, path))
                self.assertEqual(
                    get_invoice(self.org, self.invoice["id"])["status"],
                    "ready_to_issue",
                )

        cross = self._post("org_b", path)
        self.assertEqual(cross.status_code, 404)
        blocked = self._post("admin", f"/billing/{self.invoice_b['id']}/issue-arca")
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(
            get_invoice(self.org_b, self.invoice_b["id"])["status"],
            "ready_to_issue",
        )

        charge_path = f"/billing/{self.charge_invoice['id']}/issue-arca"
        owner_client = self._client("owner")
        owner_response = owner_client.post(charge_path)
        self.assertEqual(
            self._location(owner_response),
            f"/billing/{self.charge_invoice['id']}",
        )
        self.assertIn("invoice_err_forbidden", self._flashes(owner_client))
        self.assertEqual(
            get_invoice(self.org, self.charge_invoice["id"])["status"],
            "ready_to_issue",
        )
        admin_client = self._client("admin")
        admin_response = admin_client.post(charge_path)
        self.assertEqual(
            self._location(admin_response),
            f"/billing/{self.charge_invoice['id']}",
        )
        self.assertNotIn("invoice_err_forbidden", self._flashes(admin_client))
        self.assertNotIn("access_denied", self._flashes(admin_client))

    def test_arca_connect_is_personal_for_agents_and_office_for_admins(self):
        environment = get_arca_environment()
        payload = {
            "legal_name": "Agente Fiscal",
            "tax_id": "20-30123456-7",
            "tax_condition": "monotributo",
            "fiscal_address": "Domicilio agente",
            "point_of_sale": "4",
        }
        guest = self._post("guest", "/settings/arca/connect", payload)
        self._assert_denied_home(guest)
        self.assertEqual(
            get_billing_issuer_profile(self.org, self.issuer["id"])["legal_name"],
            "Oficina Legal SA",
        )

        owner = self._post("owner", "/settings/arca/connect", payload)
        self.assertEqual(self._location(owner), "/settings/arca/authorize")
        self.assertEqual(
            get_agent_billing_profile(self.org, self.agent)["legal_name"],
            "Agente Fiscal",
        )
        self.assertEqual(
            get_billing_issuer_profile(self.org, self.issuer["id"])["legal_name"],
            "Oficina Legal SA",
        )
        self.assertIsNotNone(
            get_arca_connection(self.org, self.owner_user, environment=environment)
        )

        admin_payload = dict(payload, legal_name="Oficina Actualizada", point_of_sale="7")
        admin = self._post("admin", "/settings/arca/connect", admin_payload)
        self.assertEqual(self._location(admin), "/settings/arca/authorize")
        self.assertEqual(
            get_billing_issuer_profile(self.org, self.issuer["id"])["legal_name"],
            "Oficina Actualizada",
        )
        self.assertEqual(
            get_agent_billing_profile(self.org, self.agent)["legal_name"],
            "Agente Fiscal",
        )
        self.assertEqual(
            get_arca_connection(self.org, self.admin, environment=environment)[
                "point_of_sale"
            ],
            "7",
        )
        self.assertEqual(
            get_arca_connection(self.org, self.owner_user, environment=environment)[
                "point_of_sale"
            ],
            "4",
        )

    def test_existing_treasury_account_and_cash_movement(self):
        path = f"/cash/treasury-accounts/{self.treasury['id']}"
        for role in ("owner", "other", "guest"):
            with self.subTest(role=role):
                self._assert_denied_home(self._post(role, path, {"action": "deactivate"}))
                self.assertTrue(
                    get_treasury_account(self.treasury["id"], self.org)["is_active"]
                )
        cross = self._post("org_b", path, {"action": "deactivate"})
        self.assertNotEqual(cross.status_code, 200)
        self.assertTrue(
            get_treasury_account(self.treasury["id"], self.org)["is_active"]
        )
        blocked = self._post(
            "admin",
            f"/cash/treasury-accounts/{self.treasury_b['id']}",
            {"action": "deactivate"},
        )
        self.assertNotEqual(blocked.status_code, 200)
        self.assertTrue(
            get_treasury_account(self.treasury_b["id"], self.org_b)["is_active"]
        )

        allowed = self._post("admin", path, {"action": "deactivate"})
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(self._location(allowed), "/cash/treasury-accounts")
        self.assertFalse(
            get_treasury_account(self.treasury["id"], self.org)["is_active"]
        )

        movement = {
            "action": "confirm",
            "movement_type": "income",
            "currency": "ARS",
            "amount": "1500",
            "category": "other_income",
            "description": "Ingreso roles reales",
            "payment_method": "cash",
            "movement_date": "2026-09-20",
        }
        for role in ("owner", "guest"):
            self._assert_denied_home(self._post(role, "/cash/new", movement))
        self.assertEqual(self._movement_count("Ingreso roles reales"), 0)
        created = self._post("admin", "/cash/new", movement)
        self.assertEqual(created.status_code, 302)
        self.assertTrue(self._location(created).startswith("/cash/"))
        self.assertNotEqual(self._location(created), "/")
        self.assertEqual(self._movement_count("Ingreso roles reales"), 1)

    def _movement_count(self, description):
        connection = get_connection()
        count = connection.execute(
            "SELECT COUNT(*) FROM cash_movements WHERE description = ?",
            (description,),
        ).fetchone()[0]
        connection.close()
        return count

    def test_organization_settings_show_integrations_without_secrets(self):
        response = self._client("admin").get("/settings/organization")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Integraciones", html)
        self.assertIn("Configurada", html)
        self.assertIn("Falta configurar", html)
        self.assertNotIn("sk-role-matrix-secret", html)
