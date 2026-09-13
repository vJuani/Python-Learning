"""Load real property/agent/style images as provider references. Never invent faces."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from modules.agent_branding import get_agent_presentation_asset
from modules.branding import resolve_brand_logo_path
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
    """Labeled inputs: approved style, real property, real agent."""
    from modules.marketing_visual_spec import STYLE_REFERENCE_LABEL, approved_style_path

    photos = list((context or {}).get("photos") or [])
    loaded = load_property_photos(photos)
    references = []
    style_path = approved_style_path()
    style_bytes = _open_path_bytes(style_path)
    if style_bytes:
        references.append(
            {
                "role": "style",
                "label": f"Reference A: APPROVED JRH STYLE. {STYLE_REFERENCE_LABEL}",
                "bytes": style_bytes,
                "mime": "image/jpeg",
                "name": "jrh-approved-style.jpg",
            }
        )
    for index, image in enumerate(loaded[:3]):
        payload = _png_bytes(image)
        if not payload:
            continue
        letter = "BCD"[index]
        if index == 0:
            label = (
                f"Reference {letter}: REAL PROPERTY HERO. "
                "This is the actual property being advertised. Use it prominently."
            )
            role = "property_hero"
        else:
            label = (
                f"Reference {letter}: REAL PROPERTY SECONDARY of the SAME listing. "
                "Do not invent another property."
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
                    "Reference D: REAL AGENT PORTRAIT. This is the exact person. "
                    "Integrate a clean professional cutout, small or medium, never huge. "
                    "Do not invent another face and do not duplicate the portrait."
                ),
                "bytes": agent_bytes,
                "mime": "image/png",
                "name": "agent-portrait.png",
            }
        )
    logo_bytes = _open_path_bytes(resolve_brand_logo_path())
    if logo_bytes:
        references.append(
            {
                "role": "logo",
                "label": "Reference E: REAL JRH ONE LOGO. Use small, elegant, never stretched.",
                "bytes": logo_bytes,
                "mime": "image/png",
                "name": "jrh-one-logo.png",
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
