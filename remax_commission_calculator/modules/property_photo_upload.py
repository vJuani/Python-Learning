"""Local photo files for an office's own property. Original bytes stay intact."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from modules.database.property_media_repository import (
    MEDIA_PHOTO,
    STRATEGY_COPY,
    get_property_media,
    list_property_media,
    mark_media_removed_from_source,
    set_property_media_cover,
    set_property_media_positions,
    upsert_property_media,
)
from modules.config import get_private_upload_root
from modules.property_sync.media import media_storage_dir, resolve_media_filesystem_path
from modules.property_sync.security import MAX_MEDIA_BYTES

SOURCE_MANUAL = "manual"

ALLOWED_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
PIL_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class PropertyPhotoError(Exception):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


def _declared_type(value):
    raw = str(value or "").split(";", 1)[0].strip().lower()
    if raw == "image/jpg":
        return "image/jpeg"
    return raw


def inspect_local_image(filename, declared_type, payload):
    """Reject anything that is not a real JPG, PNG, or WebP. Do not transform it."""
    if not payload:
        raise PropertyPhotoError("property_photo_empty")
    if len(payload) > MAX_MEDIA_BYTES:
        raise PropertyPhotoError("property_photo_too_large")
    extension = Path(str(filename or "")).suffix.lower()
    expected = ALLOWED_EXTENSIONS.get(extension)
    if expected is None:
        raise PropertyPhotoError("property_photo_invalid")
    declared = _declared_type(declared_type)
    if declared and declared != expected:
        raise PropertyPhotoError("property_photo_invalid")
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            image.load()
            detected = PIL_FORMATS.get((image.format or "").upper())
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise PropertyPhotoError("property_photo_invalid") from None
    if detected != expected or width < 1 or height < 1:
        raise PropertyPhotoError("property_photo_invalid")
    return {
        "extension": extension,
        "content_type": detected,
        "width": width,
        "height": height,
    }


def _store_original(organization_id, property_id, digest, extension, payload):
    directory = media_storage_dir(organization_id, property_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}{extension}"
    path.write_bytes(payload)
    return str(path.relative_to(get_private_upload_root())).replace("\\", "/")


def _active_photos(organization_id, property_id):
    rows = [
        item
        for item in list_property_media(organization_id, property_id)
        if item.get("media_type") in (None, "", MEDIA_PHOTO)
    ]
    rows.sort(key=lambda item: (int(item.get("position") or 0), int(item.get("id") or 0)))
    return rows


def upload_local_photos(organization_id, property_id, storages):
    """Save original files on the property. The first photo becomes the cover."""
    incoming = [item for item in storages or [] if item is not None]
    if not incoming:
        raise PropertyPhotoError("property_photo_none")
    active = _active_photos(organization_id, property_id)
    has_cover = any(item.get("is_cover") for item in active)
    position = max((int(item.get("position") or 0) for item in active), default=-1) + 1
    saved = []
    errors = []
    for storage in incoming:
        filename = getattr(storage, "filename", "") or ""
        if not str(filename).strip():
            continue
        payload = storage.read(MAX_MEDIA_BYTES + 1)
        declared = getattr(storage, "mimetype", None) or getattr(storage, "content_type", None)
        try:
            inspected = inspect_local_image(filename, declared, payload)
        except PropertyPhotoError as error:
            errors.append(error.message_key)
            continue
        digest = hashlib.sha256(payload).hexdigest()
        external_id = f"local-{digest}"
        from modules.database.property_media_repository import find_media_by_external_id

        existing = find_media_by_external_id(
            organization_id,
            property_id,
            SOURCE_MANUAL,
            external_id,
        )
        is_cover = bool(existing and existing.get("is_cover")) or not has_cover
        stored_position = int(existing["position"]) if existing else position
        storage_key = _store_original(
            organization_id,
            property_id,
            digest,
            inspected["extension"],
            payload,
        )
        row = upsert_property_media(
            organization_id,
            property_id,
            source=SOURCE_MANUAL,
            external_media_id=external_id,
            media_type=MEDIA_PHOTO,
            storage_key=storage_key,
            storage_strategy=STRATEGY_COPY,
            url_kind="original",
            position=stored_position,
            is_cover=is_cover,
            width=inspected["width"],
            height=inspected["height"],
            content_type=inspected["content_type"],
            content_hash=digest,
        )
        if not existing:
            position += 1
        if row.get("is_cover"):
            has_cover = True
        saved.append(row)
    if not saved and not errors:
        raise PropertyPhotoError("property_photo_none")
    return saved, errors


def list_managed_photos(property_data):
    from modules.property_sync.media import get_property_media_url

    if not property_data or property_data.get("id") is None:
        return []
    property_id = property_data["id"]
    photos = []
    for item in _active_photos(property_data.get("organization_id"), property_id):
        if item.get("source") == "marketing":
            continue
        src = get_property_media_url(item, property_id)
        if not src:
            continue
        photos.append(
            {
                "id": item["id"],
                "src": src,
                "is_cover": bool(item.get("is_cover")),
                "position": item.get("position") or 0,
            }
        )
    return photos


def make_cover(organization_id, property_id, media_id):
    if not set_property_media_cover(organization_id, property_id, media_id):
        raise PropertyPhotoError("property_photo_missing", 404)


def reorder_photos(organization_id, property_id, ordered_ids):
    active = _active_photos(organization_id, property_id)
    current = [int(item["id"]) for item in active]
    wanted = []
    for raw in ordered_ids or []:
        try:
            wanted.append(int(raw))
        except (TypeError, ValueError):
            raise PropertyPhotoError("property_photo_order_invalid") from None
    if wanted != list(dict.fromkeys(wanted)) or set(wanted) != set(current):
        raise PropertyPhotoError("property_photo_order_invalid")
    set_property_media_positions(organization_id, property_id, wanted)


def move_photo(organization_id, property_id, media_id, direction):
    ids = [int(item["id"]) for item in _active_photos(organization_id, property_id)]
    try:
        index = ids.index(int(media_id))
    except ValueError:
        raise PropertyPhotoError("property_photo_missing", 404) from None
    if direction == "earlier" and index > 0:
        ids[index - 1], ids[index] = ids[index], ids[index - 1]
    elif direction == "later" and index < len(ids) - 1:
        ids[index + 1], ids[index] = ids[index], ids[index + 1]
    else:
        return
    set_property_media_positions(organization_id, property_id, ids)


def _unlink_owned_file(item, organization_id, property_id):
    path = resolve_media_filesystem_path(item)
    if path is None:
        return
    root = media_storage_dir(organization_id, property_id).resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return
    others = _active_photos(organization_id, property_id)
    if any(other.get("storage_key") == item.get("storage_key") for other in others):
        return
    resolved.unlink(missing_ok=True)


def delete_photo(organization_id, property_id, media_id):
    item = get_property_media(media_id, organization_id)
    if (
        item is None
        or int(item.get("property_id") or 0) != int(property_id)
        or item.get("status") != "active"
    ):
        raise PropertyPhotoError("property_photo_missing", 404)
    was_cover = bool(item.get("is_cover"))
    mark_media_removed_from_source(item["id"], organization_id)
    _unlink_owned_file(item, organization_id, property_id)
    if was_cover:
        remaining = _active_photos(organization_id, property_id)
        if remaining:
            set_property_media_cover(organization_id, property_id, remaining[0]["id"])
