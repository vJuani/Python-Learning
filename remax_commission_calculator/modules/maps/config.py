"""Environment-driven Maps configuration. Never log API keys."""

from __future__ import annotations

import os


GEOCODE_RESOLVED = "resolved"
GEOCODE_MANUAL = "manual"
GEOCODE_STALE = "stale"
GEOCODE_UNRESOLVED = "unresolved"

PROVIDER_NONE = "none"
PROVIDER_GOOGLE = "google"
PROVIDER_MOCK = "mock"


def get_maps_provider_name() -> str:
    raw = (os.environ.get("MAPS_PROVIDER") or "").strip().lower()
    if raw in {PROVIDER_GOOGLE, PROVIDER_MOCK, PROVIDER_NONE}:
        return raw
    if maps_browser_key() or maps_server_key():
        return PROVIDER_GOOGLE
    return PROVIDER_NONE


def maps_browser_key() -> str:
    return (os.environ.get("GOOGLE_MAPS_BROWSER_KEY") or "").strip()


def maps_server_key() -> str:
    return (os.environ.get("GOOGLE_MAPS_SERVER_KEY") or "").strip()


def maps_region() -> str:
    raw = (os.environ.get("MAPS_DEFAULT_REGION") or "AR").strip().upper()
    return raw or "AR"


def maps_is_configured() -> bool:
    name = get_maps_provider_name()
    if name == PROVIDER_MOCK:
        return True
    if name == PROVIDER_GOOGLE:
        return bool(maps_browser_key())
    return False


def maps_public_config() -> dict:
    """Safe values for templates. Includes the browser key only when set."""
    configured = maps_is_configured()
    provider = get_maps_provider_name()
    key = maps_browser_key() if configured and provider == PROVIDER_GOOGLE else ""
    return {
        "provider": provider,
        "configured": configured,
        "autocomplete_enabled": bool(key),
        "embed_enabled": bool(key),
        "browser_key": key,
        "region": maps_region().lower(),
    }
