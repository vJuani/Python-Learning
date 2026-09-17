"""JRH official mascot resolution and display-title mapping."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.agenda_nlp import (
    build_task_display_title,
    build_task_title,
    parse_property_query,
)
from modules.jrh_branding import (
    JRH_AI_REL_DIR,
    JRH_BOT_DARK,
    JRH_BOT_LIGHT,
    jrh_ai_dir,
    resolve_jrh_mascot_rel,
)
from modules.jrh_home import build_jrh_chip_actions


class JrhBrandingTests(unittest.TestCase):
    def test_official_bot_files_exist(self):
        folder = jrh_ai_dir()
        self.assertTrue((folder / JRH_BOT_LIGHT).is_file())
        self.assertTrue((folder / JRH_BOT_DARK).is_file())

    def test_resolver_uses_official_bot_pair(self):
        self.assertEqual(
            resolve_jrh_mascot_rel("hero", "light"),
            f"{JRH_AI_REL_DIR}/{JRH_BOT_LIGHT}",
        )
        self.assertEqual(
            resolve_jrh_mascot_rel("hero", "dark"),
            f"{JRH_AI_REL_DIR}/{JRH_BOT_DARK}",
        )
        self.assertEqual(
            resolve_jrh_mascot_rel("avatar", "light"),
            f"{JRH_AI_REL_DIR}/{JRH_BOT_LIGHT}",
        )
        self.assertEqual(
            resolve_jrh_mascot_rel("floating", "dark"),
            f"{JRH_AI_REL_DIR}/{JRH_BOT_DARK}",
        )

    def test_official_bot_wins_over_legacy_and_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "jrh-bot-hero.svg").write_text("<svg></svg>", encoding="utf-8")
            (folder / "jrh-bot-hero.png").write_bytes(b"legacy")
            (folder / "jrh-ia-hero-light.png").write_bytes(b"old-crop")
            (folder / JRH_BOT_LIGHT).write_bytes(b"official-light")
            (folder / JRH_BOT_DARK).write_bytes(b"official-dark")
            with patch("modules.jrh_branding.jrh_ai_dir", return_value=folder):
                self.assertEqual(
                    resolve_jrh_mascot_rel("hero", "light"),
                    f"{JRH_AI_REL_DIR}/{JRH_BOT_LIGHT}",
                )
                self.assertEqual(
                    resolve_jrh_mascot_rel("hero", "dark"),
                    f"{JRH_AI_REL_DIR}/{JRH_BOT_DARK}",
                )
                self.assertEqual(
                    resolve_jrh_mascot_rel("floating", "light"),
                    f"{JRH_AI_REL_DIR}/{JRH_BOT_LIGHT}",
                )
                self.assertNotIn(".svg", resolve_jrh_mascot_rel("hero"))
                self.assertNotIn("jrh-ia-hero", resolve_jrh_mascot_rel("hero", "light"))

    def test_official_bot_is_reused_for_every_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / JRH_BOT_LIGHT).write_bytes(b"png")
            with patch("modules.jrh_branding.jrh_ai_dir", return_value=folder):
                self.assertEqual(
                    resolve_jrh_mascot_rel("hero"),
                    f"{JRH_AI_REL_DIR}/{JRH_BOT_LIGHT}",
                )
                self.assertEqual(
                    resolve_jrh_mascot_rel("avatar"),
                    f"{JRH_AI_REL_DIR}/{JRH_BOT_LIGHT}",
                )
                self.assertEqual(
                    resolve_jrh_mascot_rel("floating"),
                    f"{JRH_AI_REL_DIR}/{JRH_BOT_LIGHT}",
                )

    def test_missing_asset_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("modules.jrh_branding.jrh_ai_dir", return_value=Path(tmp)):
                self.assertIsNone(resolve_jrh_mascot_rel("hero"))


class JrhDisplayTitleTests(unittest.TestCase):
    def test_junk_note_si_is_not_a_place(self):
        self.assertEqual(parse_property_query("visita con Ro si"), "")
        self.assertEqual(
            build_task_title("visit", "Ro", "si"),
            "Visita con Ro",
        )
        self.assertEqual(
            build_task_display_title(
                task_type="visit",
                title="Visita con Ro · si",
                contact_name="Ro",
                property_address="si",
                type_label="Visita",
            ),
            "Visita con Ro",
        )

    def test_real_place_and_contact_keep_mapped_fields(self):
        self.assertEqual(
            build_task_display_title(
                task_type="visit",
                title="Visita con Ro · España",
                contact_name="Ro",
                property_address="España",
                type_label="Visita",
            ),
            "Visita · España",
        )
        self.assertEqual(
            build_task_display_title(
                task_type="call",
                title="Llamada con Ro · si",
                contact_name="Ro",
                property_address="",
                type_label="Llamada",
            ),
            "Llamada con Ro",
        )


class JrhChipActionsTests(unittest.TestCase):
    def test_agent_chips_include_acm_and_performance(self):
        keys = [
            item["key"]
            for item in build_jrh_chip_actions(
                can_acm=True,
                can_productivity=True,
                can_agent_workspace=True,
            )
        ]
        self.assertEqual(
            keys,
            ["search", "agenda", "contacts", "billing", "acm", "performance"],
        )

    def test_staff_chips_omit_agent_only(self):
        keys = [item["key"] for item in build_jrh_chip_actions()]
        self.assertEqual(keys, ["search", "billing"])
        self.assertNotIn("agenda", keys)
        self.assertNotIn("contacts", keys)
        self.assertNotIn("acm", keys)


if __name__ == "__main__":
    unittest.main()
