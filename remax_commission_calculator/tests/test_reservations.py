"""Reservations: permissions, cancel, documents, operations, and the matcher."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_reservations.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_tasks import complete_task, create_task
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.operations_repository import get_operation_record
from modules.database.properties_repository import get_property_record
from modules.database.reservations_repository import (
    get_open_reservation_for_property,
    get_reservation,
    get_reservation_document,
)
from modules.organization_time import UTC, organization_timezone
from modules.property_inventory import is_commercially_available
from modules.property_match import passes_hard_filters
from modules.reservations import (
    add_note,
    cancel_reservation,
    link_operation,
    list_visible_reservations,
    load_reservation_detail,
    sync_reservation_from_operation,
)
from modules.visit_close import apply_visit_close
from web_app import app


class ReservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="reservations-test")
        create_tables()
        cls.org = add_organization("Reservas Org")
        cls.other_org = add_organization("Reservas Other")
        cls.agent = add_agent("Agente Reserva", "Alto", cls.org)
        cls.other_agent = add_agent("Otro Agente", "Alto", cls.org)
        cls.admin = add_user(
            "reserva_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            cls.org,
        )
        cls.agent_user = add_user(
            "reserva_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.other_user = add_user(
            "reserva_other",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.tz = organization_timezone(cls.org)

    def _now(self):
        return datetime(2026, 9, 24, 15, 0, tzinfo=self.tz).astimezone(UTC)

    def _reserve(self, address="Calle Reserva 10", *, purpose="sale", agent=None, org=None):
        organization_id = org or self.org
        agent_id = agent or self.agent
        property_id = add_property(
            address,
            "CABA",
            organization_id,
            agent_id=agent_id,
            listing_price=185000,
            listing_currency="USD",
            listing_purpose=purpose,
        )
        contact = create_agent_contact(
            organization_id,
            agent_id,
            {"name": address, "phone": "1155550000", "status": "lead", "source": "manual"},
        )
        visit = create_task(
            organization_id,
            agent_id,
            {
                "title": address,
                "task_type": "visit",
                "due_date": "2026-09-24",
                "due_time": "11:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "property_id": property_id,
            },
            created_by_user_id=self.agent_user,
        )
        visit = complete_task(
            organization_id,
            visit["id"],
            agent_id=agent_id,
            actor_user_id=self.agent_user,
        )
        apply_visit_close(
            visit,
            {
                "result": "reserved",
                "reservation_amount": "2.000",
                "reservation_currency": "USD",
                "agreed_property_price": "178.000",
                "agreed_property_currency": "USD",
                "payment_method": "cash",
                "next_milestone": "boleto" if purpose == "sale" else "keys",
                "note": "seña",
            },
            organization_id=organization_id,
            agent_id=agent_id,
            actor_user_id=self.agent_user,
            now=self._now(),
            tz=self.tz,
            language="es",
        )
        return property_id, get_open_reservation_for_property(organization_id, property_id)

    def _login(self, client, username):
        client.post(
            "/login",
            data={"username": username, "password": "Password1"},
            follow_redirects=True,
        )

    def test_agent_sees_only_own_reservations_and_cannot_edit(self):
        _own_property, own = self._reserve("Calle Propia")
        _other_property, other = self._reserve("Calle Ajena", agent=self.other_agent)
        visible = list_visible_reservations(self.org, agent_id=self.agent)
        self.assertEqual([row["id"] for row in visible], [own["id"]])
        self.assertIsNone(load_reservation_detail(self.org, other["id"], agent_id=self.agent))
        client = app.test_client()
        self._login(client, "reserva_agent")
        page = client.get("/reservations")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Calle Propia", body)
        self.assertNotIn("Calle Ajena", body)
        denied = client.post(f"/reservations/{own['id']}/notes", data={"body": "no"})
        self.assertEqual(denied.status_code, 302)
        self.assertNotIn("/reservations/", denied.headers.get("Location") or "")
        self.assertEqual(load_reservation_detail(self.org, own["id"])["notes"], [])

    def test_staff_can_edit_and_notes_keep_the_actor(self):
        _property_id, reservation = self._reserve("Calle Staff")
        client = app.test_client()
        self._login(client, "reserva_admin")
        updated = client.post(
            f"/reservations/{reservation['id']}",
            data={
                "reservation_status": "documentation",
                "payment_method": "mortgage",
                "next_milestone": "deed",
                "estimated_closing_date": "2026-10-15",
                "notes": "en documentación",
            },
            follow_redirects=True,
        )
        self.assertEqual(updated.status_code, 200)
        stored = get_reservation(reservation["id"], self.org)
        self.assertEqual(stored["reservation_status"], "documentation")
        self.assertEqual(stored["next_milestone"], "deed")
        note = client.post(
            f"/reservations/{reservation['id']}/notes",
            data={"body": "30/09 - Reserva tomada USD 2.000"},
            follow_redirects=True,
        )
        self.assertEqual(note.status_code, 200)
        detail = load_reservation_detail(self.org, reservation["id"])
        self.assertEqual(detail["notes"][-1]["body"], "30/09 - Reserva tomada USD 2.000")
        self.assertEqual(detail["notes"][-1]["actor_user_id"], self.admin)
        self.assertTrue(detail["notes"][-1]["created_at"])
        agent = app.test_client()
        self._login(agent, "reserva_agent")
        readable = agent.get(f"/reservations/{reservation['id']}")
        self.assertIn("30/09 - Reserva tomada USD 2.000", readable.get_data(as_text=True))

    def test_cancel_restores_or_keeps_the_price_and_audits_the_choice(self):
        restore_property, restore = self._reserve("Calle Restaurar")
        cancel_reservation(
            self.org,
            restore["id"],
            actor_user_id=self.admin,
            keep_negotiated_price=False,
        )
        restored = get_property_record(restore_property, self.org)
        self.assertEqual(restored["commercial_status"], "available")
        self.assertEqual(int(restored["listing_price"]), 185000)
        audit = get_reservation(restore["id"], self.org)
        self.assertEqual(audit["reservation_status"], "cancelled")
        self.assertEqual(audit["price_decision"], "restore_original")
        self.assertEqual(audit["cancelled_by_user_id"], self.admin)
        self.assertTrue(audit["cancelled_at"])

        keep_property, kept = self._reserve("Calle Negociada")
        cancel_reservation(
            self.org,
            kept["id"],
            actor_user_id=self.admin,
            keep_negotiated_price=True,
        )
        row = get_property_record(keep_property, self.org)
        self.assertEqual(row["commercial_status"], "available")
        self.assertEqual(int(row["listing_price"]), 178000)
        decision = get_reservation(kept["id"], self.org)
        self.assertEqual(decision["price_decision"], "keep_agreed")
        self.assertEqual(decision["cancelled_by_user_id"], self.admin)

    def test_documents_are_optional_and_isolated(self):
        _property_id, reservation = self._reserve("Calle Docs")
        detail = load_reservation_detail(self.org, reservation["id"])
        self.assertEqual(detail["documents"], [])
        upload = BytesIO(b"receipt")
        upload.filename = "sena.pdf"
        upload.mimetype = "application/pdf"
        from modules.reservations import save_document

        document_id = save_document(
            self.org,
            reservation["id"],
            upload,
            "reservation_receipt",
            actor_user_id=self.admin,
        )
        self.assertIsNone(get_reservation_document(self.other_org, document_id))
        client = app.test_client()
        self._login(client, "reserva_agent")
        downloaded = client.get(
            f"/reservations/{reservation['id']}/documents/{document_id}"
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn(b"receipt", downloaded.data)

    def test_prepare_operation_uses_the_existing_flow_and_reads_its_commission(self):
        property_id, reservation = self._reserve("Calle Operacion")
        client = app.test_client()
        self._login(client, "reserva_admin")
        form = client.get(f"/operations/new?reservation_id={reservation['id']}")
        html = form.get_data(as_text=True)
        self.assertIn(f'name="reservation_id" value="{reservation["id"]}"', html)
        self.assertIn('value="178000"', html)
        saved = client.post(
            "/operations/new",
            data={
                "action": "save",
                "operation_date": "01/01/2026",
                "search_mode": "agent",
                "agent_id": str(self.agent),
                "property_id": str(property_id),
                "reservation_id": str(reservation["id"]),
                "currency": "USD",
                "original_amount": "178000",
                "exchange_rate": "",
                "seller_side_active": "1",
                "buyer_side_active": "1",
                "is_referred": "",
                "referred_side": "",
                "seller_commission_rate": "2.5",
                "buyer_commission_rate": "3",
                "seller_vat_amount": "0",
                "buyer_vat_amount": "0",
            },
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 302)
        linked = get_reservation(reservation["id"], self.org)
        self.assertEqual(linked["reservation_status"], "in_operation")
        self.assertIsNotNone(linked["operation_id"])
        operation = get_operation_record(linked["operation_id"], self.org)
        detail = load_reservation_detail(self.org, reservation["id"])
        self.assertEqual(detail["operation"]["total_commission"], operation["total_commission"])
        self.assertEqual(detail["operation"]["agent_payment"], operation["agent_payment"])
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE operations SET was_invoiced = ? WHERE id = ? AND organization_id = ?",
                ("yes", linked["operation_id"], self.org),
            )
            connection.commit()
        finally:
            connection.close()
        sync_reservation_from_operation(self.org, linked["operation_id"])
        closed = get_reservation(reservation["id"], self.org)
        self.assertEqual(closed["reservation_status"], "closed")
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "sold")

        rent_property, rent = self._reserve("Calle Alquiler", purpose="rental")
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO operations (
                    operation_date, agent_id, property_id, organization_id,
                    was_invoiced, vat_amount, sale_price, commission_rate,
                    total_commission, commission_after_abao, abao, martillero,
                    agent_payment, office_payment, office_total, currency,
                    original_amount, exchange_rate, status, created_by_user_id
                ) VALUES (
                    '01/01/2026', ?, ?, ?, 'yes', 0, 178000, 5, 8900, 8900, 0, 0,
                    5000, 3900, 3900, 'USD', 178000, 1, 'approved', ?
                )
                """,
                (self.agent, rent_property, self.org, self.admin),
            )
            rent_operation = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()
        link_operation(self.org, rent["id"], rent_operation, actor_user_id=self.admin)
        sync_reservation_from_operation(self.org, rent_operation)
        self.assertEqual(get_reservation(rent["id"], self.org)["reservation_status"], "closed")
        self.assertEqual(get_property_record(rent_property, self.org)["commercial_status"], "rented")

    def test_matcher_does_not_offer_reserved_properties(self):
        self.assertFalse(
            is_commercially_available(
                {"status": "approved", "commercial_status": "reserved"}
            )
        )
        self.assertFalse(
            passes_hard_filters(
                {},
                {"source": "portal", "commercial_status": "reserved"},
                {},
            )
        )
        self.assertFalse(
            passes_hard_filters(
                {},
                {"source": "internal"},
                {"status": "approved", "commercial_status": "reserved"},
            )
        )

    def test_other_organization_cannot_read_the_reservation(self):
        _property_id, reservation = self._reserve("Calle Aislada")
        self.assertIsNone(get_reservation(reservation["id"], self.other_org))
        self.assertEqual(list_visible_reservations(self.other_org), [])
        add_note(self.org, reservation["id"], "interna", actor_user_id=self.admin)
        self.assertIsNone(load_reservation_detail(self.other_org, reservation["id"]))

    def test_board_is_a_read_model_and_does_not_query_per_row(self):
        from datetime import date

        from modules.database import property_media_repository
        from modules.database.reservations_repository import update_reservation_fields
        from modules.reservation_board import (
            build_reservation_board,
            present_reservation,
            property_cover_url,
        )
        from modules import reservation_board

        today = date(2026, 9, 30)
        self.assertIsNone(
            property_cover_url(
                {"source": "acm", "original_url": "https://photos.example/comp.jpg"},
                1,
            )
        )
        pending = present_reservation(
            {
                "reservation_status": "reserved",
                "listing_purpose": "sale",
                "next_milestone": "boleto",
                "operation_id": None,
                "estimated_closing_date": None,
            },
            today=today,
        )
        self.assertEqual(pending["commission_state"], "pending_operation")
        self.assertEqual(pending["commission_agent_label"], "")
        self.assertEqual(pending["close_display"], "")
        self.assertNotIn("milestone_date", pending)
        self.assertIn("reservation_gap_prepare", pending["gaps"])
        self.assertIn("reservation_gap_boleto", pending["gaps"])
        self.assertIn("reservation_gap_no_close", pending["gaps"])

        linked = present_reservation(
            {
                "reservation_status": "in_operation",
                "listing_purpose": "sale",
                "next_milestone": "deed",
                "operation_id": 8,
                "estimated_closing_date": "2026-10-10",
            },
            operation={
                "sale_price": 178000,
                "total_commission": 8900,
                "agent_payment": 5000,
                "currency": "USD",
                "was_invoiced": "no",
            },
            today=today,
        )
        self.assertEqual(linked["commission_total_label"], "USD 8.900")
        self.assertEqual(linked["commission_agent_label"], "USD 5.000")
        self.assertEqual(linked["commission_state"], "unpaid")
        self.assertEqual(linked["close_display"], "10/10/2026")
        self.assertTrue(linked["closing_soon"])
        self.assertFalse(linked["delayed"])

        overdue = present_reservation(
            {
                "reservation_status": "financing",
                "listing_purpose": "sale",
                "estimated_closing_date": "2026-09-01",
                "next_milestone": "mortgage",
                "operation_id": None,
            },
            today=today,
        )
        self.assertTrue(overdue["delayed"])
        self.assertIn("reservation_alert_close_overdue", overdue["alerts"])
        self.assertIn("reservation_gap_financing", overdue["gaps"])

        _property_id, pesos = self._reserve("Calle Pesos")
        update_reservation_fields(
            self.org,
            pesos["id"],
            {
                "agreed_currency": "ARS",
                "agreed_property_price": 1000000,
                "reservation_currency": "ARS",
                "reservation_amount": 250000,
                "estimated_closing_date": "2026-10-08",
                "updated_at": "2026-09-30T12:00:00",
            },
        )
        board = build_reservation_board(self.org, today=today)
        agreed = {line["currency"]: line["label"] for line in board["kpis"]["agreed"]}
        deposits = {line["currency"]: line["label"] for line in board["kpis"]["deposits"]}
        self.assertIn("USD", agreed["USD"])
        self.assertNotIn("ARS", agreed["USD"])
        self.assertEqual(agreed["ARS"], "ARS 1.000.000")
        self.assertEqual(deposits["ARS"], "ARS 250.000")
        visible_ids = [row["id"] for row in board["rows"]]
        self.assertEqual(len(visible_ids), len(set(visible_ids)))
        self.assertIn(pesos["id"], visible_ids)

        def count_reads():
            calls = []
            modules = (
                reservation_board,
                property_media_repository,
                __import__(
                    "modules.database.reservations_repository",
                    fromlist=["get_connection"],
                ),
            )
            originals = {module: module.get_connection for module in modules}

            def wrapped(real):
                def open_connection():
                    connection = real()
                    original_execute = connection.execute
                    original_cursor = connection.cursor

                    def execute(sql, parameters=None):
                        calls.append(sql)
                        if parameters is None:
                            return original_execute(sql)
                        return original_execute(sql, parameters)

                    def cursor(*args, **kwargs):
                        current = original_cursor(*args, **kwargs)
                        current_execute = current.execute

                        def cursor_execute(sql, parameters=None):
                            calls.append(sql)
                            if parameters is None:
                                return current_execute(sql)
                            return current_execute(sql, parameters)

                        current.execute = cursor_execute
                        return current

                    connection.execute = execute
                    connection.cursor = cursor
                    return connection

                return open_connection

            for module in modules:
                module.get_connection = wrapped(originals[module])
            try:
                build_reservation_board(self.org, today=today)
            finally:
                for module, original in originals.items():
                    module.get_connection = original
            return len(calls)

        _first_property, first = self._reserve("Calle Lote Uno")
        update_reservation_fields(
            self.org,
            first["id"],
            {"operation_id": 910001, "updated_at": "2026-09-30T12:00:00"},
        )
        one_row = count_reads()
        for index in (2, 3):
            _extra_property, extra = self._reserve(f"Calle Lote {index}")
            update_reservation_fields(
                self.org,
                extra["id"],
                {"operation_id": 910000 + index, "updated_at": "2026-09-30T12:00:00"},
            )
        many_rows = count_reads()
        self.assertEqual(one_row, many_rows)
        self.assertEqual(many_rows, 3)

        client = app.test_client()
        self._login(client, "reserva_admin")
        staff = client.get("/reservations")
        staff_html = staff.get_data(as_text=True)
        self.assertEqual(staff.status_code, 200)
        self.assertNotIn("Nueva reserva", staff_html)
        self.assertIn("Reservas activas", staff_html)
        self.assertIn("Demoradas", staff_html)
        self.assertIn("USD acordado", staff_html)
        self.assertIn("Señas ARS", staff_html)
        self.assertIn("data-res-drawer", staff_html)
        self.assertIn("data-res-agent-input", staff_html)
        detail = client.get(f"/reservations/{pesos['id']}")
        detail_html = detail.get_data(as_text=True)
        self.assertIn("Qué falta para cerrar", detail_html)
        self.assertIn("Editar reserva", detail_html)
        self.assertIn("Boleto", detail_html)
        self.assertIn("08/10/2026", detail_html)

        agent = app.test_client()
        self._login(agent, "reserva_agent")
        mine = agent.get("/reservations")
        mine_html = mine.get_data(as_text=True)
        self.assertIn("Mis reservas", mine_html)
        self.assertNotIn("Nueva reserva", mine_html)
        self.assertNotIn('name="reservation_status"', mine_html)
        self.assertNotIn("Cancelar reserva", mine_html)
        own_detail = agent.get(f"/reservations/{pesos['id']}")
        own_html = own_detail.get_data(as_text=True)
        self.assertIn("Qué falta para cerrar", own_html)
        self.assertNotIn("Editar reserva", own_html)
        self.assertNotIn("Cancelar reserva", own_html)

