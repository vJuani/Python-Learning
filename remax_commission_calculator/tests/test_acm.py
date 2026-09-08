"""FASE 4G — Agent-only ACM (comparative market analysis)."""

from __future__ import annotations

import base64
import os
import re
import tempfile
import unittest
import zlib
from decimal import Decimal
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_acm.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.acm_engine import (
    compute_metrics,
    flag_outliers,
    is_valuation_valid,
    price_per_m2,
    score_comparable,
    subject_quality,
    to_decimal,
)
from modules.acm_service import (
    AcmError,
    add_manual_comparable,
    agent_contact_for_acm,
    create_acm_for_property,
    finalize_acm,
    get_acm_view,
    override_comparable_area,
    preview_acm_property,
    recalculate_acm,
    refresh_draft,
    require_acm_agent,
    set_comparable_selected,
)
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_operation,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.database.properties_repository import (
    STATUS_APPROVED,
    update_property_from_sync,
)
from modules.database.property_acm_migration import migrate_property_acm_sqlite
from modules.database.property_acm_repository import get_acm, list_comparables
from modules.acm_explain import build_acm_facts, explain_acm
from modules.acm_engine import compute_price_scenario
from modules.acm_sources import format_diff_label
from modules.jrh_ai_intents import (
    ACM_EXPLAIN,
    ACM_FILTER_COMPARABLES,
    ACM_PRICE_SCENARIO,
    ACM_REMOVE_COMPARABLE,
    DOWNLOAD_ACM,
    START_ACM,
)
from modules.jrh_ai_service import ask_jrh
from web_app import app


def _pdf_haystack(payload):
    parts = [payload]
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", payload, re.S):
        raw = match.group(1)
        try:
            raw = base64.a85decode(raw, adobe=True, ignorechars=b" \t\r\n")
        except Exception:
            pass
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        parts.append(raw)
    return b"\n".join(parts)


class AcmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="acm-tests")
        create_tables()
        cls.org = add_organization("ACM Org")
        cls.other_org = add_organization("ACM Other")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana ACM", "Alto", cls.org)
        cls.other_agent = add_agent("Otro ACM", "Alto", cls.org)
        cls.foreign_agent = add_agent("Foreign ACM", "Alto", cls.other_org)
        cls.agent_user = add_user(
            "acm_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            email="ana@jrh.test",
            first_name="Ana",
            last_name="ACM",
            phone="1144441111",
        )
        cls.other_user = add_user(
            "acm_other",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.admin = add_user("acm_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent_record = {
            "id": cls.agent_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.agent_id,
        }
        cls.other_record = {
            "id": cls.other_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.other_agent,
        }
        cls.admin_record = {
            "id": cls.admin,
            "role": ROLE_ADMIN,
            "organization_id": cls.org,
        }
        cls.target = cls._prop(
            "Av. Libertador 4200",
            "Núñez",
            228000,
            78,
            3,
            2,
        )
        cls.comp_ids = [
            cls._prop("Libertador 4300", "Núñez", 220000, 74, 3, 2),
            cls._prop("Libertador 4400", "Núñez", 232000, 80, 3, 2),
            cls._prop("Congreso 2100", "Núñez", 215000, 72, 3, 2),
            cls._prop("Quesada 1800", "Núñez", 240000, 82, 3, 2),
            cls._prop("11 de Septiembre 900", "Núñez", 225000, 76, 3, 2),
            cls._prop("Cabildo 4500", "Núñez", 390000, 70, 3, 2),
        ]
        cls.house = cls._prop(
            "Casa Núñez 100",
            "Núñez",
            400000,
            140,
            5,
            4,
            property_type="house",
        )
        cls.ars = cls._prop(
            "Libertador ARS",
            "Núñez",
            80000000,
            75,
            3,
            2,
            currency="ARS",
        )
        cls.palermo = cls._prop("Santa Fe 3000", "Palermo", 300000, 78, 3, 2)
        cls.other_org_prop = cls._prop(
            "Av. Libertador 4200",
            "Núñez",
            210000,
            77,
            3,
            2,
            org=cls.other_org,
            agent_id=cls.foreign_agent,
        )
        cls.closed_prop = cls._prop("Libertador Cierre 10", "Núñez", 210000, 73, 3, 2)
        cls.target_op = add_operation(
            "02/08/2026",
            cls.agent_id,
            cls.target,
            "no",
            0,
            228000,
            3,
            6800,
            6800,
            0,
            0,
            0,
            0,
            0,
            cls.org,
        )
        cls.no_area = add_property(
            "Santa Fe 410",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=370000,
            listing_purpose="sale",
            listing_currency="USD",
            neighborhood="Núñez",
            rooms=3,
            bedrooms=2,
            parking_spaces=1,
        )
        add_operation(
            "01/08/2026",
            cls.agent_id,
            cls.closed_prop,
            "no",
            0,
            218000,
            3,
            6500,
            6500,
            0,
            0,
            0,
            0,
            0,
            cls.org,
        )

    @classmethod
    def _prop(
        cls,
        address,
        neighborhood,
        price,
        meters,
        rooms,
        bedrooms,
        *,
        property_type="apartment",
        currency="USD",
        org=None,
        agent_id=None,
    ):
        return add_property(
            address,
            "CABA",
            org or cls.org,
            agent_id=agent_id or cls.agent_id,
            status=STATUS_APPROVED,
            property_type=property_type,
            listing_price=price,
            listing_purpose="sale",
            listing_currency=currency,
            neighborhood=neighborhood,
            rooms=rooms,
            bedrooms=bedrooms,
            covered_m2=meters,
            total_m2=meters,
            parking_spaces=1,
            features={"balcony": True},
        )

    def _login(self, user_id, role, org):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["role"] = role
            sess["organization_id"] = org
        return client

    def _create(self, user=None, property_id=None):
        return create_acm_for_property(
            self.org,
            user=user or self.agent_record,
            property_id=property_id or self.target,
            language="es",
        )

    def test_01_agent_can_create_acm(self):
        view = self._create()
        self.assertIsNotNone(view["acm"]["id"])
        self.assertEqual(view["acm"]["agent_id"], self.agent_id)
        self.assertGreaterEqual(len(view["comparables"]), 4)

    def test_02_staff_route_403(self):
        client = self._login(self.admin, ROLE_ADMIN, self.org)
        self.assertEqual(client.get("/acm").status_code, 403)
        guest = app.test_client()
        self.assertEqual(guest.get("/acm").status_code, 403)

    def test_03_admin_role_403(self):
        with self.assertRaises(AcmError) as caught:
            require_acm_agent(self.admin_record)
        self.assertEqual(caught.exception.status_code, 403)

    def test_04_agent_cannot_open_other_acm(self):
        view = self._create()
        with self.assertRaises(AcmError):
            get_acm_view(
                view["acm"]["id"],
                self.org,
                user=self.other_record,
            )

    def test_05_other_org_isolated(self):
        view = self._create()
        with self.assertRaises(AcmError):
            get_acm_view(
                view["acm"]["id"],
                self.other_org,
                user={
                    "id": 0,
                    "role": ROLE_AGENT,
                    "agent_id": self.foreign_agent,
                    "organization_id": self.other_org,
                },
            )
        ids = {
            row.get("comparable_property_id")
            for row in view["comparables"]
        }
        self.assertNotIn(self.other_org_prop, ids)

    def test_06_only_own_org_comparables(self):
        view = self._create()
        for row in view["comparables"]:
            if row.get("comparable_property_id"):
                self.assertNotEqual(row["comparable_property_id"], self.other_org_prop)

    def test_07_property_type_helps_score(self):
        subject = {
            "property_type": "apartment",
            "neighborhood": "Núñez",
            "jurisdiction": "CABA",
            "covered_m2": 78,
            "listing_currency": "USD",
        }
        apt = score_comparable(subject, {"property_type": "apartment", "neighborhood": "Núñez", "covered_m2": 78, "listing_currency": "USD"})
        house = score_comparable(subject, {"property_type": "house", "neighborhood": "Núñez", "covered_m2": 78, "listing_currency": "USD"})
        self.assertGreater(apt, house)

    def test_08_currency_compatible(self):
        view = self._create()
        currencies = {
            row.get("snapshot_currency")
            for row in view["comparables"]
            if row.get("selected")
        }
        self.assertTrue(currencies <= {"USD", None, ""})

    def test_09_zone_contributes_score(self):
        subject = {"neighborhood": "Núñez", "jurisdiction": "CABA"}
        same = score_comparable(subject, {"neighborhood": "Núñez", "jurisdiction": "CABA"})
        other = score_comparable(subject, {"neighborhood": "Palermo", "jurisdiction": "CABA"})
        self.assertGreater(same, other)

    def test_10_area_similarity_contributes(self):
        subject = {"covered_m2": 78, "neighborhood": "Núñez"}
        close = score_comparable(subject, {"covered_m2": 76, "neighborhood": "Núñez"})
        far = score_comparable(subject, {"covered_m2": 140, "neighborhood": "Núñez"})
        self.assertGreater(close, far)

    def test_11_score_deterministic(self):
        subject = {
            "neighborhood": "Núñez",
            "property_type": "apartment",
            "covered_m2": 78,
            "rooms": 3,
            "bedrooms": 2,
            "listing_currency": "USD",
        }
        cand = dict(subject)
        first = score_comparable(subject, cand)
        second = score_comparable(subject, cand)
        self.assertEqual(first, second)

    def test_12_score_0_100(self):
        score = score_comparable(
            {"neighborhood": "Núñez", "property_type": "apartment"},
            {"neighborhood": "Núñez", "property_type": "apartment"},
        )
        self.assertGreaterEqual(score, Decimal("0"))
        self.assertLessEqual(score, Decimal("100"))

    def test_13_ppm2_is_decimal(self):
        value = price_per_m2("228000", "78")
        self.assertIsInstance(value, Decimal)
        self.assertEqual(value, (Decimal("228000") / Decimal("78")).quantize(Decimal("0.01")))

    def test_14_median_correct(self):
        subject = {"covered_m2": 10}
        rows = [
            {"selected": True, "is_outlier": False, "snapshot_price_per_m2": "10"},
            {"selected": True, "is_outlier": False, "snapshot_price_per_m2": "20"},
            {"selected": True, "is_outlier": False, "snapshot_price_per_m2": "30"},
        ]
        metrics = compute_metrics(rows, subject)
        self.assertEqual(metrics["median_ppm2"], Decimal("20.00"))

    def test_15_average_correct(self):
        subject = {"covered_m2": 10}
        rows = [
            {"selected": True, "is_outlier": False, "snapshot_price_per_m2": "10"},
            {"selected": True, "is_outlier": False, "snapshot_price_per_m2": "20"},
        ]
        metrics = compute_metrics(rows, subject)
        self.assertEqual(metrics["average_ppm2"], Decimal("15.00"))

    def test_16_outlier_identified(self):
        flags = flag_outliers(["2650", "2880", "2910", "3150", "9000"])
        self.assertTrue(flags[-1])
        self.assertFalse(any(flags[:-1]))

    def test_17_exclude_recalculates(self):
        view = self._create()
        selected = [row for row in view["comparables"] if row.get("selected")]
        self.assertGreaterEqual(len(selected), 2)
        before = view["acm"]["estimated_value"]
        updated = set_comparable_selected(
            view["acm"]["id"],
            self.org,
            user=self.agent_record,
            comparable_id=selected[0]["id"],
            selected=False,
        )
        self.assertIsNotNone(updated["acm"]["estimated_value"])
        self.assertNotEqual(
            sum(1 for row in updated["comparables"] if row.get("selected")),
            len(selected),
        )
        self.assertTrue(before is None or updated["acm"]["estimated_value"] is not None)

    def test_18_manual_recalculates(self):
        view = self._create()
        updated = add_manual_comparable(
            view["acm"]["id"],
            self.org,
            user=self.agent_record,
            payload={
                "reference": "Portal demo",
                "location": "Núñez",
                "price": "221000",
                "currency": "USD",
                "area": "75",
                "rooms": 3,
                "bedrooms": 2,
            },
        )
        manuals = [
            row
            for row in updated["comparables"]
            if row.get("source_type") == "manual_external"
        ]
        self.assertEqual(len(manuals), 1)
        self.assertIsNotNone(updated["acm"]["median_price_per_m2"])

    def test_19_manual_is_labeled(self):
        view = self._create()
        updated = add_manual_comparable(
            view["acm"]["id"],
            self.org,
            user=self.agent_record,
            payload={"reference": "ML", "price": "219000", "area": "74", "currency": "USD"},
        )
        manual = next(
            row
            for row in updated["comparables"]
            if row.get("source_type") == "manual_external"
        )
        self.assertEqual(manual["source_type"], "manual_external")

    def test_20_estimate_deterministic(self):
        first = self._create()["acm"]["estimated_value"]
        second = self._create()["acm"]["estimated_value"]
        self.assertEqual(first, second)
        self.assertIsNotNone(to_decimal(first))

    def test_21_range_deterministic(self):
        first = self._create()["acm"]
        second = self._create()["acm"]
        self.assertEqual(first["suggested_min_value"], second["suggested_min_value"])
        self.assertEqual(first["suggested_max_value"], second["suggested_max_value"])
        self.assertLessEqual(
            to_decimal(first["suggested_min_value"]),
            to_decimal(first["estimated_value"]),
        )
        self.assertGreaterEqual(
            to_decimal(first["suggested_max_value"]),
            to_decimal(first["estimated_value"]),
        )

    def test_22_finalized_keeps_snapshots(self):
        view = finalize_acm(
            self._create()["acm"]["id"],
            self.org,
            user=self.agent_record,
        )
        self.assertEqual(view["acm"]["status"], "finalized")
        self.assertTrue(view["comparables"])
        self.assertTrue(all(row.get("snapshot_price") for row in view["comparables"]))

    def test_23_later_property_change_does_not_alter_finalized(self):
        view = finalize_acm(
            self._create()["acm"]["id"],
            self.org,
            user=self.agent_record,
        )
        target_comp = next(
            row
            for row in view["comparables"]
            if row.get("comparable_property_id") == self.comp_ids[0]
        )
        original = target_comp["snapshot_price"]
        update_property_from_sync(self.comp_ids[0], self.org, listing_price=1)
        again = get_acm_view(
            view["acm"]["id"],
            self.org,
            user=self.agent_record,
        )
        frozen = next(
            row
            for row in again["comparables"]
            if row.get("id") == target_comp["id"]
        )
        self.assertEqual(frozen["snapshot_price"], original)

    def test_24_draft_can_refresh(self):
        view = self._create()
        update_property_from_sync(self.comp_ids[1], self.org, listing_price=233500)
        refreshed = refresh_draft(
            view["acm"]["id"],
            self.org,
            user=self.agent_record,
        )
        self.assertNotEqual(refreshed["acm"]["status"], "finalized")
        prices = {
            row.get("snapshot_price")
            for row in refreshed["comparables"]
            if row.get("comparable_property_id") == self.comp_ids[1]
        }
        self.assertTrue(prices)

    def test_25_pdf_with_agent_has_contact(self):
        from modules.pdf_acm_report import build_acm_pdf

        view = self._create()
        view["agent_contact"] = {
            "name": "Ana ACM",
            "phone": "1144441111",
            "email": "ana@jrh.test",
        }
        payload = build_acm_pdf(
            view,
            include_agent=True,
            language="es",
            brand_name="JRH One",
            logo_path=None,
            compress=False,
        )
        text = _pdf_haystack(payload.getvalue())
        self.assertTrue(b"Ana ACM" in text, "agent name missing from ACM PDF")
        self.assertTrue(b"1144441111" in text, "agent phone missing from ACM PDF")
        self.assertTrue(b"ana@jrh.test" in text, "agent email missing from ACM PDF")

    def test_26_pdf_without_agent_hides_contact(self):
        from modules.pdf_acm_report import build_acm_pdf

        view = self._create()
        view["agent_contact"] = {
            "name": "Ana ACM",
            "phone": "1144441111",
            "email": "ana@jrh.test",
        }
        payload = build_acm_pdf(
            view,
            include_agent=False,
            language="es",
            brand_name="JRH One",
            logo_path=None,
            compress=False,
        )
        text = _pdf_haystack(payload.getvalue())
        self.assertNotIn(b"1144441111", text)
        self.assertNotIn(b"ana@jrh.test", text)

    def test_27_jrh_agent_start_acm(self):
        result = ask_jrh(
            "haceme un ACM de Libertador 4200",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], START_ACM)
        self.assertTrue(result.get("wrote"))
        self.assertEqual(result["entity"].get("kind"), "acm")
        self.assertTrue(result.get("actions"))

    def test_28_jrh_staff_start_acm_blocked(self):
        result = ask_jrh(
            "haceme un ACM de Libertador 4200",
            organization_id=self.org,
            user=self.admin_record,
            agent_id=None,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], START_ACM)
        self.assertIn("Agente", result["message"])

    def test_29_ambiguous_property_asks(self):
        result = ask_jrh(
            "haceme un ACM de Libertador",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], START_ACM)
        self.assertGreaterEqual(len(result.get("candidates") or result.get("cards") or []), 2)

    def test_30_context_property_then_acm(self):
        session = {
            "jrh_ai_context": {
                "last_intent": "QUERY_PROPERTIES",
                "last_entity": {
                    "kind": "property",
                    "id": self.target,
                    "label": "Av. Libertador 4200",
                },
                "last_prompt": "mostrame la propiedad de Libertador 4200",
                "pending_invoice": {},
            }
        }
        result = ask_jrh(
            "haceme un ACM",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session=session,
        )
        self.assertEqual(result["intent"], START_ACM)
        self.assertTrue(result.get("wrote"))
        self.assertIn("Libertador 4200", result.get("data", {}).get("address") or result.get("message") or "")

    def test_31_migration_idempotent(self):
        migrate_property_acm_sqlite()
        migrate_property_acm_sqlite()

    def test_32_create_tables_twice(self):
        create_tables()
        create_tables()

    def test_33_render_acm_200(self):
        view = self._create()
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        listing = client.get("/acm")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("ACM", listing.get_data(as_text=True))
        detail = client.get(f"/acm/{view['acm']['id']}")
        self.assertEqual(detail.status_code, 200)
        comps = client.get(f"/acm/{view['acm']['id']}/comparables")
        self.assertEqual(comps.status_code, 200)

    def test_34_pdf_200(self):
        view = self._create()
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        response = client.get(f"/acm/{view['acm']['id']}/pdf?include_agent=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        guest = self._login(self.admin, ROLE_ADMIN, self.org)
        self.assertEqual(guest.get(f"/acm/{view['acm']['id']}/pdf").status_code, 403)

    def test_35_property_surface_is_read(self):
        view = self._create()
        self.assertEqual(to_decimal(view["subject"].get("covered_m2")), Decimal("78"))
        with_area = [row for row in view["comparables"] if row.get("display_area")]
        self.assertGreaterEqual(len(with_area), 3)

    def test_36_missing_surface_is_flagged(self):
        quality = preview_acm_property(
            self.org,
            user=self.agent_record,
            property_id=self.no_area,
        )["quality"]
        self.assertEqual(quality["checks"]["area"], "missing")
        self.assertFalse(quality["can_valuate"])

    def test_37_price_and_area_is_valid(self):
        self.assertTrue(
            is_valuation_valid(
                {
                    "selected": True,
                    "snapshot_price": "150000",
                    "snapshot_covered_area": "75",
                    "snapshot_currency": "USD",
                },
                {"listing_currency": "USD", "covered_m2": 78},
            )
        )

    def test_38_missing_area_is_reference(self):
        self.assertFalse(
            is_valuation_valid(
                {
                    "selected": True,
                    "snapshot_price": "105000",
                    "snapshot_currency": "USD",
                },
                {"listing_currency": "USD", "covered_m2": 78},
            )
        )

    def test_39_found_vs_valid_counts(self):
        view = self._create()
        metrics = view["metrics"]
        self.assertGreaterEqual(metrics["found_count"], metrics["valuation_count"])
        self.assertEqual(
            metrics["found_count"],
            (metrics["valuation_count"] or 0) + (metrics["reference_count"] or 0),
        )

    def test_40_min_comps_block_finalize(self):
        view = create_acm_for_property(
            self.org,
            user=self.agent_record,
            property_id=self.no_area,
            language="es",
        )
        self.assertFalse(view["can_finalize"])
        with self.assertRaises(AcmError) as caught:
            finalize_acm(view["acm"]["id"], self.org, user=self.agent_record)
        self.assertEqual(caught.exception.message_key, "acm_err_not_ready")

    def test_41_manual_area_override(self):
        view = self._create()
        target = next(
            row
            for row in view["comparables"]
            if row.get("comparable_property_id") == self.comp_ids[0]
        )
        updated = override_comparable_area(
            view["acm"]["id"],
            self.org,
            user=self.agent_record,
            comparable_id=target["id"],
            area="81",
        )
        row = next(item for item in updated["comparables"] if item["id"] == target["id"])
        self.assertEqual(row.get("area_source"), "manual_acm")
        self.assertIn("81", str(row.get("snapshot_covered_area") or row.get("snapshot_total_area")))

    def test_42_manual_comparable_identified(self):
        view = self._create()
        updated = add_manual_comparable(
            view["acm"]["id"],
            self.org,
            user=self.agent_record,
            payload={
                "reference": "Zonaprop",
                "address": "Libertador 5000",
                "location": "Núñez",
                "price": "230000",
                "currency": "USD",
                "area": "75",
                "url": "https://example.com/listing",
            },
        )
        manual = next(
            row for row in updated["comparables"] if row.get("source_type") == "manual_external"
        )
        self.assertEqual(manual["area_source"], "manual_external")
        self.assertEqual(manual["snapshot_url"], "https://example.com/listing")

    def test_43_closing_identified(self):
        view = self._create()
        closings = [
            row
            for row in view["comparables"]
            if row.get("snapshot_price_kind") == "closing"
        ]
        self.assertTrue(closings)
        self.assertGreaterEqual(view["metrics"].get("closing_count") or 0, 0)

    def test_44_confidence_deterministic(self):
        view = self._create()
        self.assertIn(view["metrics"]["confidence"], {"high", "medium", "low"})

    def test_45_positioning_and_scenarios(self):
        view = self._create()
        self.assertIsNotNone(view["metrics"].get("scenarios"))
        self.assertIsNotNone(view["metrics"].get("positioning"))
        self.assertEqual(
            to_decimal(view["metrics"]["scenarios"]["market"]),
            to_decimal(view["acm"]["estimated_value"]),
        )

    def test_46_money_formatting(self):
        from modules.formatting import format_money

        self.assertEqual(format_money(385000, currency="USD", language="es"), "USD 385.000,00")

    def test_47_jrh_missing_area(self):
        result = ask_jrh(
            "haceme un ACM de Santa Fe 410",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], START_ACM)
        self.assertIn("superficie", result["message"].lower())

    def test_48_quality_preview_render(self):
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        response = client.get(f"/acm/new?property_id={self.target}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/acm/", response.headers.get("Location", ""))

    def test_49_hero_uses_backend_values(self):
        view = self._create()
        facts = build_acm_facts(view, language="es")
        self.assertEqual(facts["estimated"], view["acm"]["estimated_value"])
        self.assertEqual(facts["range_min"], view["acm"]["suggested_min_value"])
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        html = client.get(f"/acm/{view['acm']['id']}").get_data(as_text=True)
        self.assertIn("Valor de referencia", html)
        self.assertNotIn("acm_diff_[", html)

    def test_50_ai_explanation_does_not_change_numbers(self):
        view = self._create()
        facts = view["facts"]
        explained = explain_acm(
            facts,
            narrative=f"El valor es {facts['estimated_label']} según el motor.",
            language="es",
        )
        self.assertEqual(view["acm"]["estimated_value"], facts["estimated"])
        if explained["source"] == "ai":
            self.assertIn(str(facts["estimated"])[:3], explained["text"])

    def test_51_fallback_deterministic(self):
        view = self._create()
        self.assertEqual(view["ai_explanation"]["source"], "fallback")
        self.assertTrue(view["market_highlights"])

    def test_52_human_diff_in_ui(self):
        label = format_diff_label(("area_delta", "-19.8"), language="es")
        self.assertIn("menos superficie", label)
        view = self._create()
        html = self._login(self.agent_user, ROLE_AGENT, self.org).get(
            f"/acm/{view['acm']['id']}"
        ).get_data(as_text=True)
        self.assertNotIn("acm_diff_['area_delta'", html)

    def test_53_price_scenario_route(self):
        view = self._create()
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        response = client.post(
            f"/acm/{view['acm']['id']}/scenario",
            data={"proposed_price": "360000"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertTrue("rango" in body.lower())
        scenario = compute_price_scenario(
            360000,
            view["acm"]["estimated_value"],
            view["acm"]["suggested_min_value"],
            view["acm"]["suggested_max_value"],
        )
        self.assertIsNotNone(scenario)

    def test_54_exclude_requires_confirm(self):
        view = self._create()
        row = next(item for item in view["comparables"] if item.get("selected"))
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        preview = client.get(
            f"/acm/{view['acm']['id']}/comparables?confirm_exclude={row['id']}"
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("¿Excluir este comparable", preview.get_data(as_text=True))
        still = get_acm_view(
            view["acm"]["id"], self.org, user=self.agent_record, language="es"
        )
        self.assertTrue(
            any(item["id"] == row["id"] and item["selected"] for item in still["comparables"])
        )

    def test_55_finalized_not_modified(self):
        view = self._create()
        finalize_acm(view["acm"]["id"], self.org, user=self.agent_record, language="es")
        row = next(item for item in view["comparables"] if item.get("selected"))
        with self.assertRaises(AcmError):
            set_comparable_selected(
                view["acm"]["id"],
                self.org,
                user=self.agent_record,
                comparable_id=row["id"],
                selected=False,
                language="es",
            )

    def test_56_source_type_visible(self):
        view = self._create()
        html = self._login(self.agent_user, ROLE_AGENT, self.org).get(
            f"/acm/{view['acm']['id']}"
        ).get_data(as_text=True)
        self.assertTrue(
            "Publicación JRH" in html or "Cierre" in html or "manual" in html.lower()
        )
        self.assertTrue(all(row.get("source_label") for row in view["comparables"]))

    def test_57_agent_only_still_valid(self):
        client = self._login(self.admin, ROLE_ADMIN, self.org)
        view = self._create()
        self.assertEqual(client.get(f"/acm/{view['acm']['id']}").status_code, 403)
        self.assertEqual(client.post(f"/acm/{view['acm']['id']}/scenario", data={"proposed_price": "1"}).status_code, 403)

    def test_58_pdf_with_and_without_agent(self):
        from modules.pdf_acm_report import generate_acm_pdf_bytes

        view = self._create()
        view["agent_contact"] = agent_contact_for_acm(view)
        with_agent = generate_acm_pdf_bytes(view, include_agent=True, language="es").read()
        without = generate_acm_pdf_bytes(view, include_agent=False, language="es").read()
        self.assertIn(b"Ana ACM", _pdf_haystack(with_agent))
        self.assertNotIn(b"Ana ACM", _pdf_haystack(without))

    def test_59_mobile_detail_200(self):
        view = self._create()
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        response = client.get(f"/acm/{view['acm']['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("acm-hero", response.get_data(as_text=True))

    def test_60_min_comps_still_blocks(self):
        view = create_acm_for_property(
            self.org,
            user=self.agent_record,
            property_id=self.house,
            language="es",
        )
        if view.get("can_finalize"):
            return
        with self.assertRaises(AcmError):
            finalize_acm(view["acm"]["id"], self.org, user=self.agent_record)

    def test_61_jrh_explain_and_scenario(self):
        view = self._create()
        session = {
            "jrh_ai_context": {
                "last_intent": "QUERY_ACM",
                "last_entity": {"kind": "acm", "id": view["acm"]["id"]},
                "last_prompt": "",
                "pending_invoice": {},
            }
        }
        explain = ask_jrh(
            "por qué me da ese valor",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session=session,
        )
        self.assertEqual(explain["intent"], ACM_EXPLAIN)
        preview = ask_jrh(
            "sacá Libertador 4300",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session=session,
        )
        self.assertEqual(preview["intent"], ACM_REMOVE_COMPARABLE)
        self.assertFalse(preview.get("wrote"))
        self.assertTrue(preview.get("confirm_required"))
        self.assertEqual(preview.get("message_key"), "acm_jrh_exclude_preview")
        self.assertTrue(preview.get("actions"))
        self.assertIn("4300", str(preview.get("data") or {}))
        scenario = ask_jrh(
            "qué pasa si publico en 360000",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session=session,
        )
        self.assertEqual(scenario["intent"], ACM_PRICE_SCENARIO)
        filtered = ask_jrh(
            "solo cierres reales",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session=session,
        )
        self.assertEqual(filtered["intent"], ACM_FILTER_COMPARABLES)

    def test_62_jrh_start_from_operation_reference(self):
        com = f"COM-{int(self.target_op):06d}"
        result = ask_jrh(
            f"haceme un ACM de {com}",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], START_ACM)
        self.assertTrue(result.get("wrote"))
        self.assertEqual(result["entity"].get("kind"), "acm")
        self.assertIn("Libertador 4200", result.get("data", {}).get("address") or "")

    def test_63_jrh_start_from_operation_address(self):
        result = ask_jrh(
            "haceme un ACM de la operación de Av. Libertador 4200",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], START_ACM)
        self.assertTrue(result.get("wrote"))
        self.assertIn("Libertador 4200", result.get("data", {}).get("address") or "")

    def test_64_jrh_ambiguous_operations(self):
        result = ask_jrh(
            "haceme un ACM de la operación de Libertador",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], START_ACM)
        self.assertFalse(result.get("wrote"))
        self.assertGreaterEqual(len(result.get("candidates") or result.get("cards") or []), 2)

    def test_65_missing_area_asks_only_area(self):
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        response = client.get(f"/acm/new?property_id={self.no_area}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("superficie", html.lower())
        self.assertIn("total_m2", html)
        created = client.post(
            f"/acm/new?property_id={self.no_area}",
            data={"total_m2": "85"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        self.assertIn("/acm/", created.headers.get("Location", ""))

    def test_66_exclude_recalculates_from_detail(self):
        view = self._create()
        selected = next(row for row in view["comparables"] if row.get("selected"))
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        response = client.post(
            f"/acm/{view['acm']['id']}/exclude",
            data={"comparable_id": selected["id"]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/acm/{view['acm']['id']}", response.headers.get("Location", ""))

    def test_67_download_uses_acm_context(self):
        view = self._create()
        session = {
            "jrh_ai_context": {
                "last_intent": START_ACM,
                "last_entity": {"kind": "acm", "id": view["acm"]["id"]},
                "last_prompt": "",
                "pending_invoice": {},
            }
        }
        choice = ask_jrh(
            "descargame el ACM",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session=session,
        )
        self.assertEqual(choice["intent"], DOWNLOAD_ACM)
        self.assertGreaterEqual(len(choice.get("actions") or []), 2)
        without = ask_jrh(
            "descargalo sin mis datos",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session=session,
        )
        self.assertEqual(without["intent"], DOWNLOAD_ACM)
        self.assertTrue(without.get("actions"))
        self.assertEqual(without["actions"][0]["href_args"].get("include_agent"), 0)

    def test_68_pdf_contains_range_and_money(self):
        from modules.pdf_acm_report import generate_acm_pdf_bytes

        view = self._create()
        view["agent_contact"] = agent_contact_for_acm(view)
        payload = generate_acm_pdf_bytes(view, include_agent=True, language="es").read()
        haystack = _pdf_haystack(payload)
        from modules.formatting import format_money

        text = haystack.decode("latin-1", "ignore")
        self.assertIn("USD", text)
        estimated = format_money(view["acm"]["estimated_value"], currency="USD", language="es")
        self.assertTrue(
            estimated in text or estimated.replace("USD ", "") in text
        )

    def test_69_review_comparables_is_optional(self):
        view = self._create()
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        html = client.get(f"/acm/{view['acm']['id']}").get_data(as_text=True)
        self.assertIn("Revisar comparables", html)
        self.assertIn("acm-hero", html)
        self.assertNotIn("acm_diff_[", html)


if __name__ == "__main__":
    unittest.main()

