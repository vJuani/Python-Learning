"""Marketing IA: every listing visual renders the 3 property_* designs from one payload."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_marketing_render_routing.db")
os.environ.pop("DATABASE_URL", None)
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ["MARKETING_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from unittest.mock import patch

from PIL import Image

import modules.marketing_chat_service as chat_service
import modules.marketing_service as marketing_service
import modules.property_marketing as property_marketing
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.marketing_conversations_repository import (
    get_marketing_conversation,
    last_generation_id_for_conversation,
    list_marketing_messages,
    update_marketing_conversation,
)
from modules.database.marketing_generations_repository import (
    get_marketing_generation,
    update_marketing_generation,
)
from modules.database.marketing_repository import (
    STATUS_SELECTED,
    get_marketing_asset,
    list_generation_assets,
    update_marketing_asset,
)
from modules.database.property_media_repository import STRATEGY_COPY, upsert_property_media
from modules.marketing_service import _render_local_visual
from modules.property_marketing import (
    LEGACY_ROUTE_BLOCKED,
    PropertyMarketingError,
    PropertyMarketingRouteBlocked,
    assert_property_route,
    normalize_property_design,
    resolve_property_marketing_template,
)
from web_app import app

VARIANTS = ["property_clean_grid", "property_lifestyle_dark", "property_premium_hero"]
PROPERTY_TEMPLATES = set(VARIANTS)
LEGACY_MARKERS = (
    "modern_commercial_v1",
    "modern_commercial_v2",
    "modern_commercial_v3",
    "pillow_commercial_v2",
    "stamp_branding_overlay",
)
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"


def _add_listing_photo(organization_id, property_id, name, color, position):
    relative = f"organizations/{organization_id}/properties/{property_id}/media/{name}"
    path = _PRIVATE_ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 1200), color).save(path, "JPEG", quality=90)
    upsert_property_media(
        organization_id,
        property_id,
        source="manual",
        external_media_id=name,
        original_url=None,
        storage_key=relative,
        storage_strategy=STRATEGY_COPY,
        position=position,
        is_cover=position == 0,
        content_type="image/jpeg",
    )


class PropertyVariantsFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="render-routing-test", SESSION_COOKIE_SECURE=False)
        create_tables()
        cls.org = add_organization("Routing Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Jose Barreiro", "Alto", cls.org)
        cls.admin_id = add_user("routing_admin", pwd, ROLE_ADMIN, cls.org, email="routing.admin@example.com")
        cls.agent_user_id = add_user(
            "routing_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Jose",
            last_name="Barreiro",
            email="routing.agent@example.com",
        )
        cls.property_id = add_property(
            "Santamarina 1335",
            "Victoria",
            cls.org,
            agent_id=cls.agent_id,
            neighborhood="Victoria",
            locality="San Fernando",
            created_by_user_id=cls.admin_id,
        )
        colors = ((88, 96, 104), (140, 120, 96), (60, 90, 70), (120, 130, 150), (150, 100, 90))
        for index, color in enumerate(colors):
            _add_listing_photo(cls.org, cls.property_id, f"santamarina_{index}.jpg", color, index)

    def _client(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.agent_user_id
            session["role"] = ROLE_AGENT
            session["organization_id"] = self.org
            session["agent_id"] = self.agent_id
        return client

    def _last_generation(self, conversation_id):
        return get_marketing_generation(
            last_generation_id_for_conversation(conversation_id, self.org), self.org
        )

    def _chat(self, prompt, *, conversation_id=None, client=None):
        client = client or self._client()
        url = f"/marketing/c/{conversation_id}" if conversation_id else "/marketing/chat"
        response = client.post(url, data={"prompt": prompt}, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        conversation_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        return client, conversation_id, self._last_generation(conversation_id)

    def _post(self, client, url, data):
        response = client.post(url, data=data, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])

    def _assert_no_legacy(self, *payloads):
        blob = json.dumps(payloads, ensure_ascii=False, default=str)
        for marker in LEGACY_MARKERS:
            self.assertNotIn(marker, blob)

    def _assert_variants(self, generation, expected):
        data = generation.get("generated_data") or {}
        self.assertEqual(data.get("visual_status"), "completed", data)
        variants = data.get("visual_variants") or []
        self.assertEqual([row["template_used"] for row in variants], expected)
        group_id = data.get("generation_group_id")
        self.assertTrue(group_id)
        assets = list_generation_assets(self.org, group_id)
        self.assertEqual(len(assets), len(expected))
        for row in variants:
            self.assertEqual(row["status"], "ready", row)
            self.assertEqual(row["renderer_used"], "html_playwright")
            self.assertEqual(row["layout_version"], row["template_used"])
            self.assertEqual(row["property_id"], self.property_id)
            self.assertEqual(row["generation_group_id"], group_id)
            self.assertTrue(row["variant_id"])
            options = get_marketing_asset(row["asset_id"], self.org).get("options") or {}
            self.assertEqual(options.get("variant_id"), row["variant_id"])
            self.assertEqual(options.get("template_used"), row["template_used"])
            self.assertEqual(options.get("renderer_used"), "html_playwright")
            self.assertEqual(options.get("layout_version"), row["template_used"])
            self.assertEqual(options.get("generation_group_id"), group_id)
            self.assertEqual(options.get("property_id"), self.property_id)
        self.assertEqual(len({row["variant_id"] for row in variants}), len(expected))
        self._assert_no_legacy(data, [asset.get("options") for asset in assets], [a.get("template") for a in assets])
        return data, assets

    def test_post_renders_three_variants_from_one_payload(self):
        real_copy = chat_service.create_and_run_generation
        real_load = property_marketing.load_original_media_bytes
        copy_calls = []
        photo_loads = []

        def _count_copy(*args, **kwargs):
            copy_calls.append(kwargs.get("content_type"))
            return real_copy(*args, **kwargs)

        def _count_load(item, **kwargs):
            photo_loads.append((item or {}).get("storage_key") or (item or {}).get("id"))
            return real_load(item, **kwargs)

        with patch.object(chat_service, "create_and_run_generation", _count_copy), patch.object(
            property_marketing, "load_original_media_bytes", _count_load
        ):
            client, conversation_id, generation = self._chat("Haceme un post de Santamarina 1335")

        self.assertEqual(len(copy_calls), 1, "copy/brief must be generated once for the 3 designs")
        self.assertTrue(photo_loads)
        self.assertEqual(len(photo_loads), len(set(photo_loads)), "each original photo is read once")

        data, assets = self._assert_variants(generation, VARIANTS)
        self.assertIsNone(data.get("selected_template"))
        self.assertEqual({asset["property_id"] for asset in assets}, {self.property_id})
        self.assertEqual({asset["format"] for asset in assets}, {"post"})
        copies = {json.dumps(asset.get("copy_snapshot"), sort_keys=True) for asset in assets}
        self.assertEqual(len(copies), 1, "same headline/subtitle/CTA on every variant")
        photos = {tuple((asset.get("options") or {}).get("photo_ids") or []) for asset in assets}
        self.assertEqual(len(photos), 1, "same original photos on every variant")

        messages = list_marketing_messages(conversation_id, self.org)
        self.assertIn("Preparé 3 propuestas para Santamarina 1335.", messages[-1]["content"])
        page = client.get(f"/marketing/c/{conversation_id}").get_data(as_text=True)
        for label in ("Clean Grid", "Lifestyle Dark", "Premium Hero"):
            self.assertIn(label, page)
        self.assertEqual(page.count("Elegir este diseño"), 3)
        self.assertIn("data-mkt-zoom", page)
        self.assertNotIn('name="layout_template"', page)

    def test_select_premium_then_regenerate_only_premium(self):
        client, conversation_id, generation = self._chat("Haceme un post de Santamarina 1335")
        data, _assets = self._assert_variants(generation, VARIANTS)
        premium = next(row for row in data["visual_variants"] if row["template_used"] == "property_premium_hero")

        self._post(
            client,
            f"/marketing/c/{conversation_id}/select-variant",
            {"generation_id": generation["id"], "asset_id": premium["asset_id"]},
        )
        chosen = get_marketing_generation(generation["id"], self.org)
        chosen_data = chosen.get("generated_data") or {}
        self.assertEqual(chosen_data.get("selected_template"), "property_premium_hero")
        self.assertEqual(chosen_data.get("selected_asset_id"), premium["asset_id"])
        self.assertEqual(chosen_data.get("template_used"), "property_premium_hero")
        self.assertEqual(len(chosen_data.get("visual_variants") or []), 3, "the other two are kept")
        self.assertEqual(get_marketing_asset(premium["asset_id"], self.org)["status"], STATUS_SELECTED)
        context = get_marketing_conversation(conversation_id, self.org).get("context") or {}
        self.assertEqual(context.get("selected_template"), "property_premium_hero")
        self.assertEqual(context.get("primary_generation_id"), generation["id"])
        self.assertEqual(self._last_generation(conversation_id)["id"], generation["id"])
        page = client.get(f"/marketing/c/{conversation_id}").get_data(as_text=True)
        for action in ("Descargar PNG", "Regenerar", "Cambiar texto", "Cambiar CTA", "Cambiar fotos", "Crear otra versión"):
            self.assertIn(action, page)

        self._post(client, f"/marketing/c/{conversation_id}/regenerate", {"generation_id": generation["id"]})
        regenerated = self._last_generation(conversation_id)
        self.assertNotEqual(regenerated["id"], generation["id"])
        regen_data, _ = self._assert_variants(regenerated, ["property_premium_hero"])
        self.assertEqual(regen_data.get("selected_template"), "property_premium_hero")
        original = get_marketing_generation(generation["id"], self.org)
        self.assertEqual(len((original.get("generated_data") or {}).get("visual_variants") or []), 3)

        self._post(
            client,
            f"/marketing/c/{conversation_id}/regenerate",
            {"generation_id": regenerated["id"], "change_photos": "1"},
        )
        new_photos = self._last_generation(conversation_id)
        photos_data, _ = self._assert_variants(new_photos, ["property_premium_hero"])
        self.assertEqual(photos_data.get("photo_offset"), 1)

        _client, _conversation_id, edited = self._chat(
            "Cambiá el CTA por Consultame hoy", conversation_id=conversation_id, client=client
        )
        self._assert_variants(edited, ["property_premium_hero"])

        _client, _conversation_id, again = self._chat(
            "Mostrame las tres de nuevo", conversation_id=conversation_id, client=client
        )
        again_data, _ = self._assert_variants(again, VARIANTS)
        self.assertIsNone(again_data.get("selected_template"))

    def test_failed_variant_is_shown_and_never_replaced_by_legacy(self):
        real_render = marketing_service.render_property_marketing

        def _render(context, fmt, *, options=None, **kwargs):
            if (options or {}).get("variant_template") == "property_lifestyle_dark":
                raise PropertyMarketingError("PROPERTY_MARKETING_RENDER_FAILED", 500)
            return real_render(context, fmt, options=options, **kwargs)

        def _boom(*_args, **_kwargs):
            raise AssertionError("legacy renderer must not run")

        with patch.object(marketing_service, "render_property_marketing", _render), patch.object(
            marketing_service, "stamp_branding_overlay", _boom
        ):
            client, conversation_id, generation = self._chat("Haceme un post de Santamarina 1335")
        data = generation.get("generated_data") or {}
        self.assertEqual(data.get("visual_status"), "completed")
        rows = {row["template_used"]: row for row in data["visual_variants"]}
        self.assertEqual(list(rows), VARIANTS)
        self.assertEqual(rows["property_lifestyle_dark"]["status"], "failed")
        self.assertEqual(rows["property_clean_grid"]["status"], "ready")
        self.assertEqual(rows["property_premium_hero"]["status"], "ready")
        self.assertEqual(data.get("visual_failed_variants"), ["property_lifestyle_dark"])
        self.assertEqual(len(data.get("visual_asset_ids") or []), 2)
        self._assert_no_legacy(data)
        page = client.get(f"/marketing/c/{conversation_id}").get_data(as_text=True)
        self.assertIn("Esta propuesta no se pudo generar.", page)
        self.assertEqual(page.count("Elegir este diseño"), 2)

    def test_story_and_hacelo_imagen_render_three_property_variants(self):
        client, conversation_id, generation = self._chat("Haceme una historia de Santamarina 1335")
        _data, assets = self._assert_variants(generation, VARIANTS)
        self.assertEqual({asset["format"] for asset in assets}, {"story"})
        _client, _conversation_id, again = self._chat("hacelo imagen", conversation_id=conversation_id, client=client)
        self._assert_variants(again, VARIANTS)
        self.assertEqual(len((get_marketing_generation(generation["id"], self.org).get("generated_data") or {}).get("visual_variants") or []), 3)

    def test_regenerate_legacy_generation_migrates_to_property(self):
        client, conversation_id, generation = self._chat("Haceme un post de Santamarina 1335")
        legacy_asset = get_marketing_asset((generation.get("generated_data") or {})["visual_asset_ids"][0], self.org)
        old_png = (_PRIVATE_ROOT / legacy_asset["storage_key"]).read_bytes()
        legacy_options = dict(legacy_asset.get("options") or {})
        legacy_options.update(
            layout_template="modern_commercial_v2",
            template="modern_commercial_v2",
            template_used="modern_commercial_v2",
            renderer_used="pillow_commercial_v2",
            layout_version="modern_commercial_v2",
            design_requested="modern_commercial_v2",
        )
        update_marketing_asset(legacy_asset["id"], self.org, options_json=legacy_options)
        legacy_data = {
            key: value
            for key, value in (generation.get("generated_data") or {}).items()
            if key not in {"visual_variants", "generation_group_id", "selected_template", "selected_asset_id"}
        }
        legacy_data.update(
            visual_asset_ids=[legacy_asset["id"]],
            template_used="modern_commercial_v2",
            renderer_used="pillow_commercial_v2",
            layout_version="modern_commercial_v2",
            layout_template="modern_commercial_v2",
        )
        update_marketing_generation(generation["id"], self.org, generated_data=legacy_data)
        conversation = get_marketing_conversation(conversation_id, self.org)
        context = dict(conversation.get("context") or {})
        context["layout_template"] = "modern_commercial_v2"
        update_marketing_conversation(conversation_id, self.org, context=context)

        self._post(
            client,
            f"/marketing/c/{conversation_id}/regenerate",
            {"generation_id": generation["id"], "visual_only": "1"},
        )
        regenerated = self._last_generation(conversation_id)
        data, _assets = self._assert_variants(regenerated, VARIANTS)
        self.assertNotIn(legacy_asset["id"], data.get("visual_asset_ids") or [])

        response = client.post(f"/marketing/assets/{legacy_asset['id']}/regenerate", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        from modules.database.marketing_repository import list_marketing_assets

        newest = max(list_marketing_assets(self.org, limit=200), key=lambda item: item["id"])
        self.assertGreater(newest["id"], legacy_asset["id"])
        options = newest.get("options") or {}
        self.assertIn(options.get("template_used"), PROPERTY_TEMPLATES)
        self.assertEqual(options.get("renderer_used"), "html_playwright")

        history = get_marketing_asset(legacy_asset["id"], self.org)
        self.assertEqual((history.get("options") or {}).get("renderer_used"), "pillow_commercial_v2")
        self.assertEqual((_PRIVATE_ROOT / history["storage_key"]).read_bytes(), old_png)


class RoutingGuardTests(unittest.TestCase):
    def test_design_normalization(self):
        self.assertEqual(normalize_property_design("Clean Grid"), "property_clean_grid")
        self.assertEqual(normalize_property_design("Lifestyle Dark"), "property_lifestyle_dark")
        self.assertEqual(normalize_property_design("Premium Hero"), "property_premium_hero")
        self.assertEqual(normalize_property_design("Automático"), "automatic")
        for legacy in ("modern_commercial_v2", "modern_commercial_v3", "marketing_post_v2", "pillow_commercial_v2", "", None):
            self.assertEqual(normalize_property_design(legacy), "automatic")

    def test_automatic_resolves_only_property_ids(self):
        for legacy in ("modern_commercial_v2", "automatic", None):
            self.assertIn(resolve_property_marketing_template(legacy, []), PROPERTY_TEMPLATES)

    def test_variant_list_rejects_legacy_and_automatic(self):
        with self.assertRaises(PropertyMarketingRouteBlocked):
            marketing_service._resolve_variant_templates(
                ["property_clean_grid", "modern_commercial_v2"], kind="post", property_id=70
            )
        with self.assertRaises(Exception):
            marketing_service._resolve_variant_templates(["automatic"], kind="post", property_id=70)
        self.assertEqual(
            marketing_service._resolve_variant_templates(VARIANTS, kind="post", property_id=70), VARIANTS
        )

    def test_variant_selection_rules(self):
        pick = chat_service._variant_templates_for
        self.assertEqual(pick({"action": "generate_post_image"}), VARIANTS)
        chosen = {"generated_data": {"selected_template": "property_premium_hero"}}
        self.assertEqual(pick({"action": "edit_existing_generation"}, None, chosen), ["property_premium_hero"])
        self.assertEqual(pick({"action": "generate_visual", "show_all_variants": True}, chosen), VARIANTS)
        self.assertEqual(pick({"action": "generate_post_image"}, None, chosen), VARIANTS)
        self.assertTrue(chat_service.SHOW_ALL_VARIANTS_RE.search(chat_service._fold("Mostrame las tres de nuevo")))

    def test_legacy_route_is_blocked(self):
        with self.assertLogs("modules.property_marketing", level="ERROR") as logs:
            with self.assertRaises(PropertyMarketingRouteBlocked) as raised:
                assert_property_route(
                    "modern_commercial_v2",
                    renderer="pillow_commercial_v2",
                    requested="automatic",
                    kind="story",
                    property_id=70,
                    callsite="test",
                )
        self.assertEqual(raised.exception.message_key, LEGACY_ROUTE_BLOCKED)
        line = "\n".join(logs.output)
        for field in ("requested=automatic", "resolved=modern_commercial_v2", "kind=story", "property_id=70", "callsite=test"):
            self.assertIn(field, line)
        with self.assertRaises(PropertyMarketingRouteBlocked):
            assert_property_route("property_clean_grid", renderer="pillow_commercial_v2", callsite="test")

    def test_render_local_visual_never_uses_pillow_overlay_for_listing(self):
        def _boom(*_args, **_kwargs):
            raise AssertionError("stamp_branding_overlay must not run for listing visuals")

        fake = {
            "template_used": "property_clean_grid",
            "renderer_used": "html_playwright",
            "png_bytes": b"",
        }
        context = {"facts": {"property_id": 70}}
        for fmt in ("post", "story", "flyer", "status"):
            with patch("modules.marketing_service.stamp_branding_overlay", _boom), patch(
                "modules.marketing_service.render_property_marketing", return_value=fake
            ) as rendered:
                result = _render_local_visual(context, fmt, options={"layout_template": "modern_commercial_v2"}, art={}, language="es")
            self.assertEqual(result["renderer_used"], "html_playwright")
            rendered.assert_called_once()

    def test_render_local_visual_blocks_legacy_result(self):
        legacy = {"template_used": "modern_commercial_v2", "renderer_used": "pillow_commercial_v2", "png_bytes": b""}
        with patch("modules.marketing_service.render_property_marketing", return_value=legacy):
            with self.assertRaises(PropertyMarketingRouteBlocked):
                _render_local_visual({"facts": {"property_id": 70}}, "post", options={}, art={}, language="es")


if __name__ == "__main__":
    unittest.main()
