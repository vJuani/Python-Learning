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

DEFAULT_LOGO_HORIZONTAL_REL = "images/jrh-one-logo-horizontal.jpg"
DEFAULT_LOGO_FULL_REL = "images/jrh-one-logo-full.jpg"
DEFAULT_LOGO_ICON_REL = "images/jrh-one-icon.jpg"

# Legacy filenames kept for reference / manual fallback only.
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


def get_logo_horizontal_rel() -> str:
    return _logo_rel(
        "APP_BRAND_LOGO_HORIZONTAL",
        DEFAULT_LOGO_HORIZONTAL_REL,
    )


def get_logo_full_rel() -> str:
    return _logo_rel(
        "APP_BRAND_LOGO_FULL",
        DEFAULT_LOGO_FULL_REL,
    )


def get_logo_icon_rel() -> str:
    return _logo_rel(
        "APP_BRAND_LOGO_ICON",
        DEFAULT_LOGO_ICON_REL,
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
        _resolve_static_path(LEGACY_LOGO_HORIZONTAL_REL),
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
