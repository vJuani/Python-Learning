"""Marketing IA routing: every new listing visual renders with property_* + html_playwright."""

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

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.marketing_conversations_repository import (
    get_marketing_conversation,
    last_generation_id_for_conversation,
    update_marketing_conversation,
)
from modules.database.marketing_generations_repository import (
    get_marketing_generation,
    update_marketing_generation,
)
from modules.database.marketing_repository import (
    get_marketing_asset,
    list_generation_assets,
    update_marketing_asset,
)
from modules.database.property_media_repository import STRATEGY_COPY, upsert_property_media
from modules.marketing_service import _render_local_visual
from modules.property_marketing import (
    LEGACY_ROUTE_BLOCKED,
    PropertyMarketingRouteBlocked,
    assert_property_route,
    normalize_property_design,
    resolve_property_marketing_template,
)
from web_app import app

PROPERTY_TEMPLATES = {"property_clean_grid", "property_lifestyle_dark", "property_premium_hero"}
LEGACY_MARKERS = ("modern_commercial_v2", "pillow_commercial_v2", "modern_commercial_v3", "stamp_branding_overlay")
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


class RenderRoutingTests(unittest.TestCase):
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
        colors = ((88, 96, 104), (140, 120, 96), (60, 90, 70), (120, 130, 150))
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

    def _chat(self, prompt, *, layout_template="automatic", conversation_id=None):
        client = self._client()
        data = {"prompt": prompt, "layout_template": layout_template}
        url = f"/marketing/c/{conversation_id}" if conversation_id else "/marketing/chat"
        response = client.post(url, data=data, follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        conversation_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
        generation = get_marketing_generation(
            last_generation_id_for_conversation(conversation_id, self.org), self.org
        )
        return client, conversation_id, generation

    def _assets(self, generation):
        data = generation.get("generated_data") or {}
        return [get_marketing_asset(asset_id, self.org) for asset_id in data.get("visual_asset_ids") or []]

    def _assert_property_result(self, generation, expected=None):
        data = generation.get("generated_data") or {}
        self.assertEqual(data.get("visual_status"), "completed", data)
        if expected:
            self.assertEqual(data.get("template_used"), expected)
        self.assertIn(data.get("template_used"), PROPERTY_TEMPLATES)
        self.assertEqual(data.get("renderer_used"), "html_playwright")
        self.assertEqual(data.get("layout_version"), data.get("template_used"))
        assets = self._assets(generation)
        self.assertTrue(assets)
        for asset in assets:
            options = asset.get("options") or {}
            self.assertIn(options.get("template_used"), PROPERTY_TEMPLATES)
            if expected:
                self.assertEqual(options.get("template_used"), expected)
            self.assertEqual(options.get("renderer_used"), "html_playwright")
            self.assertEqual(options.get("layout_version"), options.get("template_used"))
            self.assertEqual(asset.get("template"), options.get("design_requested"))
            blob = json.dumps({"options": options, "template": asset.get("template")}, ensure_ascii=False)
            for marker in LEGACY_MARKERS:
                self.assertNotIn(marker, blob)
        blob = json.dumps(data, ensure_ascii=False)
        for marker in LEGACY_MARKERS:
            self.assertNotIn(marker, blob)
        return data, assets

    def test_a_automatic_post_uses_property_templates(self):
        _client, _conversation_id, generation = self._chat("Haceme un post de Santamarina 1335")
        data, assets = self._assert_property_result(generation)
        self.assertEqual({asset["format"] for asset in assets}, {"post"})
        self.assertEqual((assets[0].get("options") or {}).get("design_requested"), "automatic")

    def test_b_c_d_manual_selection_survives_pipeline(self):
        for template in sorted(PROPERTY_TEMPLATES):
            with self.subTest(template=template):
                _client, conversation_id, generation = self._chat(
                    "Haceme un post de Santamarina 1335", layout_template=template
                )
                self._assert_property_result(generation, expected=template)
                conversation = get_marketing_conversation(conversation_id, self.org)
                self.assertEqual((conversation.get("context") or {}).get("layout_template"), template)

    def test_story_and_hacelo_imagen_never_fall_back_to_legacy(self):
        _client, conversation_id, generation = self._chat("Haceme una historia de Santamarina 1335")
        _data, assets = self._assert_property_result(generation)
        self.assertIn("story", {asset["format"] for asset in assets})
        _client, _conversation_id, again = self._chat(
            "hacelo imagen", layout_template="property_premium_hero", conversation_id=conversation_id
        )
        self._assert_property_result(again, expected="property_premium_hero")

    def test_e_regenerate_legacy_generation_migrates_to_property(self):
        client, conversation_id, generation = self._chat("Haceme un post de Santamarina 1335")
        old_assets = self._assets(generation)
        legacy_asset = old_assets[0]
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
        legacy_data = dict(generation.get("generated_data") or {})
        legacy_data.update(
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

        response = client.post(
            f"/marketing/c/{conversation_id}/regenerate",
            data={"generation_id": generation["id"], "visual_only": "1"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        regenerated = get_marketing_generation(
            last_generation_id_for_conversation(conversation_id, self.org), self.org
        )
        self._assert_property_result(regenerated)
        new_ids = set((regenerated.get("generated_data") or {}).get("visual_asset_ids") or [])
        self.assertNotIn(legacy_asset["id"], new_ids)

        response = client.post(f"/marketing/assets/{legacy_asset['id']}/regenerate", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        from modules.database.marketing_repository import list_marketing_assets

        newest = max(list_marketing_assets(self.org, limit=200), key=lambda item: item["id"])
        self.assertGreater(newest["id"], legacy_asset["id"])
        options = newest.get("options") or {}
        self.assertEqual(options.get("design_requested"), "automatic")
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
