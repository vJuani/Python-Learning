"""Load real property/agent/style images as provider references. Never invent faces."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from modules.agent_branding import get_agent_presentation_asset
from modules.marketing_renderer import load_property_photos

logger = logging.getLogger(__name__)

STYLE_DIR = Path(__file__).resolve().parent.parent / "marketing_style_references"


def _png_bytes(image, *, keep_alpha=False):
    if image is None:
        return None
    buffer = io.BytesIO()
    if keep_alpha and image.mode in {"RGBA", "LA"}:
        image.save(buffer, format="PNG")
    else:
        image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _open_path_image(path):
    if not path:
        return None
    try:
        image = Image.open(path)
        image.load()
        return image
    except Exception:
        return None


def _open_path_bytes(path, *, keep_alpha=False):
    image = _open_path_image(path)
    return _png_bytes(image, keep_alpha=keep_alpha)


def resolve_listing_agent_photo(agent):
    """Exact ACM/ficha file. Does not invent or search a different portrait."""
    path = (agent or {}).get("photo_path")
    if path:
        image = _open_path_image(path)
        if image is not None:
            return image, path, (agent or {}).get("photo_variant") or "display"
    agent_id = (agent or {}).get("agent_id")
    organization_id = (agent or {}).get("organization_id")
    if not agent_id or not organization_id:
        return None, None, "none"
    fresh = get_agent_presentation_asset(agent_id, organization_id, agent_login_only=True)
    if not fresh:
        return None, None, "none"
    image = _open_path_image(fresh.get("photo_path"))
    return image, fresh.get("photo_path"), fresh.get("photo_variant") or "none"


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
    agent_image, agent_path, variant = (None, None, "none")
    if want_photo:
        agent_image, agent_path, variant = resolve_listing_agent_photo(agent)
    agent_bytes = _png_bytes(agent_image, keep_alpha=True) if agent_image is not None else None
    if want_photo and agent_bytes:
        references.append(
            {
                "role": "agent",
                "label": (
                    f"Image {len(references) + 1}: professional portrait of the real "
                    "estate agent. Use this exact person. Reserve space; the final "
                    "renderer will overlay this same cutout. Do not invent another face."
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
        "marketing refs property=%s property_agent_id=%s agent_branding_found=%s "
        "agent_photo_found=%s agent_photo_variant=%s agent_photo_loaded=%s style=%s",
        sum(1 for item in references if str(item["role"]).startswith("property")),
        agent.get("agent_id"),
        bool(agent.get("agent_id")),
        bool(agent_path or agent.get("has_photo")),
        variant,
        bool(agent_bytes),
        bool(style_bytes),
    )
    return {
        "references": references,
        "agent_photo_present": bool(agent.get("has_photo") or agent_path),
        "agent_photo_loaded": bool(agent_bytes),
        "agent_photo_requested": want_photo,
        "agent_photo_variant": variant,
        "property_agent_id": agent.get("agent_id"),
        "agent_branding_found": bool(agent.get("agent_id")),
        "property_photo_count": sum(1 for item in references if str(item["role"]).startswith("property")),
    }


def agent_overlay_image(context, options):
    if not (options or {}).get("show_agent_photo"):
        return None
    if not (options or {}).get("include_agent"):
        return None
    image, _path, _variant = resolve_listing_agent_photo((context or {}).get("agent") or {})
    return image
