"""Resolve official JRH AI mascot files under static/branding/jrh-ai/.

Prefer a raster master (webp/png). Variant-specific files win when present.
A single master file is reused for hero, avatar and floating.
Missing assets return None so templates can hide the image instead of
rendering a broken icon.
"""

from __future__ import annotations

from pathlib import Path

from modules.branding import get_brand_asset_version
from modules.config import BASE_DIR


JRH_AI_REL_DIR = "branding/jrh-ai"
JRH_MASCOT_VARIANTS = ("hero", "avatar", "floating")
_RASTER_EXTS = (".webp", ".png")
_VECTOR_EXTS = (".svg",)


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


def _raster_names(variant: str):
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


def resolve_jrh_mascot_rel(variant: str = "hero") -> str | None:
    chosen = variant if variant in JRH_MASCOT_VARIANTS else "hero"
    return _first_existing(_raster_names(chosen)) or _first_existing(
        _vector_names(chosen)
    )


def jrh_mascot_context(url_for):
    """Build template URLs with the project's normal static versioning."""
    urls = {}
    for variant in JRH_MASCOT_VARIANTS:
        rel = resolve_jrh_mascot_rel(variant)
        if not rel:
            urls[variant] = ""
            continue
        urls[variant] = url_for(
            "static",
            filename=rel,
            v=get_brand_asset_version(rel),
        )
    urls["available"] = any(urls[variant] for variant in JRH_MASCOT_VARIANTS)
    return urls
