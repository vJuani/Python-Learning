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
    resolve_jrh_mascot_rel,
)
from modules.jrh_home import build_jrh_chip_actions


class JrhBrandingTests(unittest.TestCase):
    def test_resolver_finds_existing_placeholder(self):
        rel = resolve_jrh_mascot_rel("hero")
        self.assertIsNotNone(rel)
        self.assertTrue(rel.startswith(f"{JRH_AI_REL_DIR}/"))
        self.assertTrue(rel.endswith(".png") or rel.endswith(".svg"))
        self.assertNotIn(".webp", rel)

    def test_single_master_raster_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "jrh-bot-hero.png").write_bytes(b"png")
            with patch("modules.jrh_branding.jrh_ai_dir", return_value=folder):
                self.assertEqual(
                    resolve_jrh_mascot_rel("hero"),
                    f"{JRH_AI_REL_DIR}/jrh-bot-hero.png",
                )
                self.assertEqual(
                    resolve_jrh_mascot_rel("avatar"),
                    f"{JRH_AI_REL_DIR}/jrh-bot-hero.png",
                )
                self.assertEqual(
                    resolve_jrh_mascot_rel("floating"),
                    f"{JRH_AI_REL_DIR}/jrh-bot-hero.png",
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
            for item in build_jrh_chip_actions(can_acm=True, can_productivity=True)
        ]
        self.assertEqual(
            keys,
            ["search", "agenda", "contacts", "billing", "acm", "performance"],
        )

    def test_staff_chips_omit_agent_only(self):
        keys = [item["key"] for item in build_jrh_chip_actions()]
        self.assertEqual(keys, ["search", "agenda", "contacts", "billing"])
        self.assertNotIn("acm", keys)


if __name__ == "__main__":
    unittest.main()
