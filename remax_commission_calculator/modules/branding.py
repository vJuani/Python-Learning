"""
Central product branding configuration.

Visible identity (name, logos, domain) is read from environment so
staging/production can override without code changes.
"""

from __future__ import annotations

import os
from pathlib import Path

from modules.config import BASE_DIR


DEFAULT_BRAND_NAME = "JRH One"
DEFAULT_APP_DOMAIN = "jrhone.com"
DEFAULT_APP_BASE_URL = "http://127.0.0.1:5000"

# JRH One identity palette — Irish green light / night-violet dark
BRAND_COLORS = {
    "brand": "#156A4A",
    "brand_hover": "#0E4F38",
    "accent": "#23805A",
    "navy": "#1E3557",
    "night": "#080A0F",
    "violet": "#7C5CFF",
    "surface_muted": "#F6F8F6",
    "white": "#FFFFFF",
}

# Canonical brand asset folder: static/brand/ (see static/brand/README.md)
BRAND_ASSET_DIR = "brand"
BRAND_MARKS = {
    "primary_light": f"{BRAND_ASSET_DIR}/logo-primary-light.png",
    "horizontal_light": f"{BRAND_ASSET_DIR}/logo-horizontal-light.png",
    "isotype_light": f"{BRAND_ASSET_DIR}/isotype-light.png",
    "app_icon": f"{BRAND_ASSET_DIR}/app-icon.png",
    "primary_dark_green": f"{BRAND_ASSET_DIR}/logo-dark-green.png",
    "primary_dark_violet": f"{BRAND_ASSET_DIR}/logo-dark-violet.png",
    "horizontal_dark_green": f"{BRAND_ASSET_DIR}/logo-horizontal-dark-green.png",
    "horizontal_dark_violet": f"{BRAND_ASSET_DIR}/logo-horizontal-dark-violet.png",
    "isotype_dark_green": f"{BRAND_ASSET_DIR}/isotype-dark-green.png",
    "isotype_dark_violet": f"{BRAND_ASSET_DIR}/isotype-dark-violet.png",
}
BRAND_ICON_REL = BRAND_MARKS["app_icon"]
BRAND_LOGO_LIGHT_REL = BRAND_MARKS["horizontal_light"]
BRAND_LOGO_DARK_REL = BRAND_MARKS["horizontal_dark_green"]
BRAND_EMAIL_FOOTER_REL = f"{BRAND_ASSET_DIR}/email-footer.png"
BRAND_CHROME_ICONS = {
    "favicon": "icons/icon-192.png",
    "apple_touch": "icons/apple-touch-icon.png",
    "pwa_192": "icons/icon-192.png",
    "pwa_512": "icons/icon-512.png",
    "pwa_maskable": "icons/icon-maskable-512.png",
}

# Legacy filenames — deprecated; do not use for product UI (old Commission Calculator art).
LEGACY_LOGO_HORIZONTAL_REL = "images/logo-horizontal.png"
LEGACY_LOGO_FULL_REL = "images/logo-full.png"
LEGACY_LOGO_ICON_REL = "images/logo-icon.png"


def get_brand_name() -> str:
    raw = os.environ.get("APP_BRAND_NAME", DEFAULT_BRAND_NAME).strip()
    return raw or DEFAULT_BRAND_NAME


def get_app_domain() -> str:
    raw = os.environ.get("APP_DOMAIN", DEFAULT_APP_DOMAIN).strip()
    return raw or DEFAULT_APP_DOMAIN


def get_app_base_url() -> str:
    raw = os.environ.get("APP_BASE_URL", DEFAULT_APP_BASE_URL).strip()
    return (raw or DEFAULT_APP_BASE_URL).rstrip("/")


def _logo_rel(env_key: str, default_rel: str) -> str:
    raw = os.environ.get(env_key, "").strip()
    return raw or default_rel


def _resolve_brand_asset(canonical_rel: str) -> str:
    """Return canonical brand/ path when the file exists."""
    if _resolve_static_path(canonical_rel).is_file():
        return canonical_rel
    return canonical_rel


def get_brand_asset_version(rel_path: str) -> int:
    """Cache-buster from file mtime (0 if missing)."""
    path = _resolve_static_path(rel_path)
    if not path.is_file():
        return 0
    return int(path.stat().st_mtime)


def get_brand_mark_rel(name: str) -> str:
    if name not in BRAND_MARKS:
        raise KeyError(name)
    env_key = "APP_BRAND_MARK_" + name.upper()
    return _logo_rel(env_key, BRAND_MARKS[name])


def get_brand_chrome_icon_rel(name: str) -> str:
    if name not in BRAND_CHROME_ICONS:
        raise KeyError(name)
    env_key = "APP_BRAND_CHROME_" + name.upper()
    return _logo_rel(env_key, BRAND_CHROME_ICONS[name])


def iter_brand_mark_rels():
    for name in BRAND_MARKS:
        yield name, get_brand_mark_rel(name)
    for name in BRAND_CHROME_ICONS:
        yield name, get_brand_chrome_icon_rel(name)


def get_web_manifest_payload(icon_url) -> dict:
    """PWA manifest. icon_url(rel) should return a public static URL."""
    return {
        "name": get_brand_name(),
        "short_name": get_brand_name(),
        "description": f"{get_brand_name()} — Gestión. Control. Resultados.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": BRAND_COLORS["surface_muted"],
        "theme_color": BRAND_COLORS["brand_hover"],
        "icons": [
            {
                "src": icon_url(BRAND_CHROME_ICONS["pwa_192"]),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": icon_url(BRAND_CHROME_ICONS["pwa_512"]),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": icon_url(BRAND_CHROME_ICONS["pwa_maskable"]),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }


def get_brand_icon_rel() -> str:
    return _logo_rel(
        "APP_BRAND_ICON",
        _resolve_brand_asset(BRAND_ICON_REL),
    )


def get_brand_logo_light_rel() -> str:
    return _logo_rel(
        "APP_BRAND_LOGO_LIGHT",
        _resolve_brand_asset(BRAND_LOGO_LIGHT_REL),
    )


def get_brand_logo_dark_rel() -> str:
    return _logo_rel(
        "APP_BRAND_LOGO_DARK",
        _resolve_brand_asset(BRAND_LOGO_DARK_REL),
    )


def get_brand_email_footer_rel() -> str:
    return _logo_rel(
        "APP_BRAND_EMAIL_FOOTER",
        BRAND_EMAIL_FOOTER_REL,
    )


def get_logo_horizontal_rel() -> str:
    return _logo_rel(
        "APP_BRAND_LOGO_HORIZONTAL",
        get_brand_logo_light_rel(),
    )


def get_logo_full_rel() -> str:
    return _logo_rel(
        "APP_BRAND_LOGO_FULL",
        get_brand_logo_light_rel(),
    )


def get_logo_icon_rel() -> str:
    return _logo_rel(
        "APP_BRAND_LOGO_ICON",
        get_brand_icon_rel(),
    )


def _resolve_static_path(rel_path: str) -> Path:
    return (BASE_DIR / "static" / rel_path).resolve()


def get_logo_horizontal_path() -> Path:
    return _resolve_static_path(get_logo_horizontal_rel())


def get_logo_full_path() -> Path:
    return _resolve_static_path(get_logo_full_rel())


def get_logo_icon_path() -> Path:
    return _resolve_static_path(get_logo_icon_rel())


def resolve_brand_logo_path(rel_path: str | None = None) -> Path | None:
    """Return an existing brand logo file path, with legacy fallback."""
    candidates = []

    if rel_path:
        candidates.append(_resolve_static_path(rel_path))

    candidates.extend([
        get_logo_horizontal_path(),
        _resolve_static_path(BRAND_MARKS["primary_light"]),
        _resolve_static_path(BRAND_LOGO_LIGHT_REL),
        _resolve_static_path(BRAND_LOGO_DARK_REL),
        _resolve_static_path(BRAND_ICON_REL),
    ])

    for path in candidates:
        if path.is_file():
            return path

    return None


# Backward-compatible alias used by PDF/report modules.
DEFAULT_BRAND_LOGO = get_logo_horizontal_path()


def get_brand_footer_suffix(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return " — Web Dashboard"
    return " — Panel Web"


def get_brand_footer(language: str = "es") -> str:
    return f"{get_brand_name()}{get_brand_footer_suffix(language)}"
