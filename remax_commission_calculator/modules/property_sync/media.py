"""Sync and read property gallery media. No OpenAI upload. No fragile hotlink fetch."""

from __future__ import annotations

import hashlib
import logging
import tempfile
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
from modules.listing_photo_origin import (
    ORIGINAL_SOURCE_TYPES,
    eligible_listing_photos,
    is_generated_marketing_asset,
    is_original_listing_photo,
    photo_source_type,
    prefer_highest_resolution_media,
    source_variant,
)

logger = logging.getLogger(__name__)
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
            if source == "redremax":
                from modules.property_sync.redremax.photos import (
                    canonical_photo_url,
                    is_allowed_redremax_photo_url,
                )

                original_url = canonical_photo_url(original_url) or None
                if original_url and not is_allowed_redremax_photo_url(original_url):
                    original_url = None
            try:
                if original_url:
                    assert_safe_media_url(original_url, require_https=True)
            except ValueError:
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
    rows = [
        item
        for item in (items or [])
        if item and not is_generated_marketing_asset(item)
    ]
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


def csp_allows_redremax_images(header_value):
    """True when CSP is absent or img-src includes a RedREMAX photo host."""
    from urllib.parse import urlparse

    from modules.property_sync.redremax.mapping import REDREMAX_ALLOWED_IMAGE_HOSTS

    raw = str(header_value or "").strip()
    if not raw:
        return True
    parts = [part.strip() for part in raw.split(";") if part.strip()]
    img_src = next(
        (part for part in parts if part.lower().startswith("img-src")),
        "",
    )
    if not img_src:
        return True
    tokens = []
    for token in img_src.split():
        lowered = token.lower()
        if lowered in {"img-src", "'self'", "data:", "blob:", "*"}:
            tokens.append(lowered)
            continue
        parsed = urlparse(token if "://" in token else f"https://{token}")
        host = (parsed.hostname or "").strip().lower()
        if host:
            tokens.append(host)
    if "*" in tokens:
        return True
    return any(host in tokens for host in REDREMAX_ALLOWED_IMAGE_HOSTS)


def get_property_cover_media(property_or_org, property_id=None):
    organization_id, resolved_id = _property_identity(property_or_org, property_id)
    if organization_id is None or resolved_id is None:
        return None
    return pick_cover_media(list_property_media(organization_id, resolved_id))


PLACEHOLDER_MEDIA_HOSTS = frozenset(
    {
        "example.com",
        "www.example.com",
        "example.invalid",
        "www.example.invalid",
        "example.org",
        "www.example.org",
    }
)


def _media_source(item):
    return str((item or {}).get("source") or "").strip().lower()


def _media_hostname(item):
    from urllib.parse import urlparse

    url = ((item or {}).get("original_url") or (item or {}).get("remote_url") or "").strip()
    if not url:
        return ""
    return (urlparse(url).hostname or "").strip().lower()


def is_displayable_media(item):
    """True only when this media may be used as a real <img> src."""
    if not item:
        return False
    source = _media_source(item)
    if source == "mock_network":
        return False
    host = _media_hostname(item)
    if host in PLACEHOLDER_MEDIA_HOSTS or host.endswith(".example.com"):
        return False
    url = (item.get("original_url") or item.get("remote_url") or "").strip()
    if source == "redremax":
        from modules.property_sync.redremax.photos import is_allowed_redremax_photo_url

        return is_allowed_redremax_photo_url(url)
    if item.get("storage_key"):
        return True
    if not url:
        return False
    return is_safe_media_url(url, require_https=True)


def get_property_media_url(media, property_id=None):
    """Single <img src> resolver. Local managed file, else remote URL, else None."""
    if not is_displayable_media(media):
        return None
    url = (media.get("original_url") or media.get("remote_url") or "").strip()
    if _media_source(media) == "redremax":
        from modules.property_sync.redremax.photos import canonical_photo_url

        url = canonical_photo_url(url)
    if media.get("storage_key") and property_id and media.get("id"):
        if media.get("storage_strategy") != STRATEGY_REMOTE:
            try:
                from flask import has_request_context, url_for

                if has_request_context():
                    return url_for(
                        "property_gallery_file",
                        property_id=property_id,
                        media_id=media["id"],
                    )
            except Exception:
                return None
    if media.get("storage_strategy") == STRATEGY_REMOTE or (
        url and not media.get("storage_key")
    ):
        return url or None
    return url or None


def media_display_src(item, property_id=None):
    return get_property_media_url(item, property_id)


def describe_property_media(item, property_id=None):
    """Admin/dev debug. Never includes the full remote URL."""
    url = ((item or {}).get("original_url") or (item or {}).get("remote_url") or "").strip()
    host = _media_hostname(item)
    renderable = is_displayable_media(item) and bool(
        get_property_media_url(item, property_id or (item or {}).get("property_id"))
    )
    return {
        "id": (item or {}).get("id"),
        "source": (item or {}).get("source"),
        "media_type": (item or {}).get("media_type"),
        "is_cover": bool((item or {}).get("is_cover")),
        "position": (item or {}).get("position"),
        "has_remote_url": bool(url),
        "hostname": host,
        "status": (item or {}).get("status"),
        "renderable": renderable,
    }


def _organization_id_for_property(property_id):
    try:
        pid = int(property_id)
    except (TypeError, ValueError):
        return None
    from modules.database.connection import get_connection

    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT organization_id FROM properties WHERE id = ?",
            (pid,),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return None
    return row[0]


def _property_row_for_media(property_id, organization_id=None, property_row=None):
    if isinstance(property_row, dict) and property_row.get("id") is not None:
        return property_row
    if property_id is None:
        return None
    org = organization_id
    if org is None:
        org = _organization_id_for_property(property_id)
    if org is None:
        return None
    from modules.database.properties_repository import get_property_record

    return get_property_record(property_id, org)


def _eligible_property_media_rows(property_row):
    organization_id, property_id = _property_identity(property_row)
    if organization_id is None or property_id is None:
        return []
    items = eligible_listing_photos(
        [
            item
            for item in list_property_media(organization_id, property_id)
            if is_displayable_media(item) and not is_generated_marketing_asset(item)
        ]
    )
    items = prefer_highest_resolution_media(items)
    originals = [item for item in items if is_original_listing_photo(item)]
    return originals or items


def _order_cover_first(items, limit=None):
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


def _media_display_url(item, property_id=None):
    url = get_property_media_url(item, property_id or (item or {}).get("property_id"))
    if url:
        return url
    return ((item or {}).get("original_url") or (item or {}).get("remote_url") or "").strip() or None


def get_property_original_media(
    property_id,
    organization_id=None,
    *,
    limit=None,
    property_row=None,
):
    """Same listing photos the property sheet and Marketing IA already show.

    Returns originals (manual upload, RedREMAX remote URL, or cached copy).
    Thumbnails only if no original remains. Never generated marketing assets.
    """
    row = _property_row_for_media(property_id, organization_id, property_row)
    if not row:
        return []
    resolved_id = row.get("id") if property_id is None else property_id
    ordered = _order_cover_first(_eligible_property_media_rows(row), limit=limit)
    records = []
    for index, item in enumerate(ordered):
        source_type = photo_source_type(item)
        variant = source_variant(item)
        records.append(
            {
                "id": item.get("id"),
                "source_type": source_type,
                "url": _media_display_url(item, resolved_id),
                "path": item.get("storage_key") or item.get("path"),
                "width": item.get("width"),
                "height": item.get("height"),
                "media_type": item.get("media_type") or "photo",
                "order": index,
                "is_original": source_type in ORIGINAL_SOURCE_TYPES,
                "is_cover": bool(item.get("is_cover")),
                "source": item.get("source"),
                "original_url": item.get("original_url") or item.get("remote_url"),
                "storage_key": item.get("storage_key"),
                "storage_strategy": item.get("storage_strategy"),
                "url_kind": item.get("url_kind"),
                "source_variant": variant,
                "position": item.get("position"),
                "media": item,
            }
        )
        logger.info(
            "[PROPERTY_ORIGINAL_MEDIA] index=%s id=%s url_kind=%s source_variant=%s width=%s height=%s is_original=%s source=%s",
            index,
            item.get("id"),
            item.get("url_kind") or "",
            variant,
            item.get("width"),
            item.get("height"),
            str(source_type in ORIGINAL_SOURCE_TYPES).lower(),
            item.get("original_url") or item.get("storage_key") or item.get("path") or "",
        )
    return records


def get_property_media_for_generation(property_row, limit=5):
    """Cover first, then valid photos. Does not send anything to OpenAI."""
    if not property_row:
        return []
    return [
        record["media"]
        for record in get_property_original_media(
            property_row.get("id"),
            property_row.get("organization_id"),
            limit=limit,
            property_row=property_row,
        )
    ]


def original_media_temp_cache_dir():
    root = Path(tempfile.gettempdir()) / "jrh_original_media"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_original_media_bytes(item, *, cache=None, cache_dir=None):
    """Local managed file, else allowlisted remote original. Optional temp cache."""
    if cache_dir is True:
        cache_dir = original_media_temp_cache_dir()
    path = resolve_media_filesystem_path(item)
    if path:
        return path.read_bytes()
    explicit = str((item or {}).get("path") or "").strip()
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate.read_bytes()
    url = ((item or {}).get("original_url") or (item or {}).get("remote_url") or "").strip()
    if _media_source(item) == "redremax":
        from modules.property_sync.redremax.photos import canonical_photo_url

        url = canonical_photo_url(url)
    if not url:
        return None
    from modules.property_sync.remote_media import fetch_allowed_image_bytes

    return fetch_allowed_image_bytes(url, cache=cache, cache_dir=cache_dir)


def write_original_media_qa_fixture(records, dest_dir, *, limit=4, names=None):
    """QA-only copies. Not a production media store."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    labels = names or ("hero", "secondary_1", "secondary_2", "secondary_3")
    written = []
    for name, record in zip(labels, (records or [])[:limit]):
        payload = load_original_media_bytes(
            (record or {}).get("media") or record,
            cache_dir=True,
        )
        if not payload:
            continue
        path = dest / f"{name}.jpg"
        path.write_bytes(payload)
        written.append(path)
    return written


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
    keys = [
        (item or {}).get("storage_key"),
        (item or {}).get("path"),
    ]
    for key in keys:
        raw = str(key or "").strip()
        if not raw:
            continue
        candidate = Path(raw)
        if candidate.is_file():
            return candidate
        nested = get_private_upload_root() / candidate
        if nested.is_file():
            return nested
    return None
