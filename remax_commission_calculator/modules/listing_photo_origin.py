"""Classify listing photos and block generated marketing assets from re-entering renders."""

from __future__ import annotations

GENERATED_SOURCES = frozenset(
    {
        "marketing",
        "marketing_ia",
        "campaign",
        "flyer_render",
        "html_playwright",
        "generated",
    }
)

GENERATED_PATH_MARKERS = (
    "marketing_html_render",
    "marketing_style_references",
    "tmp_acm_v5_qa",
    "/marketing_assets/",
    "\\marketing_assets\\",
    "modern_commercial_v2_reference",
    "modern_commercial_v3",
    "italia_1341_v3",
    "italia_v3_vs_reference",
    "generated_marketing",
)

THUMB_URL_MARKERS = (
    "/thumb",
    "_thumb",
    "thumbnail",
    "/small/",
    "/preview",
    "/w_200",
    "/w_400",
)

SOURCE_PRIORITY = {
    "original_property_photo": 400,
    "external_listing_original": 300,
    "local_cached_original": 200,
    "thumbnail": 100,
    "unknown": 10,
    "generated_marketing_asset": 0,
    "missing": 0,
}

EXTERNAL_SOURCES = frozenset({"redremax", "external_public_page"})
MANUAL_SOURCES = frozenset({"manual", "upload", "property", "local"})
ORIGINAL_SOURCE_TYPES = frozenset(
    {
        "original_property_photo",
        "external_listing_original",
        "local_cached_original",
    }
)


def _blob(photo):
    photo = photo or {}
    parts = [
        photo.get("storage_key"),
        photo.get("original_url"),
        photo.get("remote_url"),
        photo.get("path"),
        photo.get("role"),
    ]
    return " ".join(str(part or "") for part in parts).casefold().replace("\\", "/")


def is_generated_marketing_asset(photo):
    """True when this row is a flyer/campaign/Marketing IA output, not a listing photo."""
    if not photo:
        return False
    source = str(photo.get("source") or "").strip().lower()
    if source in GENERATED_SOURCES:
        return True
    if photo.get("generated") or photo.get("is_marketing_asset"):
        return True
    blob = _blob(photo)
    return any(marker in blob for marker in GENERATED_PATH_MARKERS)


def _is_thumbnail(photo):
    photo = photo or {}
    kind = str(photo.get("url_kind") or "").strip().lower()
    if kind in {"thumbnail", "thumb", "preview"}:
        return True
    blob = _blob(photo)
    if any(marker in blob for marker in THUMB_URL_MARKERS):
        return True
    try:
        width = int(photo.get("width") or 0)
        height = int(photo.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    if width and height and min(width, height) < 400:
        return True
    return False


def photo_source_type(photo):
    photo = photo or {}
    if is_generated_marketing_asset(photo):
        return "generated_marketing_asset"
    if _is_thumbnail(photo):
        return "thumbnail"
    source = str(photo.get("source") or "").strip().lower()
    strategy = str(photo.get("storage_strategy") or "").strip().lower()
    has_key = bool(str(photo.get("storage_key") or photo.get("path") or "").strip())
    has_url = bool(str(photo.get("original_url") or photo.get("remote_url") or "").strip())
    if source in MANUAL_SOURCES and has_key:
        return "original_property_photo"
    if source in EXTERNAL_SOURCES and strategy == "managed_copy" and has_key:
        return "local_cached_original"
    if source in EXTERNAL_SOURCES and has_url:
        return "external_listing_original"
    if has_key and strategy != "remote_reference":
        return "original_property_photo"
    if has_url:
        return "external_listing_original"
    if has_key:
        return "local_cached_original"
    return "unknown"


def source_priority(photo):
    return SOURCE_PRIORITY.get(photo_source_type(photo), 0)


def is_original_listing_photo(photo):
    return photo_source_type(photo) in ORIGINAL_SOURCE_TYPES


def eligible_listing_photos(photos):
    """Drop generated marketing assets. Keep originals, cached copies, then thumbnails."""
    eligible = []
    for item in photos or []:
        if not item or is_generated_marketing_asset(item):
            continue
        eligible.append(item)
    eligible.sort(
        key=lambda item: (
            -source_priority(item),
            0 if item.get("is_cover") else 1,
            int(item.get("position") or 0),
            int(item.get("id") or 0),
        )
    )
    return eligible


def photo_origin_log(row, image=None):
    if row is None and image is None:
        return {
            "source_type": "missing",
            "asset_id": None,
            "original_url": None,
            "path": None,
            "width": None,
            "height": None,
        }
    width = height = None
    if image is not None:
        width, height = image.size
    row = row or {}
    return {
        "source_type": photo_source_type(row),
        "asset_id": row.get("id"),
        "original_url": row.get("original_url") or row.get("remote_url"),
        "path": row.get("storage_key") or row.get("path"),
        "width": width or row.get("width"),
        "height": height or row.get("height"),
    }
