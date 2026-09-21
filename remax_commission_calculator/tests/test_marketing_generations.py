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

from PIL import Image

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
from modules.database.property_media_repository import STRATEGY_COPY, upsert_property_media
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
from modules.marketing_chat_service import (
    extract_property_query,
    interpret_prompt,
    resolve_property_from_prompt,
)
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

_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"


def _add_listing_photo(organization_id, property_id, name="cover.jpg"):
    relative = f"organizations/{organization_id}/properties/{property_id}/media/{name}"
    path = _PRIVATE_ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1200, 900), (70, 86, 98)).save(path, "JPEG", quality=90)
    upsert_property_media(
        organization_id,
        property_id,
        source="manual",
        external_media_id=name,
        original_url=None,
        storage_key=relative.replace("\\", "/"),
        storage_strategy=STRATEGY_COPY,
        position=0,
        is_cover=True,
        content_type="image/jpeg",
    )


def _add_agent_photo(organization_id, agent_id):
    from modules.database.agents_repository import update_agent_profile_photo

    relative = f"organizations/{organization_id}/agents/{agent_id}/photo.png"
    path = _PRIVATE_ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 520), (18, 72, 48)).save(path, "PNG")
    update_agent_profile_photo(
        agent_id,
        organization_id,
        profile_photo_key=relative.replace("\\", "/"),
        profile_photo_mime="image/png",
        profile_photo_width=400,
        profile_photo_height=520,
    )


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
        _add_listing_photo(cls.org, cls.property_id, "libertador.jpg")
        cls.italia_id = add_property(
            "Italia 220",
            "Palermo",
            cls.org,
            agent_id=cls.agent_id,
            neighborhood="Palermo",
            locality="Palermo",
            created_by_user_id=cls.admin_id,
        )
        _add_listing_photo(cls.org, cls.italia_id, "italia.jpg")
        cls.bare_id = add_property(
            "Sin Fotos 100",
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
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(marketing_conversations)"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertIn("marketing_conversations", names)
        self.assertIn("marketing_messages", names)
        self.assertIn("context_json", columns)

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
        self.assertIn('mkt-chat__empty-bot', body)
        self.assertIn('width="280"', body)
        self.assertIn("jrh_ia_bot_light.png", body)
        self.assertIn("jrh_ia_bot_dark.png", body)
        self.assertNotIn("jrh-ia-hero-light.png", body)
        self.assertNotIn("<h2>Marketing IA</h2>", body)
        self.assertIn("Pedile a JRH una imagen, un copy o una idea", body)
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
        self.assertEqual(first["action"], "generate_post_image")
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
            "generated_data": {"action": "generate_post_image"},
        }
        follow = interpret_prompt(
            "Cambiale el título",
            last_generation=last,
            properties=properties,
        )
        self.assertTrue(follow["revising"])
        self.assertEqual(follow["action"], "edit_existing_generation")
        self.assertEqual(follow["parent_generation_id"], 99)
        self.assertEqual(follow["content_type"], "post")
        story = interpret_prompt(
            "Ahora haceme una historia",
            last_generation=last,
            properties=properties,
        )
        self.assertEqual(story["content_type"], "story")
        self.assertEqual(story["action"], "generate_story_image")
        self.assertEqual(story["parent_generation_id"], 99)
        self.assertEqual(story.get("channel"), "instagram_story")
        self.assertTrue(story.get("channel_explicit"))

    def test_interpret_prompt_chat_vs_visual(self):
        properties = [{"id": 11, "address": "Italia 220", "locality": "Palermo"}]
        chat = interpret_prompt("Dame ideas para una campaña de captación")
        self.assertEqual(chat["action"], "chat")
        self.assertFalse(chat["needs_property"])
        titles = interpret_prompt("Escribime 3 opciones de título")
        self.assertEqual(titles["action"], "chat")
        visual = interpret_prompt(
            "Haceme una publicación para vender la propiedad de Italia",
            properties=properties,
        )
        self.assertEqual(visual["action"], "generate_post_image")
        self.assertEqual(visual["property_id"], 11)
        modern = interpret_prompt(
            "Quiero una versión más moderna",
            last_generation={
                "id": 5,
                "content_type": "post",
                "origin": "property",
                "property_id": 11,
                "generated_data": {"action": "generate_post_image"},
            },
            properties=properties,
        )
        self.assertEqual(modern["action"], "edit_existing_generation")
        combo = interpret_prompt("Quiero una historia y un carrusel", property_id=11)
        self.assertEqual(combo["action"], "generate_carousel")
        convert = interpret_prompt(
            "Ahora convertí la opción 2 en una imagen",
            last_generation={
                "id": 8,
                "content_type": "post",
                "origin": "property",
                "property_id": 11,
                "generated_copy": "Copy de prueba",
                "status": "completed",
                "generated_data": {"action": "generate_post_image", "headline": "Italia"},
            },
            property_id=11,
        )
        self.assertEqual(convert["action"], "generate_visual")
        hacelo = interpret_prompt(
            "hacelo imagen",
            last_generation={
                "id": 9,
                "content_type": "post",
                "origin": "property",
                "property_id": 11,
                "generated_copy": "Copy de prueba",
                "status": "completed",
                "generated_data": {
                    "action": "generate_post_image",
                    "copy_status": "completed",
                    "headline": "Italia",
                },
            },
            property_id=11,
        )
        self.assertEqual(hacelo["action"], "generate_visual")
        self.assertFalse(hacelo["revising"])
        italia = interpret_prompt(
            "creame una publicación de la propiedad de italia",
            properties=properties,
        )
        self.assertEqual(italia["action"], "generate_post_image")
        self.assertEqual(italia["property_id"], 11)
        forced_chat = interpret_prompt(
            "Haceme una publicación",
            property_id=11,
            preferred_mode="chat",
        )
        self.assertEqual(forced_chat["action"], "generate_post_image")
        forced_create = interpret_prompt(
            "algo lindo para esta propiedad",
            property_id=11,
            preferred_mode="create",
        )
        self.assertEqual(forced_create["action"], "generate_post_image")

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
        self.assertIn("Perfecto, armé una primera versión.", body)
        self.assertIn("Copy de prueba", body)
        self.assertIn("mkt-result", body)
        self.assertIn("Editar", body)
        self.assertIn("Regenerar", body)
        self.assertIn("Crear otra versión", body)
        self.assertIn("/marketing/assets/", body)
        self.assertIn("Ver texto de la publicación", body)
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
        )
        from modules.database.marketing_generations_repository import get_marketing_generation

        conversation_id = int(location.rstrip("/").split("/")[-1])
        last_id = last_generation_id_for_conversation(conversation_id, self.org)
        child = get_marketing_generation(last_id, self.org)
        self.assertIsNotNone(child["parent_generation_id"])
        other = self._login(self.other_user_id, ROLE_AGENT, agent_id=self.other_agent_id)
        self.assertEqual(other.get(location).status_code, 403)
        foreign = self._login(self.foreign_admin_id, ROLE_ADMIN, organization_id=self.other_org)
        self.assertEqual(foreign.get(location).status_code, 403)

    def test_http_chat_italia_visual_and_hacelo_imagen(self):
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        created = client.post(
            "/marketing/chat",
            data={"prompt": "creame una publicación de la propiedad de italia"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        location = created.headers["Location"]
        asked = client.get(location).get_data(as_text=True)
        self.assertIn("Perfecto, armé una primera versión.", asked)
        self.assertIn("Copy de prueba", asked)
        self.assertNotIn("¿Qué estilo querés?", asked)
        page = client.get(location)
        body = page.get_data(as_text=True)
        self.assertIn("Perfecto, armé una primera versión.", body)
        self.assertIn("Copy de prueba", body)
        self.assertIn("mkt-result__gallery", body)
        self.assertIn("/marketing/assets/", body)
        self.assertIn("Ver texto de la publicación", body)
        self.assertNotIn("No pude generar la imagen.", body)
        from modules.database.marketing_conversations_repository import (
            last_generation_id_for_conversation,
            get_marketing_conversation,
        )

        conversation_id = int(location.rstrip("/").split("/")[-1])
        first_id = last_generation_id_for_conversation(conversation_id, self.org)
        first = get_marketing_generation(first_id, self.org)
        data = first.get("generated_data") or {}
        self.assertEqual(data.get("copy_status"), "completed")
        self.assertEqual(data.get("visual_status"), "completed")
        self.assertEqual(data.get("template_used"), "modern_commercial_v3")
        self.assertEqual(data.get("renderer_used"), "html_playwright")
        self.assertTrue(data.get("visual_asset_url"))
        before = len(list_marketing_generations(self.org))
        follow = client.post(
            location,
            data={"prompt": "hacelo imagen"},
            follow_redirects=False,
        )
        self.assertEqual(follow.status_code, 302)
        thread = client.get(location).get_data(as_text=True)
        self.assertNotIn("Listo. Ajusto la pieza con lo que pediste.", thread)
        self.assertIn("Perfecto, armé una primera versión.", thread)
        self.assertIn("mkt-result__gallery", thread)
        self.assertEqual(len(list_marketing_generations(self.org)), before)
        second_id = last_generation_id_for_conversation(conversation_id, self.org)
        self.assertEqual(second_id, first_id)
        second = get_marketing_generation(second_id, self.org)
        self.assertEqual((second.get("generated_data") or {}).get("visual_status"), "completed")
        conversation = get_marketing_conversation(conversation_id, self.org)
        self.assertEqual(conversation["property_id"], self.italia_id)

    def test_resolve_property_from_natural_language(self):
        unique = [
            {
                "id": 11,
                "address": "Av. Italia 1234",
                "locality": "Palermo",
                "neighborhood": "Palermo",
                "jurisdiction": "CABA",
                "title": "Propiedad Italia",
                "description": "Departamento en Palermo.",
            }
        ]
        self.assertEqual(extract_property_query("creame una publicación de la propiedad de italia"), "italia")
        self.assertEqual(extract_property_query("Haceme una publicación de Italia con mis datos"), "italia")
        self.assertIsNone(extract_property_query("Haceme otra publicación"))
        self.assertIsNone(extract_property_query("Ahora haceme una historia"))
        resolved = resolve_property_from_prompt("la propiedad de Italia", unique)
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["property"]["id"], 11)
        exact = interpret_prompt("Haceme una publicidad de Don Bosco 477", properties=[
            {"id": 7, "address": "Don Bosco 477", "locality": "Victoria"}
        ])
        self.assertEqual(exact["property_id"], 7)
        self.assertEqual(exact["property_status"], "resolved")
        partial = interpret_prompt(
            "armame una pieza de la de Santamarina",
            properties=[{"id": 9, "address": "Santamarina 1335", "neighborhood": "Victoria"}],
        )
        self.assertEqual(partial["property_id"], 9)
        many = [
            {"id": 21, "address": "Italia 123", "locality": "Victoria", "rooms": 2, "listing_price": 100000, "listing_currency": "USD"},
            {"id": 22, "address": "Italia 456", "locality": "San Isidro", "rooms": 3, "listing_price": 180000, "listing_currency": "USD"},
        ]
        ambiguous = interpret_prompt("creame una publicación de la propiedad de Italia", properties=many)
        self.assertIsNone(ambiguous["property_id"])
        self.assertEqual(ambiguous["property_status"], "ambiguous")
        self.assertEqual(len(ambiguous["property_matches"]), 2)
        with_photos = [
            {**many[0], "photo_count": 0, "has_photos": False},
            {**many[1], "photo_count": 4, "has_photos": True, "cover_url": "/covers/22.jpg"},
        ]
        picked = interpret_prompt("creame una publicación de la propiedad de Italia", properties=with_photos)
        self.assertEqual(picked["property_status"], "resolved")
        self.assertEqual(picked["property_id"], 22)
        self.assertTrue(picked["property_auto_picked"])
        both_photos = [
            {**many[0], "id": 31, "photo_count": 3, "has_photos": True, "cover_url": "/covers/31.jpg"},
            {**many[1], "id": 32, "photo_count": 3, "has_photos": True, "cover_url": "/covers/32.jpg"},
        ]
        tied = interpret_prompt("creame una publicación de la propiedad de Italia", properties=both_photos)
        self.assertIsNone(tied["property_id"])
        self.assertEqual(tied["property_status"], "ambiguous")
        missing = interpret_prompt(
            "creame una publicación de la propiedad de Italia",
            properties=[{"id": 3, "address": "Libertador 1000", "locality": "Buenos Aires"}],
        )
        self.assertIsNone(missing["property_id"])
        self.assertEqual(missing["property_status"], "none")
        other_agent = interpret_prompt(
            "creame una publicación de la propiedad de Italia",
            properties=[{"id": 44, "address": "Cabildo 200", "agent_id": 99}],
        )
        self.assertEqual(other_agent["property_status"], "none")
        self.assertIsNone(other_agent["property_id"])

    def test_http_chat_ambiguous_and_missing_property(self):
        from modules.database import add_property
        from modules.database.marketing_conversations_repository import get_marketing_conversation, list_marketing_conversations
        from modules.database.marketing_generations_repository import list_marketing_generations

        add_property(
            "Belgrano 100",
            "CABA",
            self.org,
            agent_id=self.agent_id,
            neighborhood="Belgrano",
            locality="Belgrano",
            created_by_user_id=self.admin_id,
        )
        add_property(
            "Belgrano 200",
            "CABA",
            self.org,
            agent_id=self.agent_id,
            neighborhood="Belgrano",
            locality="Belgrano",
            created_by_user_id=self.admin_id,
        )
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        before = len(list_marketing_generations(self.org))
        created = client.post(
            "/marketing/chat",
            data={"prompt": "creame una publicación de la propiedad de Belgrano"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        body = client.get(created.headers["Location"]).get_data(as_text=True)
        self.assertIn("varias propiedades relacionadas con Belgrano", body)
        self.assertIn("Belgrano 100", body)
        self.assertIn("Belgrano 200", body)
        self.assertNotIn("Copy de prueba", body)
        self.assertEqual(len(list_marketing_generations(self.org)), before)
        other = self._login(self.other_user_id, ROLE_AGENT, agent_id=self.other_agent_id)
        missing = other.post(
            "/marketing/chat",
            data={"prompt": "creame una publicación de la propiedad de italia"},
            follow_redirects=False,
        )
        missing_body = other.get(missing.headers["Location"]).get_data(as_text=True)
        self.assertIn("No encontré una propiedad llamada Italia", missing_body)
        self.assertIn("Buscar propiedad", missing_body)
        self.assertNotIn("Copy de prueba", missing_body)
        conversations = list_marketing_conversations(self.org, user_id=self.other_user_id)
        conversation = get_marketing_conversation(conversations[0]["id"], self.org)
        self.assertIsNone(conversation.get("property_id"))
        foreign = self._login(self.foreign_admin_id, ROLE_ADMIN, organization_id=self.other_org)
        self.assertEqual(
            foreign.post(
                "/marketing/chat",
                data={"prompt": "creame una publicación de la propiedad de italia"},
            ).status_code,
            403,
        )

    def test_http_chat_visual_missing_photos_is_specific(self):
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        created = client.post(
            "/marketing/chat",
            data={
                "prompt": "Haceme una publicación premium de Sin Fotos 100, con mis datos",
                "property_id": str(self.bare_id),
            },
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        body = client.get(created.headers["Location"]).get_data(as_text=True)
        self.assertIn("esta propiedad no tiene fotos", body)
        self.assertIn("Reintentar", body)
        self.assertNotIn("Armé el contenido, pero la imagen no quedó lista", body)

    def test_http_chat_ideas_stay_conversational(self):
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        created = client.post(
            "/marketing/chat",
            data={"prompt": "Dame ideas para una campaña de captación"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        page = client.get(created.headers["Location"])
        body = page.get_data(as_text=True)
        self.assertIn("Dame ideas para una campaña de captación", body)
        self.assertIn("Puedo ayudarte con ideas", body)
        self.assertNotIn("mkt-result__gallery", body)
        self.assertNotIn("Perfecto, armé una primera versión.", body)

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


    def test_smart_question_layer_rules(self):
        from modules.marketing_chat_decisions import (
            apply_intelligent_defaults,
            determine_missing_decisions,
            parse_prompt_decisions,
            resolve_context,
        )

        listing = {
            "address": "Italia 220",
            "purpose": "sale",
            "agent": {"name": "Ana Gen", "has_photo": True, "whatsapp": "123"},
            "photos": [{"id": 1, "is_cover": True}],
            "has_cover": True,
        }
        intent = {
            "action": "generate_post_image",
            "origin": "property",
            "property_id": 11,
            "channel_explicit": True,
            "channel": "instagram_post",
            "property_status": "resolved",
        }
        context = resolve_context(intent, None, "Haceme una publicación de Italia", listing)
        self.assertEqual(determine_missing_decisions(context, listing, intent), [])
        specified = parse_prompt_decisions("Haceme una publicación de Italia con mis datos")
        self.assertTrue(specified["include_agent"])
        context = resolve_context(
            {**intent, "include_agent": True, "creative_style": None},
            None,
            "Haceme una publicación de Italia con mis datos",
            listing,
        )
        missing = determine_missing_decisions(context, listing, intent)
        self.assertEqual(missing, [])
        full = resolve_context(
            {
                **intent,
                "include_agent": True,
                "style": "premium",
                "creative_style": "premium",
                "channel_explicit": True,
            },
            None,
            "Haceme una publicación premium para Instagram de Italia, con mi foto y mis datos.",
            listing,
        )
        self.assertEqual(determine_missing_decisions(full, listing, intent), [])
        auto = apply_intelligent_defaults({"style": "auto", "channel": "auto", "include_agent": "auto"}, listing)
        self.assertEqual(auto["style"], "commercial")
        self.assertEqual(auto["channel"], "instagram_post")
        self.assertTrue(auto["include_agent"])
        self.assertEqual(auto["layout_template"], "modern_commercial_v3")
        switched = resolve_context(
            {
                "action": "generate_story_image",
                "origin": "property",
                "property_id": 11,
                "channel": "instagram_story",
                "channel_explicit": True,
                "content_type": "story",
            },
            {
                "property_id": 11,
                "context": {
                    "include_agent": False,
                    "channel": "instagram_post",
                    "style": "commercial",
                    "asked": ["include_agent", "style", "channel"],
                },
            },
            "Ahora haceme una historia",
            listing,
        )
        self.assertEqual(switched["channel"], "instagram_story")
        self.assertFalse(switched["include_agent"])
        self.assertEqual(switched["style"], "commercial")

    def test_http_chat_smart_questions(self):
        from modules.database.marketing_conversations_repository import (
            get_marketing_conversation,
            last_generation_id_for_conversation,
        )
        from modules.database.marketing_generations_repository import (
            get_marketing_generation,
            list_marketing_generations,
        )

        _add_agent_photo(self.org, self.agent_id)
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        before = len(list_marketing_generations(self.org))
        created = client.post(
            "/marketing/chat",
            data={"prompt": "creame una publicación de Italia"},
            follow_redirects=False,
        )
        location = created.headers["Location"]
        body = client.get(location).get_data(as_text=True)
        self.assertIn("Perfecto, armé una primera versión.", body)
        self.assertIn("Copy de prueba", body)
        self.assertNotIn("¿Querés que la publicación salga con tus datos y tu foto?", body)
        self.assertNotIn("¿Qué estilo querés?", body)
        self.assertGreater(len(list_marketing_generations(self.org)), before)
        conversation_id = int(location.rstrip("/").split("/")[-1])
        conversation = get_marketing_conversation(conversation_id, self.org)
        context = conversation.get("context") or {}
        self.assertEqual(conversation["property_id"], self.italia_id)
        self.assertTrue(context.get("include_agent"))
        self.assertEqual(context.get("style"), "commercial")
        self.assertEqual(context.get("layout_template"), "modern_commercial_v3")
        first_id = last_generation_id_for_conversation(conversation_id, self.org)
        first = get_marketing_generation(first_id, self.org)
        self.assertEqual((first.get("generated_data") or {}).get("template_used"), "modern_commercial_v3")

        premium = client.post(
            location,
            data={"prompt": "hacelo premium"},
            follow_redirects=False,
        )
        self.assertEqual(premium.status_code, 302)
        premium_body = client.get(location).get_data(as_text=True)
        self.assertIn("Copy de prueba", premium_body)
        self.assertNotIn("¿Cuál querés usar?", premium_body)
        conversation = get_marketing_conversation(conversation_id, self.org)
        context = conversation.get("context") or {}
        self.assertEqual(conversation["property_id"], self.italia_id)
        self.assertEqual(context.get("style"), "premium")
        self.assertEqual(context.get("layout_template"), "modern_commercial_v3")
        premium_id = last_generation_id_for_conversation(conversation_id, self.org)
        premium_gen = get_marketing_generation(premium_id, self.org)
        self.assertEqual((premium_gen.get("generated_data") or {}).get("template_used"), "modern_commercial_v3")

        again = client.post(
            location,
            data={"prompt": "Haceme otra publicación de Italia"},
            follow_redirects=False,
        )
        self.assertEqual(again.status_code, 302)
        again_body = client.get(location).get_data(as_text=True)
        tail = again_body.split("Haceme otra publicación de Italia")[-1]
        self.assertNotIn("¿Cuál querés usar?", tail)
        self.assertNotIn("varias propiedades relacionadas", tail)
        conversation = get_marketing_conversation(conversation_id, self.org)
        self.assertEqual(conversation["property_id"], self.italia_id)

    def test_http_chat_italia_twins_keep_same_property_id_for_copy_and_visual(self):
        """Two listings share address; only one has photos. Pipeline must stick to that id."""
        from modules.database import add_property
        from modules.database.marketing_conversations_repository import (
            get_marketing_conversation,
            last_generation_id_for_conversation,
        )
        from modules.database.marketing_generations_repository import get_marketing_generation
        from modules.marketing_context import build_property_marketing_context
        from modules.marketing_chat_service import interpret_prompt, resolve_property_from_prompt

        bare_twin = add_property(
            "Italia 1341",
            "Martínez",
            self.org,
            agent_id=self.agent_id,
            neighborhood="Martínez",
            locality="Martínez",
            created_by_user_id=self.admin_id,
        )
        photo_twin = add_property(
            "Italia 1341",
            "Martínez",
            self.org,
            agent_id=self.agent_id,
            neighborhood="Martínez",
            locality="Martínez",
            created_by_user_id=self.admin_id,
        )
        _add_listing_photo(self.org, photo_twin, "italia-twin.jpg")
        self.assertNotEqual(bare_twin, photo_twin)

        properties = [
            {
                "id": bare_twin,
                "address": "Italia 1341",
                "locality": "Martínez",
                "neighborhood": "Martínez",
                "photo_count": 0,
                "has_photos": False,
            },
            {
                "id": photo_twin,
                "address": "Italia 1341",
                "locality": "Martínez",
                "neighborhood": "Martínez",
                "photo_count": 3,
                "has_photos": True,
                "cover_url": "/covers/twin.jpg",
            },
        ]
        resolved = resolve_property_from_prompt("Haceme una publicación de Italia", properties)
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["property"]["id"], photo_twin)
        self.assertTrue(resolved["auto_picked"])
        intent = interpret_prompt("Haceme una publicación de Italia", properties=properties)
        self.assertEqual(intent["property_id"], photo_twin)

        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        created = client.post(
            "/marketing/chat",
            data={"prompt": "Haceme una publicación de Italia 1341"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        location = created.headers["Location"]
        body = client.get(location).get_data(as_text=True)
        self.assertIn("tiene fotos", body.lower())
        self.assertNotIn("esta propiedad no tiene fotos", body.lower())
        self.assertIn("mkt-result__gallery", body)

        conversation_id = int(location.rstrip("/").split("/")[-1])
        conversation = get_marketing_conversation(conversation_id, self.org)
        self.assertEqual(conversation["property_id"], photo_twin)
        last_id = last_generation_id_for_conversation(conversation_id, self.org)
        generation = get_marketing_generation(last_id, self.org)
        self.assertEqual(generation["property_id"], photo_twin)
        self.assertEqual(
            (generation.get("generated_data") or {}).get("resolved_property_id"),
            photo_twin,
        )
        self.assertEqual(
            (generation.get("generated_data") or {}).get("visual_status"),
            "completed",
        )
        photo_count = build_property_marketing_context(
            {"id": photo_twin, "organization_id": self.org, "address": "Italia 1341"},
            language="es",
        ).get("photo_count")
        self.assertGreater(photo_count, 0)

        # Retry must keep the same generation property_id (never re-resolve by text).
        retry = client.post(
            f"/marketing/c/{conversation_id}/regenerate",
            data={"generation_id": str(last_id), "visual_only": "1"},
            follow_redirects=False,
        )
        self.assertEqual(retry.status_code, 302)
        retried = get_marketing_generation(last_id, self.org)
        self.assertEqual(retried["property_id"], photo_twin)
        conversation = get_marketing_conversation(conversation_id, self.org)
        self.assertEqual(conversation["property_id"], photo_twin)


    def test_http_chat_italia_auto_picks_listing_with_photos(self):
        from modules.database import add_property
        from modules.database.marketing_conversations_repository import (
            get_marketing_conversation,
            last_generation_id_for_conversation,
        )
        from modules.database.marketing_generations_repository import get_marketing_generation

        add_property(
            "Italia 900",
            "Martínez",
            self.org,
            agent_id=self.agent_id,
            neighborhood="Martínez",
            locality="Martínez",
            created_by_user_id=self.admin_id,
        )
        client = self._login(self.agent_user_id, ROLE_AGENT, agent_id=self.agent_id)
        created = client.post(
            "/marketing/chat",
            data={"prompt": "creame una publicación de la propiedad de Italia"},
            follow_redirects=False,
        )
        location = created.headers["Location"]
        body = client.get(location).get_data(as_text=True)
        self.assertIn("Perfecto, armé una primera versión.", body)
        self.assertIn("tiene fotos", body.lower())
        self.assertNotIn("¿Cuál querés usar?", body)
        conversation = get_marketing_conversation(int(location.rstrip("/").split("/")[-1]), self.org)
        self.assertEqual(conversation["property_id"], self.italia_id)
        last_id = last_generation_id_for_conversation(conversation["id"], self.org)
        generation = get_marketing_generation(last_id, self.org)
        self.assertEqual((generation.get("generated_data") or {}).get("template_used"), "modern_commercial_v3")



if __name__ == "__main__":
    unittest.main()
