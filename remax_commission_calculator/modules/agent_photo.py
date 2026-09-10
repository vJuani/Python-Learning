"""Agent professional photo upload. Preserves PNG/WebP alpha. No auto background removal."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from PIL import UnidentifiedImageError

from modules.config import get_private_upload_root
from modules.database.agents_repository import (
    clear_agent_profile_photo,
    get_agent_record,
    update_agent_profile_photo,
)
from modules.database.tenant import TenantError, require_organization_id
from modules.property_sync.security import MAX_MEDIA_BYTES


ALLOWED_AGENT_PHOTO_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_EDGE = 2400
MIN_EDGE = 64
DISPLAY_EDGE = 1400


class AgentPhotoError(ValueError):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


def agent_photo_dir(organization_id, agent_id):
    return (
        get_private_upload_root()
        / "organizations"
        / str(organization_id)
        / "agents"
        / str(agent_id)
        / "profile"
    )


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _detect_mime(payload, declared):
    declared = str(declared or "").split(";", 1)[0].strip().lower()
    if declared in ALLOWED_AGENT_PHOTO_TYPES:
        return declared
    try:
        image = Image.open(io.BytesIO(payload))
        fmt = (image.format or "").upper()
    except (UnidentifiedImageError, OSError) as error:
        raise AgentPhotoError("agent_photo_err_mime") from error
    mapping = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    mime = mapping.get(fmt)
    if not mime:
        raise AgentPhotoError("agent_photo_err_mime")
    return mime


def _normalize_image(payload):
    try:
        image = Image.open(io.BytesIO(payload))
        image.load()
    except (UnidentifiedImageError, OSError) as error:
        raise AgentPhotoError("agent_photo_err_mime") from error
    image = ImageOps.exif_transpose(image)
    width, height = image.size
    if min(width, height) < MIN_EDGE:
        raise AgentPhotoError("agent_photo_err_dimensions")
    longest = max(width, height)
    if longest > MAX_EDGE:
        scale = MAX_EDGE / float(longest)
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


def _save_image(image, path, mime):
    path.parent.mkdir(parents=True, exist_ok=True)
    if mime == "image/png":
        if image.mode not in {"RGBA", "RGB", "L", "LA", "P"}:
            image = image.convert("RGBA")
        image.save(path, format="PNG", optimize=True)
        return "image/png"
    if mime == "image/webp":
        if image.mode not in {"RGBA", "RGB"}:
            image = image.convert("RGBA" if "A" in image.mode else "RGB")
        image.save(path, format="WEBP", quality=90, method=6)
        return "image/webp"
    if image.mode in {"RGBA", "LA", "P"}:
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.save(path, format="JPEG", quality=90, optimize=True)
    return "image/jpeg"


def _display_image(image):
    width, height = image.size
    longest = max(width, height)
    if longest <= DISPLAY_EDGE:
        return image
    scale = DISPLAY_EDGE / float(longest)
    return image.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        Image.Resampling.LANCZOS,
    )


def save_agent_profile_photo(organization_id, agent_id, payload, *, content_type=None):
    organization_id = require_organization_id(organization_id)
    if payload is None:
        raise AgentPhotoError("agent_photo_err_missing")
    if not isinstance(payload, (bytes, bytearray)):
        payload = payload.read()
    payload = bytes(payload or b"")
    if not payload:
        raise AgentPhotoError("agent_photo_err_missing")
    if len(payload) > MAX_MEDIA_BYTES:
        raise AgentPhotoError("agent_photo_err_too_large")
    mime = _detect_mime(payload, content_type)
    image = _normalize_image(payload)
    extension = ALLOWED_AGENT_PHOTO_TYPES[mime]
    directory = agent_photo_dir(organization_id, agent_id)
    original_name = f"original{extension}"
    display_name = f"display{extension}"
    original_path = directory / original_name
    display_path = directory / display_name
    stored_mime = _save_image(image, original_path, mime)
    display = _display_image(image)
    _save_image(display, display_path, mime)
    relative_original = str(original_path.relative_to(get_private_upload_root())).replace(
        "\\", "/"
    )
    relative_display = str(display_path.relative_to(get_private_upload_root())).replace(
        "\\", "/"
    )
    return update_agent_profile_photo(
        agent_id,
        organization_id,
        profile_photo_key=relative_display,
        profile_photo_original_key=relative_original,
        profile_photo_mime=stored_mime,
        profile_photo_width=display.size[0],
        profile_photo_height=display.size[1],
        profile_photo_updated_at=_now_iso(),
    )


def delete_agent_profile_photo(organization_id, agent_id):
    agent = get_agent_record(agent_id, organization_id)
    if agent is None:
        raise TenantError("Agent was not found in this organization.")
    root = get_private_upload_root()
    for key in (agent.get("profile_photo_key"), agent.get("profile_photo_original_key")):
        if not key:
            continue
        path = root / key
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
    return clear_agent_profile_photo(agent_id, organization_id)


def resolve_agent_photo_path(agent, *, original=False):
    key = (agent or {}).get("profile_photo_original_key" if original else "profile_photo_key")
    if not key:
        return None
    path = get_private_upload_root() / key
    if path.is_file():
        return path
    return None


def remove_background(agent_photo):
    """Reserved for a future background-removal provider. Not implemented."""
    raise NotImplementedError("remove_background is not enabled yet")


def image_has_alpha(path):
    if path is None or not Path(path).is_file():
        return False
    with Image.open(path) as image:
        return image.mode in {"RGBA", "LA", "PA"} or (
            image.mode == "P" and "transparency" in image.info
        )
