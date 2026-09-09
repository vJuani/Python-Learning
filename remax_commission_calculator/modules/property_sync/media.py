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
    mark_media_removed_from_source,
    upsert_property_media,
)
from modules.property_sync.security import (
    MAX_MEDIA_BYTES,
    assert_safe_media_url,
    is_allowed_image_type,
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
            find_media_by_external_id(organization_id, source, external_media_id)
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


def get_property_cover_media(organization_id, property_id):
    items = list_property_media(organization_id, property_id)
    for item in items:
        if item.get("is_cover"):
            return item
    return items[0] if items else None


def get_property_media_for_generation(property_row):
    """Authorized, available gallery only. Does not send anything to OpenAI."""
    if not property_row:
        return []
    return list_property_media(
        property_row["organization_id"],
        property_row["id"],
    )


def resolve_media_filesystem_path(item):
    key = (item or {}).get("storage_key")
    if not key:
        return None
    path = get_private_upload_root() / Path(key)
    if path.is_file():
        return path
    return None
