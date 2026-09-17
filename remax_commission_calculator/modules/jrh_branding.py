"""Resolve official JRH AI mascot files under static/branding/jrh-ai/.

Canonical assets (full body, transparent PNG):

    jrh_ia_bot_light.png
    jrh_ia_bot_dark.png

These win for every variant (hero, avatar, floating). Older cropped
rasters and SVG placeholders are not used while the official pair exists.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from modules.branding import get_brand_asset_version
from modules.config import BASE_DIR


JRH_AI_REL_DIR = "branding/jrh-ai"
JRH_MASCOT_VARIANTS = ("hero", "avatar", "floating")
JRH_BOT_LIGHT = "jrh_ia_bot_light.png"
JRH_BOT_DARK = "jrh_ia_bot_dark.png"
JRH_BOT_FILES = {
    "light": JRH_BOT_LIGHT,
    "dark": JRH_BOT_DARK,
}
_LOG = logging.getLogger("modules.jrh_branding")


def jrh_ai_dir() -> Path:
    return (BASE_DIR / "static" / JRH_AI_REL_DIR).resolve()


def _rel(name: str) -> str:
    return f"{JRH_AI_REL_DIR}/{name}"


def _lookup_name(filename: str) -> str | None:
    folder = jrh_ai_dir()
    exact = folder / filename
    if exact.is_file():
        return filename
    if not folder.is_dir():
        return None
    wanted = filename.lower()
    for child in folder.iterdir():
        if child.is_file() and child.name.lower() == wanted:
            return child.name
    return None


def resolve_jrh_mascot_rel(variant: str = "hero", theme: str | None = None) -> str | None:
    """Always serve the official full-body bot. Variant is a layout slot only."""
    _ = variant
    wanted = theme if theme in ("light", "dark") else "light"
    found = _lookup_name(JRH_BOT_FILES[wanted])
    if found:
        return _rel(found)
    other = "dark" if wanted == "light" else "light"
    found = _lookup_name(JRH_BOT_FILES[other])
    if found:
        return _rel(found)
    return None


def _url_for_rel(url_for, rel: str | None) -> str:
    if not rel:
        return ""
    return url_for(
        "static",
        filename=rel,
        v=get_brand_asset_version(rel),
    )


def jrh_mascot_context(url_for):
    """Build template URLs with the project's normal static versioning."""
    urls = {}
    resolved = {}
    for variant in JRH_MASCOT_VARIANTS:
        light_rel = resolve_jrh_mascot_rel(variant, "light")
        dark_rel = resolve_jrh_mascot_rel(variant, "dark") or light_rel
        urls[variant] = _url_for_rel(url_for, light_rel)
        urls[f"{variant}_light"] = _url_for_rel(url_for, light_rel)
        urls[f"{variant}_dark"] = _url_for_rel(url_for, dark_rel)
        resolved[variant] = {"light": light_rel, "dark": dark_rel}
    urls["available"] = any(urls[variant] for variant in JRH_MASCOT_VARIANTS)
    urls["resolved"] = resolved
    if os.environ.get("FLASK_DEBUG") == "1" or os.environ.get("JRH_MASCOT_DEBUG") == "1":
        _LOG.info(
            "jrh_mascot resolved hero=%s avatar=%s floating=%s",
            resolved["hero"],
            resolved["avatar"],
            resolved["floating"],
        )
    else:
        try:
            from flask import current_app, has_app_context

            if has_app_context() and current_app.debug:
                _LOG.info(
                    "jrh_mascot resolved hero=%s avatar=%s floating=%s",
                    resolved["hero"],
                    resolved["avatar"],
                    resolved["floating"],
                )
        except Exception:
            pass
    return urls
