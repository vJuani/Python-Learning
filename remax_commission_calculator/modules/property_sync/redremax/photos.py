"""RedREMAX photo URL rules. Remote reference only. No signed URL persistence."""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse, urlunparse

from modules.property_sync.redremax.mapping import PHOTO_HOST_ALLOWLIST

BETA_PHOTO_LIMIT = 5


SIGNED_MARKERS = (
    "x-amz-algorithm",
    "x-amz-credential",
    "x-amz-signature",
    "x-amz-expires",
    "x-amz-date",
    "x-amz-security-token",
)


def is_signed_url(url):
    raw = str(url or "").lower()
    return any(marker in raw for marker in SIGNED_MARKERS)


def canonical_photo_url(url):
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def is_allowed_redremax_photo_url(url):
    raw = str(url or "").strip()
    if not raw or is_signed_url(raw):
        return False
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in PHOTO_HOST_ALLOWLIST


def photo_external_id(url):
    canonical = canonical_photo_url(url)
    if not canonical:
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def map_photos(photos, *, limit=BETA_PHOTO_LIMIT):
    items = []
    if not isinstance(photos, list):
        return items
    for index, raw in enumerate(photos):
        raw = raw or {}
        cdn = str(raw.get("cdn") or "").strip()
        if not is_allowed_redremax_photo_url(cdn):
            continue
        items.append(
            {
                "external_media_id": photo_external_id(cdn),
                "original_url": canonical_photo_url(cdn),
                "url_kind": "stable",
                "storage_strategy": "remote_reference",
                "position": index,
                "is_cover": bool(raw.get("primary")),
                "content_type": "image/jpeg",
            }
        )
    return select_beta_photos(items, limit=limit)


def select_beta_photos(items, *, limit=BETA_PHOTO_LIMIT):
    """Always keep primary, then fill in array order up to the beta cap."""
    if limit is None or int(limit) <= 0:
        return list(items or [])
    cap = int(limit)
    chosen = []
    seen = set()
    primary = next((item for item in (items or []) if item.get("is_cover")), None)
    if primary:
        chosen.append(primary)
        seen.add(primary.get("external_media_id") or primary.get("original_url"))
    for item in items or []:
        if len(chosen) >= cap:
            break
        identity = item.get("external_media_id") or item.get("original_url")
        if identity in seen:
            continue
        seen.add(identity)
        chosen.append(item)
    return chosen


def has_blueprints(payload):
    blueprints = (payload or {}).get("blueprints")
    return isinstance(blueprints, list) and len(blueprints) > 0
