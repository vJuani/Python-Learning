"""Sync and read property gallery media. No OpenAI upload. No fragile hotlink fetch."""

from __future__ import annotations

import hashlib
from pathlib import Path

from modules.config import get_private_upload_root
from modules.database.property_media_repository import (
    STATUS_ACTIVE,
    STRATEGY_COPY,
    STRATEGY_REMOTE,
    find_media_by_external_id,
    list_property_media,
    list_property_media_for_properties,
    mark_media_removed_from_source,
    upsert_property_media,
)
from modules.property_sync.security import (
    MAX_MEDIA_BYTES,
    assert_safe_media_url,
    is_allowed_image_type,
    is_safe_media_url,
)


def media_storage_dir(organization_id, property_id):
    return (
        get_private_upload_root()
        / "organizations"
        / str(organization_id)
        / "properties"
        / str(property_id)
        / "media"
    )


def _write_bytes(organization_id, property_id, filename, payload):
    if len(payload) > MAX_MEDIA_BYTES:
        raise ValueError("media_too_large")
    directory = media_storage_dir(organization_id, property_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(payload)
    return str(path.relative_to(get_private_upload_root())).replace("\\", "/")


def sync_property_media(organization_id, property_id, source, media_items, *, capabilities=None):
    capabilities = capabilities or {}
    seen_ids = set()
    created = 0
    unchanged = 0
    incoming = list(media_items or [])
    if not incoming:
        return {"created": 0, "unchanged": 0, "removed": 0}

    default_strategy = capabilities.get("media_strategy") or STRATEGY_REMOTE

    for index, item in enumerate(incoming):
        item = item or {}
        external_media_id = str(item.get("external_media_id") or "").strip() or None
        if external_media_id:
            seen_ids.add(external_media_id)
        existing = (
            find_media_by_external_id(
                organization_id, property_id, source, external_media_id
            )
            if external_media_id
            else None
        )
        url_kind = item.get("url_kind") or capabilities.get("media_url_kind") or "stable"
        original_url = (item.get("original_url") or "").strip() or None
        strategy = item.get("storage_strategy") or default_strategy
        payload = item.get("content_bytes")
        content_hash = None
        storage_key = existing["storage_key"] if existing else None

        if url_kind == "temporary":
            original_url = None
        elif original_url and url_kind != "local_fixture":
            try:
                assert_safe_media_url(original_url, require_https=True)
            except ValueError:
                original_url = None
            if source == "redremax":
                from modules.property_sync.redremax.photos import (
                    is_allowed_redremax_photo_url,
                )

                if original_url and not is_allowed_redremax_photo_url(original_url):
                    original_url = None

        if strategy == STRATEGY_COPY and payload:
            if not is_allowed_image_type(item.get("content_type") or "image/png"):
                raise ValueError("unsupported_media_type")
            content_hash = hashlib.sha256(payload).hexdigest()
            if existing and existing.get("content_hash") == content_hash and existing.get("storage_key"):
                storage_key = existing["storage_key"]
            else:
                filename = f"{external_media_id or content_hash[:12]}.png"
                storage_key = _write_bytes(organization_id, property_id, filename, payload)
        elif strategy == STRATEGY_COPY and url_kind != "local_fixture":
            # Real HTTP download is not activated in V1. Keep reference only.
            strategy = STRATEGY_REMOTE

        saved = upsert_property_media(
            organization_id,
            property_id,
            source=source,
            external_media_id=external_media_id,
            original_url=original_url,
            storage_key=storage_key,
            storage_strategy=strategy,
            url_kind=url_kind,
            position=item.get("position", index),
            is_cover=bool(item.get("is_cover")),
            width=item.get("width"),
            height=item.get("height"),
            content_type=item.get("content_type"),
            content_hash=content_hash,
            external_updated_at=item.get("external_updated_at"),
        )
        if existing and existing["id"] == saved["id"] and existing["status"] == STATUS_ACTIVE:
            unchanged += 1
        else:
            created += 1

    removed = 0
    for current in list_property_media(organization_id, property_id, include_removed=True):
        if current.get("source") != source:
            continue
        ext_id = current.get("external_media_id")
        if ext_id and ext_id not in seen_ids and current["status"] == STATUS_ACTIVE:
            mark_media_removed_from_source(current["id"], organization_id)
            removed += 1

    return {"created": created, "unchanged": unchanged, "removed": removed}


def _property_identity(property_or_org, property_id=None):
    if isinstance(property_or_org, dict):
        return property_or_org.get("organization_id"), property_or_org.get("id")
    return property_or_org, property_id


def _is_external_source(source):
    return str(source or "").strip().lower() in {"redremax", "external_public_page"}


def pick_cover_media(items):
    """Manual cover override, then RedREMAX primary, then first valid media."""
    rows = [item for item in (items or []) if item]
    if not rows:
        return None
    for item in rows:
        if not _is_external_source(item.get("source")) and item.get("is_cover"):
            return item
    for item in rows:
        if str(item.get("source") or "").strip() == "redremax" and item.get("is_cover"):
            return item
    for item in rows:
        if item.get("is_cover"):
            return item
    return rows[0]


def get_property_cover_media(property_or_org, property_id=None):
    organization_id, resolved_id = _property_identity(property_or_org, property_id)
    if organization_id is None or resolved_id is None:
        return None
    return pick_cover_media(list_property_media(organization_id, resolved_id))


def is_displayable_media(item):
    if not item:
        return False
    if item.get("storage_key"):
        return True
    url = (item.get("original_url") or "").strip()
    if not url:
        return False
    if str(item.get("source") or "").strip() == "redremax":
        from modules.property_sync.redremax.photos import is_allowed_redremax_photo_url

        return is_allowed_redremax_photo_url(url)
    return is_safe_media_url(url, require_https=True)


def media_display_src(item, property_id=None):
    """Safe URL for <img>. Remote RedREMAX only if host is allowlisted."""
    if not is_displayable_media(item):
        return None
    url = (item.get("original_url") or "").strip()
    if item.get("storage_strategy") == STRATEGY_REMOTE or (
        url and not item.get("storage_key")
    ):
        return url
    if item.get("storage_key") and property_id and item.get("id"):
        try:
            from flask import has_request_context, url_for

            if has_request_context():
                return url_for(
                    "property_gallery_file",
                    property_id=property_id,
                    media_id=item["id"],
                )
        except Exception:
            return None
    return url or None


def get_property_media_for_generation(property_row, limit=5):
    """Cover first, then valid photos. Does not send anything to OpenAI."""
    if not property_row:
        return []
    organization_id, property_id = _property_identity(property_row)
    if organization_id is None or property_id is None:
        return []
    items = [
        item
        for item in list_property_media(organization_id, property_id)
        if is_displayable_media(item)
    ]
    cover = pick_cover_media(items)
    ordered = []
    seen = set()
    if cover:
        ordered.append(cover)
        seen.add(cover.get("id") or cover.get("external_media_id"))
    for item in items:
        identity = item.get("id") or item.get("external_media_id")
        if identity in seen:
            continue
        ordered.append(item)
        seen.add(identity)
        if limit and len(ordered) >= int(limit):
            break
    if limit:
        return ordered[: int(limit)]
    return ordered


def list_covers_for_properties(organization_id, property_ids):
    grouped = {}
    for item in list_property_media_for_properties(organization_id, property_ids):
        grouped.setdefault(item["property_id"], []).append(item)
    covers = {}
    for property_id in property_ids or []:
        try:
            key = int(property_id)
        except (TypeError, ValueError):
            continue
        covers[key] = pick_cover_media(grouped.get(key) or [])
    return covers


def resolve_media_filesystem_path(item):
    key = (item or {}).get("storage_key")
    if not key:
        return None
    path = get_private_upload_root() / Path(key)
    if path.is_file():
        return path
    return None
