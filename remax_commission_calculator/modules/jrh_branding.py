"""Resolve official JRH AI mascot files under static/branding/jrh-ai/.

Official themed rasters win:

    jrh-ia-{hero|avatar|launcher}-{light|dark}.png

Legacy jrh-bot-*.png files are next. SVG placeholders are last-resort
fallback only so the UI never shows a broken image icon.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from modules.branding import get_brand_asset_version
from modules.config import BASE_DIR


JRH_AI_REL_DIR = "branding/jrh-ai"
JRH_MASCOT_VARIANTS = ("hero", "avatar", "floating")
JRH_MASCOT_SLOTS = {
    "hero": "hero",
    "avatar": "avatar",
    "floating": "launcher",
    "launcher": "launcher",
}
_RASTER_EXTS = (".webp", ".png")
_VECTOR_EXTS = (".svg",)
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


def _first_existing(names) -> str | None:
    seen = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        found = _lookup_name(name)
        if found:
            return _rel(found)
    return None


def _slot(variant: str) -> str:
    return JRH_MASCOT_SLOTS.get(variant, "hero")


def _official_names(variant: str, theme: str | None):
    slot = _slot(variant)
    names = []
    themes = (theme,) if theme in ("light", "dark") else ("light", "dark")
    for current in themes:
        for ext in _RASTER_EXTS:
            names.append(f"jrh-ia-{slot}-{current}{ext}")
    for ext in _RASTER_EXTS:
        names.append(f"jrh-ia-{slot}{ext}")
    if theme == "dark":
        for ext in _RASTER_EXTS:
            names.append(f"jrh-ia-{slot}-light{ext}")
    return names


def _legacy_raster_names(variant: str):
    names = [
        f"jrh-bot-{variant}.webp",
        f"jrh-bot-{variant}.png",
        "jrh-bot.webp",
        "jrh-bot.png",
        "jrh-mascot.webp",
        "jrh-mascot.png",
        "jrh-bot-hero.webp",
        "jrh-bot-hero.png",
    ]
    for other in JRH_MASCOT_VARIANTS:
        if other == variant:
            continue
        names.extend(
            (
                f"jrh-bot-{other}.webp",
                f"jrh-bot-{other}.png",
            )
        )
    return names


def _vector_names(variant: str):
    return (
        f"jrh-bot-{variant}.svg",
        "jrh-bot.svg",
        "jrh-bot-hero.svg",
        "jrh-bot-avatar.svg",
        "jrh-bot-floating.svg",
    )


def resolve_jrh_mascot_rel(variant: str = "hero", theme: str | None = None) -> str | None:
    chosen = variant if variant in JRH_MASCOT_SLOTS else "hero"
    return (
        _first_existing(_official_names(chosen, theme))
        or _first_existing(_legacy_raster_names(chosen))
        or _first_existing(_vector_names(chosen))
    )


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
