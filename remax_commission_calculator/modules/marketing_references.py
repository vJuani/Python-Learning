"""Load real property/agent/style images as provider references. Never invent faces."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from modules.config import get_private_upload_root
from modules.marketing_renderer import load_agent_photo, load_property_photos
from modules.property_sync.media import resolve_media_filesystem_path

logger = logging.getLogger(__name__)

STYLE_DIR = Path(__file__).resolve().parent.parent / "marketing_style_references"


def _png_bytes(image):
    if image is None:
        return None
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _open_path_bytes(path):
    if not path:
        return None
    try:
        image = Image.open(path)
        return _png_bytes(image)
    except Exception:
        return None


def collect_reference_images(context, options):
    """Return labeled visual inputs. Property and agent photos are real files."""
    photos = list((context or {}).get("photos") or [])
    loaded = load_property_photos(photos)
    references = []
    for index, image in enumerate(loaded[:3]):
        payload = _png_bytes(image)
        if not payload:
            continue
        if index == 0:
            label = (
                "Image 1: main property photograph. "
                "This is the actual property being advertised."
            )
            role = "property_hero"
        else:
            label = (
                f"Image {index + 1}: additional photograph of the SAME property. "
                "Do not invent another listing."
            )
            role = "property_extra"
        references.append(
            {
                "role": role,
                "label": label,
                "bytes": payload,
                "mime": "image/png",
                "name": f"{role}-{index + 1}.png",
            }
        )
    agent = (context or {}).get("agent") or {}
    want_photo = bool(options.get("show_agent_photo") and options.get("include_agent"))
    agent_path = agent.get("photo_path")
    agent_present = bool(agent.get("has_photo") or agent_path)
    agent_image = load_agent_photo(agent_path) if want_photo and agent_path else None
    agent_bytes = _png_bytes(agent_image) if agent_image is not None else None
    if want_photo and agent_bytes:
        references.append(
            {
                "role": "agent",
                "label": (
                    f"Image {len(references) + 1}: professional portrait of the real "
                    "estate agent. The agent must appear recognizably. Do not invent "
                    "another person or face."
                ),
                "bytes": agent_bytes,
                "mime": "image/png",
                "name": "agent-portrait.png",
            }
        )
    style_path = next(
        (item for item in sorted(STYLE_DIR.glob("*")) if item.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}),
        None,
    ) if STYLE_DIR.is_dir() else None
    style_bytes = _open_path_bytes(style_path)
    if style_bytes:
        references.append(
            {
                "role": "style",
                "label": (
                    f"Image {len(references) + 1}: visual style reference only. "
                    "Do not copy its property or people."
                ),
                "bytes": style_bytes,
                "mime": "image/png",
                "name": "style-reference.png",
            }
        )
    logger.info(
        "marketing refs property=%s agent_photo_present=%s agent_photo_loaded=%s style=%s",
        sum(1 for item in references if str(item["role"]).startswith("property")),
        bool(agent_present),
        bool(agent_bytes),
        bool(style_bytes),
    )
    return {
        "references": references,
        "agent_photo_present": bool(agent_present),
        "agent_photo_loaded": bool(agent_bytes),
        "agent_photo_requested": want_photo,
        "property_photo_count": sum(1 for item in references if str(item["role"]).startswith("property")),
    }


def agent_overlay_image(context, options):
    if not (options or {}).get("show_agent_photo"):
        return None
    if not (options or {}).get("include_agent"):
        return None
    agent = (context or {}).get("agent") or {}
    return load_agent_photo(agent.get("photo_path"))
