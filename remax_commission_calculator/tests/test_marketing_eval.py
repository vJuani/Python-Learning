"""Internal Marketing IA quality evaluation. Isolated from production assets."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_marketing_eval.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("OPENAI_API_KEY", None)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.marketing_eval_repository import get_eval_run, list_eval_runs
from modules.marketing_context import build_property_marketing_context
from modules.marketing_creative import generate, generate_variants, render_creative_text
from modules.marketing_eval import run_eval_generation, run_eval_variants
from web_app import app


class MarketingEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="marketing-eval-tests")
        create_tables()
        cls.org = add_organization("Eval Org")
        cls.agent_id = add_agent("Eval Agent", "Alto", cls.org)
        cls.admin_id = add_user("eval_admin", hash_password("Password1"), ROLE_ADMIN, cls.org)
        cls.agent_user_id = add_user(
            "eval_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )
        cls.sale_apt = add_property(
            "Santamarina 1335",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            property_type="apartment",
            listing_purpose="sale",
            listing_price=180000,
            listing_currency="USD",
            neighborhood="Belgrano",
            rooms=3,
            bedrooms=2,
            bathrooms=2,
            covered_m2=78,
            description="Living luminoso y cocina integrada.",
        )
        cls.rent_apt = add_property(
            "Cabildo 2200",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            property_type="apartment",
            listing_purpose="rental",
            listing_price=900,
            listing_currency="USD",
            neighborhood="Belgrano",
            rooms=2,
            bedrooms=1,
            bathrooms=1,
            covered_m2=45,
            description="Alquiler temporal con amenities.",
            features=["pileta", "gimnasio", "SUM"],
        )
        cls.house = add_property(
            "Los Alerces 120",
            "Pilar",
            cls.org,
            agent_id=cls.agent_id,
            property_type="house",
            listing_purpose="sale",
            listing_price=320000,
            listing_currency="USD",
            rooms=5,
            bedrooms=4,
            bathrooms=3,
            covered_m2=180,
            description="Casa con jardín.",
        )
        cls.sparse = add_property(
            "Sin datos 1",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            property_type="apartment",
            listing_purpose="sale",
        )

    def _client(self, user_id, role):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = self.org
        return client

    def test_eval_route_is_admin_only(self):
        admin = self._client(self.admin_id, ROLE_ADMIN).get("/marketing/eval")
        self.assertEqual(admin.status_code, 200)
        body = admin.get_data(as_text=True)
        self.assertIn("Evaluación de calidad", body)
        self.assertNotIn("OPENAI_API_KEY", body)
        self.assertNotRegex(body, r"sk-[A-Za-z0-9]{8,}")
        agent = self._client(self.agent_user_id, ROLE_AGENT).get("/marketing/eval")
        self.assertEqual(agent.status_code, 403)
        guest = app.test_client().get("/marketing/eval")
        self.assertIn(guest.status_code, {302, 403})

    def test_generate_persists_eval_not_production_asset(self):
        admin = {"id": self.admin_id, "role": ROLE_ADMIN, "organization_id": self.org}
        run = run_eval_generation(
            self.org,
            admin,
            property_id=self.sale_apt,
            fmt="post",
            style="premium",
            tone="formal",
            case_slug="sale-apartment",
        )
        self.assertEqual(run["format"], "post")
        self.assertEqual(run["style"], "premium")
        self.assertEqual(run["tone"], "formal")
        self.assertEqual(run["prompt_version"], "creative-text-v1")
        self.assertTrue(run["rendered_text"])
        self.assertTrue(run["creative_brief"])
        self.assertIn("headline", run["output"])
        self.assertIsNotNone(run["duration_ms"])
        connection = get_connection()
        count = connection.execute("SELECT COUNT(*) FROM marketing_assets").fetchone()[0]
        connection.close()
        self.assertEqual(count, 0)
        stored = get_eval_run(run["id"], self.org)
        self.assertEqual(stored["property_id"], self.sale_apt)

    def test_generate_variants_creates_three_labeled_runs(self):
        admin = {"id": self.admin_id, "role": ROLE_ADMIN, "organization_id": self.org}
        bundle = run_eval_variants(
            self.org,
            admin,
            property_id=self.house,
            fmt="story",
        )
        self.assertEqual(len(bundle["runs"]), 3)
        labels = [item["variant_label"] for item in bundle["runs"]]
        self.assertEqual(labels, ["Directa", "Aspiracional", "Premium"])
        groups = {item["group_id"] for item in bundle["runs"]}
        self.assertEqual(len(groups), 1)
        listed = list_eval_runs(self.org, group_id=bundle["group_id"])
        self.assertEqual(len(listed), 3)

    def test_score_and_compare_via_http(self):
        admin_user = {"id": self.admin_id, "role": ROLE_ADMIN, "organization_id": self.org}
        first = run_eval_generation(
            self.org, admin_user, property_id=self.rent_apt, fmt="whatsapp", style="modern", tone="close"
        )
        second = run_eval_generation(
            self.org, admin_user, property_id=self.rent_apt, fmt="copy", style="minimal", tone="formal"
        )
        client = self._client(self.admin_id, ROLE_ADMIN)
        scored = client.post(
            f"/marketing/eval/runs/{first['id']}/score",
            data={"score": "good", "issues": ["weak_cta", "sounds_ai"]},
            follow_redirects=True,
        )
        self.assertEqual(scored.status_code, 200)
        stored = get_eval_run(first["id"], self.org)
        self.assertEqual(stored["score"], "good")
        self.assertEqual(stored["issues"], ["weak_cta", "sounds_ai"])
        compare = client.get(f"/marketing/eval?a={first['id']}&b={second['id']}")
        body = compare.get_data(as_text=True)
        self.assertEqual(compare.status_code, 200)
        self.assertIn("Comparar A vs B", body)
        self.assertIn(str(first["id"]), body)
        self.assertIn(str(second["id"]), body)

    def test_http_generate_and_variants(self):
        client = self._client(self.admin_id, ROLE_ADMIN)
        created = client.post(
            "/marketing/eval/generate",
            data={
                "property_id": self.sale_apt,
                "format": "carousel",
                "style": "dynamic",
                "tone": "aspirational",
                "origin": "listing",
                "case_slug": "sale-apartment",
            },
            follow_redirects=True,
        )
        self.assertEqual(created.status_code, 200)
        self.assertIn("carousel", created.get_data(as_text=True))
        variants = client.post(
            "/marketing/eval/variants",
            data={"property_id": self.sparse, "format": "post", "origin": "listing"},
            follow_redirects=True,
        )
        self.assertEqual(variants.status_code, 200)
        self.assertIn("Directa", variants.get_data(as_text=True))
        self.assertIn("Aspiracional", variants.get_data(as_text=True))
        self.assertIn("Premium", variants.get_data(as_text=True))

    def test_formats_and_styles_differ_in_mock_output(self):
        from modules.database.properties_repository import get_property_record

        record = get_property_record(self.sale_apt, self.org)
        context = build_property_marketing_context(record)
        post = generate(context, fmt="post", style="premium", tone="formal")
        story = generate(context, fmt="story", style="dynamic", tone="commercial")
        whatsapp = generate(context, fmt="whatsapp", style="modern", tone="close")
        copy = generate(context, fmt="copy", style="minimal", tone="exclusive")
        self.assertIn("hashtags", post["output"])
        self.assertNotIn("#", whatsapp["rendered_text"])
        self.assertGreaterEqual(len(story["output"]["frames"]), 3)
        self.assertLessEqual(len(story["output"]["frames"]), 5)
        self.assertTrue(copy["output"]["body"])
        self.assertNotEqual(post["rendered_text"], copy["rendered_text"])
        self.assertNotEqual(post["rendered_text"], whatsapp["rendered_text"])
        minimal = generate(context, fmt="story", style="minimal", tone="formal")
        dynamic = generate(context, fmt="story", style="dynamic", tone="aspirational")
        self.assertLess(len(minimal["output"]["frames"]), len(dynamic["output"]["frames"]))
        self.assertNotEqual(minimal["rendered_text"], dynamic["rendered_text"])
        bundle = generate_variants(context, fmt="post")
        self.assertEqual(len(bundle["items"]), 3)
        texts = {item["rendered_text"] for item in bundle["items"]}
        self.assertGreaterEqual(len(texts), 2)

    def test_free_origin_requires_notes(self):
        admin = {"id": self.admin_id, "role": ROLE_ADMIN, "organization_id": self.org}
        from modules.marketing_context import MarketingError

        with self.assertRaises(MarketingError):
            run_eval_generation(self.org, admin, origin="free", fmt="copy")
        run = run_eval_generation(
            self.org,
            admin,
            origin="personal_brand",
            fmt="whatsapp",
            notes="Hablar como yo, cercano, sin oficina.",
        )
        self.assertEqual(run["origin"], "personal_brand")
        self.assertIn("cercano", run["rendered_text"].lower() + run["input_summary"].get("evaluator_notes", "").lower())

    def test_render_helper_covers_all_formats(self):
        self.assertTrue(render_creative_text("post", {"headline": "H", "caption": "C", "cta": "X", "hashtags": ["#A"]}))
        self.assertIn("1.", render_creative_text("story", {"frames": [{"text": "hola"}], "cta": "X"}))
        self.assertIn("Hook", render_creative_text("carousel", {"hook": "H", "slides": [{"title": "T", "body": "B"}]}))


if __name__ == "__main__":
    unittest.main()
