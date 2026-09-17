"""Marketing IA V1 generations: mock ledger, tenant scope, no live providers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_marketing_generations.db")
os.environ.pop("DATABASE_URL", None)
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ["MARKETING_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from unittest.mock import patch

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.marketing_generations_repository import (
    STATUS_PROCESSING,
    create_marketing_generation,
    get_marketing_generation,
    list_marketing_generations,
)
from modules.database.marketing_migration import migrate_marketing
from modules.database.users_repository import get_user_by_id
from modules.marketing_ai import (
    MOCK_MODEL,
    MOCK_PROVIDER,
    MarketingAIError,
    OpenAIMarketingProvider,
    build_model_payload,
    generate_content,
    generate_variants,
    validate_generation_output,
)
from modules.marketing_ai_client import MarketingAIClientError, get_marketing_ai_model
from modules.marketing_ai_prompts import build_copy_instructions
from modules.marketing_context import MarketingError
from modules.marketing_chat_service import interpret_prompt
from modules.marketing_generation_service import (
    build_generation_context,
    can_view_generation,
    create_and_run_generation,
    discard_generation,
    list_generation_views,
    regenerate_generation,
    run_generation,
)
from web_app import app


VALID_BRIEF = {
    "audience": "Compradores de 2 ambientes en Victoria",
    "primary_goal": "Conseguir visitas",
    "creative_angle": "Ubicación y metraje reales",
    "key_facts": ["2 ambientes", "Victoria"],
    "main_hook": "2 ambientes en Victoria",
    "supporting_points": ["Pedir visita"],
    "cta_strategy": "WhatsApp para coordinar visita",
    "avoid": ["invented amenities"],
}

VALID_COPY = {
    "headline": "Departamento en Victoria",
    "body": "2 ambientes en Victoria, con los datos de la ficha. Pedí una visita.",
    "cta": "Pedí una visita",
}

VALID_POST = {
    "headline": "Departamento en Victoria",
    "caption": "2 ambientes en Victoria. Luz, metraje y ubicación de la ficha.",
    "cta": "Pedí una visita",
    "hashtags": ["#Victoria", "#Venta"],
}

VALID_STORY = {
    "frames": [
        {"headline": "Victoria", "body": "Un 2 ambientes para recorrer.", "cta": ""},
        {"headline": "Los datos", "body": "Ambientes y metros reales.", "cta": ""},
        {"headline": "Visita", "body": "Coordinamos un horario.", "cta": "Escribime"},
    ]
}

VALID_CAROUSEL = {
    "hook": "2 ambientes en Victoria",
    "slides": [
        {"slide": 1, "title": "Victoria", "body": "Barrio y acceso real de la ficha."},
        {"slide": 2, "title": "Espacios", "body": "Ambientes y metros cargados."},
        {"slide": 3, "title": "Siguiente paso", "body": "Escribime y armamos la visita."},
    ],
    "final_cta": "Pedí una visita",
}

VALID_WHATSAPP = {
    "message": "Hola, te paso este 2 ambientes en Victoria. Si te sirve, coordinamos visita.",
    "cta": "¿Te armo un horario?",
}


class MarketingGenerationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="marketing-generations-test", SESSION_COOKIE_SECURE=False)
        create_tables()
        cls.org = add_organization("Gen Org")
        cls.other_org = add_organization("Other Gen Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana Gen", "Alto", cls.org)
        cls.other_agent_id = add_agent("Otro Gen", "Alto", cls.org)
        cls.foreign_agent_id = add_agent("Foreign Gen", "Alto", cls.other_org)
        cls.admin_id = add_user(
            "gen_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="gen.admin@example.com",
            first_name="Staff",
            last_name="Admin",
        )
        cls.agent_user_id = add_user(
            "gen_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            last_name="Gen",
            email="ana.gen@example.com",
        )
        cls.other_user_id = add_user(
            "gen_other",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent_id,
            first_name="Otro",
            last_name="Gen",
            email="otro.gen@example.com",
        )
        cls.foreign_admin_id = add_user(
            "gen_foreign",
            pwd,
            ROLE_ADMIN,
            cls.other_org,
            email="foreign.gen@example.com",
        )
        cls.property_id = add_property(
            "Libertador 1000",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent_id,
            created_by_user_id=cls.admin_id,
        )
        cls.other_property_id = add_property(
            "Cabildo 200",
            "Buenos Aires",
            cls.org,
            agent_id=cls.other_agent_id,
            created_by_user_id=cls.admin_id,
        )

    def _user(self, user_id):
        return get_user_by_id(user_id)

    def _login(self, user_id, role, organization_id=None, agent_id=None):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = organization_id or self.org
            if agent_id is not None:
                session["agent_id"] = agent_id
        return client

    def test_migrate_is_idempotent(self):
        migrate_marketing()
        migrate_marketing()
        rows = list_marketing_generations(self.org)
        self.assertIsInstance(rows, list)
        connection = get_connection()
        try:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertIn("marketing_conversations", names)
        self.assertIn("marketing_messages", names)

    def test_mock_provider_does_not_call_real_api(self):
        result = generate_content(
            "copy",
            {"language": "es", "content_type": "copy", "origin": "free"},
        )
        self.assertEqual(result["provider"], MOCK_PROVIDER)
        self.assertEqual(result["model_name"], MOCK_MODEL)
        self.assertIn("Copy de prueba", result["generated_copy"])
        self.assertEqual(result["estimated_cost"], 0)

    def test_create_mock_generation_and_list(self):
        user = self._user(self.agent_user_id)
        generation = create_and_run_generation(
            self.org,
            user,
            content_type="post",
            origin="property",
            property_id=self.property_id,
            objective="sale",
            style="premium",
            tone="professional",
        )
        self.assertEqual(generation["status"], "completed")
        self.assertEqual(generation["organization_id"], self.org)
        self.assertEqual(generation["created_by_user_id"], self.agent_user_id)
        self.assertEqual(generation["agent_id"], self.agent_id)
        self.assertEqual(generation["property_id"], self.property_id)
        self.assertEqual(generation["provider"], MOCK_PROVIDER)
        self.assertIn("Copy de prueba", generation["generated_copy"] or "")
        items = list_generation_views(self.org, user, content_type="post")
        ids = {item["id"] for item in items}
        self.assertIn(generation["id"], ids)

    def test_regenerate_keeps_parent_history(self):
        user = self._user(self.agent_user_id)
        original = create_and_run_generation(
            self.org,
            user,
            content_type="story",
            origin="free",
        )
        copy_text = original["generated_copy"]
        child = regenerate_generation(self.org, user, original["id"])
        self.assertNotEqual(child["id"], original["id"])
        self.assertEqual(child["parent_generation_id"], original["id"])
        self.assertEqual(child["status"], "completed")
        stored = get_marketing_generation(original["id"], self.org)
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["generated_copy"], copy_text)

    def test_discard_keeps_row(self):
        user = self._user(self.agent_user_id)
        generation = create_and_run_generation(
            self.org,
            user,
            content_type="carousel",
            origin="personal_brand",
        )
        discarded = discard_generation(self.org, user, generation["id"])
        self.assertEqual(discarded["status"], "discarded")
        self.assertTrue(discarded["generated_copy"])

    def test_agent_sees_own_generations_only(self):
        owner = self._user(self.agent_user_id)
        other = self._user(self.other_user_id)
        mine = create_and_run_generation(self.org, owner, content_type="copy", origin="office")
        theirs = create_and_run_generation(self.org, other, content_type="copy", origin="office")
        mine_ids = {item["id"] for item in list_generation_views(self.org, owner)}
        other_ids = {item["id"] for item in list_generation_views(self.org, other)}
        self.assertIn(mine["id"], mine_ids)
        self.assertNotIn(theirs["id"], mine_ids)
        self.assertIn(theirs["id"], other_ids)
        self.assertNotIn(mine["id"], other_ids)

    def test_admin_cannot_see_agent_generations(self):
        agent = self._user(self.agent_user_id)
        admin = self._user(self.admin_id)
        generation = create_and_run_generation(self.org, agent, content_type="copy", origin="free")
        admin_ids = {item["id"] for item in list_generation_views(self.org, admin)}
        self.assertNotIn(generation["id"], admin_ids)
        self.assertFalse(can_view_generation(admin, generation))

    def test_no_cross_organization_access(self):
        owner = self._user(self.agent_user_id)
        foreign = self._user(self.foreign_admin_id)
        generation = create_and_run_generation(self.org, owner, content_type="copy", origin="free")
        self.assertIsNone(get_marketing_generation(generation["id"], self.other_org))
        foreign_ids = {item["id"] for item in list_generation_views(self.other_org, foreign)}
        self.assertNotIn(generation["id"], foreign_ids)

    def test_agent_cannot_use_other_agent_property(self):
        other = self._user(self.other_user_id)
        with self.assertRaises(MarketingError) as raised:
            create_and_run_generation(
                self.org,
                other,
                content_type="post",
                origin="property",
                property_id=self.property_id,
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_http_hub_create_list_and_detail(self):
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        hub = client.get("/marketing")
        self.assertEqual(hub.status_code, 200)
        body = hub.get_data(as_text=True)
        self.assertIn("Marketing IA", body)
        self.assertIn("¿Qué querés crear hoy?", body)
        self.assertIn("Mis creaciones", body)
        self.assertIn("Plantillas", body)
        self.assertIn("Brand kit", body)
        self.assertIn("Pedile a JRH que cree algo", body)
        create_page = client.get("/marketing/create")
        self.assertEqual(create_page.status_code, 200)
        self.assertIn('name="content_type"', create_page.get_data(as_text=True))
        created = client.post(
            "/marketing/create",
            data={
                "content_type": "copy",
                "origin": "free",
                "objective": "brand",
                "style": "minimal",
                "tone": "close",
            },
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        location = created.headers["Location"]
        self.assertIn("/marketing/generations/", location)
        detail = client.get(location)
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Copy de prueba", detail.get_data(as_text=True))
        listing = client.get("/marketing/generations?type=copy")
        self.assertEqual(listing.status_code, 200)
        self.assertIn("Copy", listing.get_data(as_text=True))

    def test_interpret_prompt_property_and_revision(self):
        properties = [{"id": 7, "address": "Don Bosco 477", "locality": "Victoria"}]
        first = interpret_prompt(
            "Haceme una publicidad premium para Instagram de Don Bosco 477",
            properties=properties,
        )
        self.assertEqual(first["content_type"], "post")
        self.assertEqual(first["origin"], "property")
        self.assertEqual(first["style"], "premium")
        self.assertEqual(first["property_id"], 7)
        self.assertFalse(first["revising"])
        last = {
            "id": 99,
            "content_type": "post",
            "origin": "property",
            "style": "premium",
            "property_id": 7,
            "generated_data": {},
        }
        follow = interpret_prompt(
            "Cambiale el título",
            last_generation=last,
            properties=properties,
        )
        self.assertTrue(follow["revising"])
        self.assertEqual(follow["parent_generation_id"], 99)
        self.assertEqual(follow["content_type"], "post")
        story = interpret_prompt(
            "Ahora haceme una historia",
            last_generation=last,
            properties=properties,
        )
        self.assertEqual(story["content_type"], "story")
        self.assertEqual(story["parent_generation_id"], 99)

    def test_http_chat_creates_conversation_and_iterates(self):
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        created = client.post(
            "/marketing/chat",
            data={
                "prompt": "Haceme un post moderno de Libertador 1000 para venderlo.",
                "property_id": str(self.property_id),
            },
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        location = created.headers["Location"]
        self.assertIn("/marketing/c/", location)
        page = client.get(location)
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Libertador 1000", body)
        self.assertIn("Copy de prueba", body)
        self.assertIn("Editar", body)
        self.assertIn("Regenerar", body)
        follow = client.post(
            location,
            data={"prompt": "Cambiale el título"},
            follow_redirects=False,
        )
        self.assertEqual(follow.status_code, 302)
        thread = client.get(location)
        self.assertIn("Cambiale el título", thread.get_data(as_text=True))
        from modules.database.marketing_conversations_repository import (
            last_generation_id_for_conversation,
            list_marketing_conversations,
        )
        from modules.database.marketing_generations_repository import get_marketing_generation

        conversations = list_marketing_conversations(self.org, user_id=self.agent_user_id)
        self.assertEqual(len(conversations), 1)
        last_id = last_generation_id_for_conversation(conversations[0]["id"], self.org)
        child = get_marketing_generation(last_id, self.org)
        self.assertIsNotNone(child["parent_generation_id"])
        other = self._login(self.other_user_id, ROLE_AGENT, agent_id=self.other_agent_id)
        self.assertEqual(other.get(location).status_code, 403)
        foreign = self._login(self.foreign_admin_id, ROLE_ADMIN, organization_id=self.other_org)
        self.assertEqual(foreign.get(location).status_code, 403)

    def test_http_guest_forbidden(self):
        client = app.test_client()
        self.assertEqual(client.get("/marketing").status_code, 403)
        self.assertEqual(client.get("/marketing/create").status_code, 403)
        self.assertEqual(client.get("/marketing/generations").status_code, 403)
        self.assertEqual(client.post("/marketing/chat", data={"prompt": "hola"}).status_code, 403)

    def test_http_agent_cannot_open_other_generation(self):
        owner = self._user(self.agent_user_id)
        generation = create_and_run_generation(self.org, owner, content_type="copy", origin="free")
        client = self._login(self.other_user_id, ROLE_AGENT, agent_id=self.other_agent_id)
        page = client.get(f"/marketing/generations/{generation['id']}")
        self.assertEqual(page.status_code, 403)

    def test_http_other_org_cannot_open_generation(self):
        owner = self._user(self.agent_user_id)
        generation = create_and_run_generation(self.org, owner, content_type="copy", origin="free")
        client = self._login(self.foreign_admin_id, ROLE_ADMIN, organization_id=self.other_org)
        page = client.get(f"/marketing/generations/{generation['id']}")
        self.assertEqual(page.status_code, 403)

    def test_http_regenerate_and_discard(self):
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        created = client.post(
            "/marketing/create",
            data={"content_type": "post", "origin": "office"},
            follow_redirects=False,
        )
        generation_id = int(created.headers["Location"].rstrip("/").split("/")[-1])
        regenerated = client.post(
            f"/marketing/generations/{generation_id}/regenerate",
            follow_redirects=False,
        )
        self.assertEqual(regenerated.status_code, 302)
        child_id = int(regenerated.headers["Location"].rstrip("/").split("/")[-1])
        self.assertNotEqual(child_id, generation_id)
        parent = get_marketing_generation(generation_id, self.org)
        child = get_marketing_generation(child_id, self.org)
        self.assertEqual(parent["status"], "completed")
        self.assertEqual(child["parent_generation_id"], generation_id)
        discarded = client.post(
            f"/marketing/generations/{child_id}/discard",
            follow_redirects=False,
        )
        self.assertEqual(discarded.status_code, 302)
        self.assertEqual(get_marketing_generation(child_id, self.org)["status"], "discarded")

    def test_composer_route_still_exists(self):
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        page = client.get("/marketing/new")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Tipo de pieza", page.get_data(as_text=True))

    def test_staff_cannot_generate(self):
        admin = self._user(self.admin_id)
        with self.assertRaises(MarketingError) as raised:
            create_and_run_generation(
                self.org,
                admin,
                content_type="copy",
                origin="property",
                property_id=self.property_id,
                objective="sell_property",
                style="premium",
                tone="formal",
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_valid_structured_output(self):
        parsed = validate_generation_output(VALID_CAROUSEL, content_type="carousel")
        self.assertEqual(parsed["hook"], "2 ambientes en Victoria")
        self.assertEqual(len(parsed["slides"]), 3)
        copy = validate_generation_output(VALID_COPY, content_type="copy")
        self.assertEqual(copy["headline"], "Departamento en Victoria")

    def test_invalid_structured_output(self):
        with self.assertRaises(MarketingAIError) as raised:
            validate_generation_output({"headline": "Hola"}, content_type="copy")
        self.assertEqual(raised.exception.code, "openai_invalid_response")
        with self.assertRaises(MarketingAIError):
            validate_generation_output(
                {**VALID_COPY, "headline": "La mejor propiedad de la zona"},
                content_type="copy",
            )

    def test_does_not_copy_caption_into_other_fields(self):
        with self.assertRaises(MarketingAIError):
            validate_generation_output(
                {
                    "headline": "Departamento en Victoria",
                    "caption": "Departamento en Victoria",
                    "cta": "Pedí una visita",
                    "hashtags": ["#Victoria"],
                },
                content_type="post",
            )

    def _fake_openai(self, copy_payload, content_type="copy"):
        def _request(**kwargs):
            name = kwargs.get("schema_name")
            if name == "marketing_brief_v2":
                return VALID_BRIEF, {"input_tokens": 10, "output_tokens": 8}
            return copy_payload, {"input_tokens": 12, "output_tokens": 40}

        return patch("modules.marketing_ai.request_marketing_json", side_effect=_request)

    def _run_openai(self, user, **kwargs):
        defaults = {
            "content_type": "copy",
            "origin": "free",
            "provider": OpenAIMarketingProvider(),
        }
        defaults.update(kwargs)
        return create_and_run_generation(self.org, user, **defaults)

    def test_openai_valid_response(self):
        user = self._user(self.agent_user_id)
        with self._fake_openai(VALID_COPY) as mocked:
            generation = self._run_openai(user, origin="property", property_id=self.property_id)
        self.assertEqual(generation["status"], "completed")
        self.assertEqual(generation["provider"], "openai")
        self.assertEqual(generation["input_tokens"], 22)
        self.assertEqual(generation["output_tokens"], 48)
        self.assertEqual(generation["estimated_cost"], 0)
        self.assertEqual(generation["generated_data"]["headline"], "Departamento en Victoria")
        self.assertEqual(generation["generated_data"]["prompt_version"], "marketing_copy_v2")
        self.assertNotIn("story_copy", generation["generated_data"])
        self.assertNotIn("whatsapp_copy", generation["generated_data"])
        self.assertEqual(mocked.call_count, 2)
        brief_call, copy_call = mocked.call_args_list
        self.assertEqual(brief_call.kwargs["schema_name"], "marketing_brief_v2")
        self.assertEqual(copy_call.kwargs["schema_name"], "marketing_copy_v2")
        self.assertIn("creative brief", brief_call.kwargs["instructions"].lower())
        self.assertIn("SOLO un copy", copy_call.kwargs["instructions"])
        self.assertNotIn("sk-", copy_call.kwargs["instructions"])
        payload = brief_call.kwargs["user_payload"]
        self.assertNotIn("commission", str(payload).lower())
        self.assertNotIn("PROPERTY_DATA", str(payload))

    def test_openai_post_does_not_generate_story_or_whatsapp(self):
        user = self._user(self.agent_user_id)
        with self._fake_openai(VALID_POST, content_type="post") as mocked:
            generation = self._run_openai(
                user,
                content_type="post",
                origin="property",
                property_id=self.property_id,
                style="premium",
                tone="exclusive",
            )
        self.assertEqual(generation["status"], "completed")
        data = generation["generated_data"]
        self.assertIn("caption", data)
        self.assertNotIn("frames", data)
        self.assertNotIn("message", data)
        self.assertNotIn("slides", data)
        copy_call = mocked.call_args_list[1]
        instructions = copy_call.kwargs["instructions"]
        self.assertIn("UN post", instructions)
        self.assertIn("No escribas historia", instructions)
        self.assertIn("STYLE premium", instructions)
        self.assertIn("TONE exclusive", instructions)
        self.assertEqual(copy_call.kwargs["schema_name"], "marketing_post_v2")

    def test_openai_invalid_response_marks_failed(self):
        user = self._user(self.agent_user_id)
        with self._fake_openai({"foo": "bar"}):
            generation = self._run_openai(user)
        self.assertEqual(generation["status"], "failed")
        self.assertIn("control de calidad", generation["error_message"])
        self.assertNotIn("openai", generation["error_message"].lower())

    def test_openai_timeout_marks_failed(self):
        user = self._user(self.agent_user_id)
        with patch(
            "modules.marketing_ai.request_marketing_json",
            side_effect=MarketingAIClientError("openai_timeout"),
        ):
            generation = self._run_openai(user)
        self.assertEqual(generation["status"], "failed")
        self.assertIn("intentar nuevamente", generation["error_message"])

    def test_openai_provider_failure_marks_failed(self):
        user = self._user(self.agent_user_id)
        with patch(
            "modules.marketing_ai.request_marketing_json",
            side_effect=MarketingAIClientError("openai_request_failed"),
        ):
            generation = self._run_openai(user)
        self.assertEqual(generation["status"], "failed")
        self.assertNotIn("Traceback", generation["error_message"] or "")
        self.assertNotIn("sk-", generation["error_message"] or "")

    def test_missing_api_key_marks_failed(self):
        user = self._user(self.agent_user_id)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "MARKETING_AI_PROVIDER": "openai"}):
            generation = self._run_openai(user)
        self.assertEqual(generation["status"], "failed")
        self.assertIn("no está configurada", generation["error_message"])

    def test_status_processing_then_completed(self):
        user = self._user(self.agent_user_id)
        row = create_marketing_generation(
            self.org,
            created_by_user_id=user["id"],
            agent_id=self.agent_id,
            content_type="copy",
            origin="free",
            status=STATUS_PROCESSING,
        )
        self.assertEqual(row["status"], "processing")
        done = run_generation(row, language="es", user=user)
        self.assertEqual(done["status"], "completed")
        self.assertTrue(done["completed_at"])

    def test_status_processing_then_failed(self):
        user = self._user(self.agent_user_id)
        row = create_marketing_generation(
            self.org,
            created_by_user_id=user["id"],
            agent_id=self.agent_id,
            content_type="copy",
            origin="free",
            status=STATUS_PROCESSING,
        )
        with patch(
            "modules.marketing_generation_service.generate_content",
            side_effect=MarketingAIError("openai_http_429"),
        ):
            failed = run_generation(row, language="es", user=user)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("intentar nuevamente", failed["error_message"])

    def test_openai_regenerate_keeps_parent(self):
        user = self._user(self.agent_user_id)
        with self._fake_openai(VALID_COPY):
            original = self._run_openai(user)
            child = regenerate_generation(
                self.org,
                user,
                original["id"],
                provider=OpenAIMarketingProvider(),
            )
        self.assertNotEqual(child["id"], original["id"])
        self.assertEqual(child["parent_generation_id"], original["id"])
        self.assertEqual(get_marketing_generation(original["id"], self.org)["status"], "completed")

    def test_marketing_does_not_use_cash_ai_or_jrh_model(self):
        import inspect
        import modules.marketing_ai as marketing_ai

        source = inspect.getsource(marketing_ai)
        self.assertNotIn("cash_ai_provider", source)
        with patch.dict(os.environ, {"JRH_AI_MODEL": "gpt-4o", "MARKETING_AI_MODEL": ""}):
            self.assertEqual(get_marketing_ai_model(), "gpt-4o-mini")
        with patch.dict(os.environ, {"MARKETING_AI_MODEL": "gpt-4o"}):
            self.assertEqual(get_marketing_ai_model(), "gpt-4o")

    def test_prompts_differ_by_format_and_style_tone(self):
        post = build_copy_instructions("post", style="premium", tone="formal")
        story = build_copy_instructions("story", style="dynamic", tone="close")
        whatsapp = build_copy_instructions("whatsapp", style="modern", tone="commercial")
        self.assertIn("UN post", post)
        self.assertNotIn("frames", post.lower())
        self.assertIn("historia vertical", story)
        self.assertNotIn("hashtags", story.lower())
        self.assertIn("mensaje de chat", whatsapp)
        self.assertIn("STYLE premium", post)
        self.assertIn("TONE formal", post)
        self.assertIn("STYLE dynamic", story)
        self.assertIn("TONE close", story)
        self.assertNotEqual(post, story)
        self.assertNotEqual(story, whatsapp)

    def test_rich_listing_facts_reach_payload_without_private_data(self):
        user = self._user(self.agent_user_id)
        property_id = add_property(
            "Libertador 2200",
            "Buenos Aires",
            self.org,
            agent_id=self.agent_id,
            created_by_user_id=self.admin_id,
            rooms=3,
            bedrooms=2,
            bathrooms=1,
            covered_m2=70,
            total_m2=95,
            parking_spaces=1,
            neighborhood="Victoria",
            locality="San Fernando",
            description="Living comedor y pileta.",
            features={"balcony": True, "pool": True, "grill": True},
        )
        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE properties
                SET external_metadata_json = ?
                WHERE id = ? AND organization_id = ?
                """,
                (
                    '{"floor":"8","orientation":"NE","year_build":2018,'
                    '"property_condition":"excelente",'
                    '"commission":{"amount":999},"notes":"interno"}',
                    property_id,
                    self.org,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        from modules.database.properties_repository import get_property_record

        record = get_property_record(property_id, self.org)
        generation = {
            "organization_id": self.org,
            "content_type": "post",
            "origin": "property",
            "objective": "sell_property",
            "style": "premium",
            "tone": "exclusive",
            "format": None,
            "prompt_input": None,
            "property_id": property_id,
            "property_address": record.get("address"),
            "agent_id": self.agent_id,
        }
        context = build_generation_context(
            generation, language="es", property_data=record, user=user
        )
        payload = build_model_payload(context)
        listing = payload["property"]
        self.assertIn("pileta", listing["amenities"])
        self.assertIn("balcón", listing["amenities"])
        self.assertTrue(listing["balcony"])
        self.assertTrue(listing["pool"])
        self.assertEqual(listing["uncovered_m2"], 25)
        self.assertEqual(listing["floor"], "8")
        self.assertEqual(listing["orientation"], "NE")
        self.assertNotIn("heating", listing)
        self.assertNotIn("air_conditioning", listing)
        self.assertNotIn("luminosity", listing)
        blob = str(payload).lower()
        self.assertNotIn("commission", blob)
        self.assertNotIn("999", blob)
        self.assertNotIn("interno", blob)
        self.assertNotIn("property_id", payload["property"])

    def test_variants_hook_exists_but_is_not_required(self):
        self.assertTrue(callable(generate_variants))

    def test_mock_whatsapp_is_not_an_instagram_caption(self):
        result = generate_content(
            "whatsapp",
            {"language": "es", "content_type": "whatsapp", "origin": "free"},
        )
        self.assertNotIn("#", result["message"])
        self.assertNotIn("caption", result)
        self.assertNotIn("hashtags", result)


if __name__ == "__main__":
    unittest.main()
