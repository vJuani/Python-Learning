"""Official property marketing templates. HTML/CSS + Playwright only.

JRH One never appears on the creative. Layout is deterministic.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from modules.agent_branding import (
    email_mailto,
    format_instagram_handle,
    format_whatsapp_display,
    instagram_profile_url,
    whatsapp_url,
)
from modules.config import BASE_DIR
from modules.listing_photo_origin import ORIGINAL_SOURCE_TYPES, photo_source_type, source_variant
from modules.marketing_context import MarketingError
from modules.marketing_branding import DEMO_MARKETING_BRANDING
from modules.marketing_copy import summarize_listing_copy
from modules.marketing_language import default_cta, listing_benefit_line
from modules.marketing_photo_fit import (
    CLEAN_GRID_CARD_HEIGHT,
    CLEAN_GRID_CARD_TOP,
    CLEAN_GRID_HERO_MAX,
    CLEAN_GRID_HERO_WIDE_MAX,
    CLEAN_GRID_MASTER,
    CLEAN_GRID_PHOTO_AREA,
    CLEAN_GRID_THUMB_GAP,
    CLEAN_GRID_THUMB_LEFT,
    CLEAN_GRID_THUMB_MAX,
    FULL_BLEED_MASTER,
    FULL_BLEED_PHOTO_MAX,
    LIFESTYLE_BELOW_TOP,
    LIFESTYLE_PHOTO_TOP,
    PHOTO_GAP,
    PREMIUM_BELOW_TOP,
    PREMIUM_PHOTO_TOP,
    plan_original_photo,
)
from modules.marketing_render_html import (
    EDITORIAL_FONT_MAPPING,
    HTML_RENDERER,
    _data_uri_from_image,
    _font_face_css,
    screenshot_poster,
)
from modules.marketing_photo_selector import (
    is_lifestyle_strong,
    is_powerful_hero,
    photo_key,
    photo_scene_label,
    score_listing_photo,
    select_photos_for_item,
    unique_scene_keys,
)
from modules.marketing_renderer import load_agent_photo
from modules.property_sync.media import (
    get_property_media_url,
    get_property_original_media,
    list_property_media,
    load_original_media_bytes,
    resolve_media_filesystem_path,
)

logger = logging.getLogger(__name__)

TEMPLATE_CLEAN_GRID = "property_clean_grid"
TEMPLATE_LIFESTYLE_DARK = "property_lifestyle_dark"
TEMPLATE_PREMIUM_HERO = "property_premium_hero"
TEMPLATE_AUTOMATIC = "automatic"

PROPERTY_TEMPLATES = (
    TEMPLATE_CLEAN_GRID,
    TEMPLATE_LIFESTYLE_DARK,
    TEMPLATE_PREMIUM_HERO,
)
TEMPLATE_FILES = {
    TEMPLATE_CLEAN_GRID: "property_clean_grid.html",
    TEMPLATE_LIFESTYLE_DARK: "property_lifestyle_dark.html",
    TEMPLATE_PREMIUM_HERO: "property_premium_hero.html",
}
PACKAGED_BALLOON = (
    BASE_DIR / "static" / "assets" / "branding" / "remax_data_house_balloon.png"
)
CSS_RELATIVE = {
    TEMPLATE_CLEAN_GRID: Path("static") / "css" / "marketing-render" / "property_clean_grid.css",
    TEMPLATE_LIFESTYLE_DARK: Path("static") / "css" / "marketing-render" / "property_lifestyle_dark.css",
    TEMPLATE_PREMIUM_HERO: Path("static") / "css" / "marketing-render" / "property_premium_hero.css",
}
SHARED_CSS = Path("static") / "css" / "marketing-render" / "property_photo_frame.css"
MASTER_SIZES = {
    TEMPLATE_CLEAN_GRID: CLEAN_GRID_MASTER,
    TEMPLATE_LIFESTYLE_DARK: FULL_BLEED_MASTER,
    TEMPLATE_PREMIUM_HERO: FULL_BLEED_MASTER,
}
# Characters that fit at full contact text size before the CTA / quote column.
CONTACT_TEXT_CAPACITY = {
    TEMPLATE_CLEAN_GRID: 24,
    TEMPLATE_LIFESTYLE_DARK: 27,
    TEMPLATE_PREMIUM_HERO: 40,
}
HERO_PHOTO_MAX = {
    TEMPLATE_CLEAN_GRID: CLEAN_GRID_HERO_MAX,
    TEMPLATE_LIFESTYLE_DARK: FULL_BLEED_PHOTO_MAX,
    TEMPLATE_PREMIUM_HERO: FULL_BLEED_PHOTO_MAX,
}
LEGACY_POST_TEMPLATES = frozenset(
    {
        "modern_commercial_v3",
        "modern_commercial_v2",
        "modern_commercial_v1",
        "modern_premium_v1",
        "marketing_post_v2",
        "post",
    }
)
LEGACY_RENDERERS = frozenset(
    {
        "pillow_commercial_v2",
        "pillow_modern_renderer",
        "openai_images",
    }
)
LEGACY_ROUTE_BLOCKED = "PROPERTY_MARKETING_LEGACY_ROUTE_BLOCKED"
DESIGN_ALIASES = {
    "automatic": TEMPLATE_AUTOMATIC,
    "automatico": TEMPLATE_AUTOMATIC,
    "auto": TEMPLATE_AUTOMATIC,
    "clean grid": TEMPLATE_CLEAN_GRID,
    "clean_grid": TEMPLATE_CLEAN_GRID,
    "clean-grid": TEMPLATE_CLEAN_GRID,
    "lifestyle dark": TEMPLATE_LIFESTYLE_DARK,
    "lifestyle_dark": TEMPLATE_LIFESTYLE_DARK,
    "lifestyle-dark": TEMPLATE_LIFESTYLE_DARK,
    "premium hero": TEMPLATE_PREMIUM_HERO,
    "premium_hero": TEMPLATE_PREMIUM_HERO,
    "premium-hero": TEMPLATE_PREMIUM_HERO,
}
ICON_ROOMS = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 11V6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5V11"/>'
    '<path d="M3 12.5a1.8 1.8 0 0 1 3.6 0V15h10.8v-2.5a1.8 1.8 0 0 1 3.6 0V19H3z"/>'
    '<path d="M5 19v2M19 19v2M10.5 8.5h3"/></svg>'
)
ICON_BED = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 11V6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5V11"/>'
    '<path d="M6.5 11V8.8h4.5V11M13 11V8.8h4.5V11"/><path d="M3 11h18v6H3z"/><path d="M4 17v3M20 17v3"/></svg>'
)
ICON_BATH = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 11h18v1.2a6.3 6.3 0 0 1-6.3 6.3H9.3A6.3 6.3 0 0 1 3 12.2z"/>'
    '<path d="M12 11V5.2A1.7 1.7 0 0 1 13.7 3.5H16"/><path d="M9.5 18.5L8.5 21M14.5 18.5l1 2.5"/>'
    '<path d="M16 3.5v2.2"/></svg>'
)
ICON_AREA = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4.5" y="3.5" width="15" height="17" rx="1.2"/>'
    '<path d="M9 16l6-8M9 16v-3M9 16h3M15 8v3M15 8h-3"/></svg>'
)
ICON_WA = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#25D366" stroke="none" d="M12 2a10 10 0 0 0-8.5 15.3L2 22l4.9-1.4A10 10 0 1 0 12 2z"/>'
    '<path fill="#fff" stroke="none" d="M16.6 14.3c-.2-.1-1.3-.6-1.5-.7s-.4-.1-.5.1-.6.7-.7.9-.3.2-.5.1a6.5 6.5 0 0 1-1.9-1.2 7.2 7.2 0 0 1-1.3-1.6c-.1-.2 0-.4.1-.5l.4-.4.2-.3c.1-.1 0-.3 0-.4l-.7-1.7c-.2-.4-.4-.4-.5-.4h-.4c-.1 0-.4.1-.6.3s-.8.8-.8 1.9.8 2.2.9 2.3a10.5 10.5 0 0 0 4 3.4c1.5.6 1.8.5 2.2.4s1.3-.5 1.4-1 .2-.9.1-1z"/></svg>'
)
ICON_IG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="17" height="17" rx="5" fill="none" stroke="currentColor" stroke-width="1.8"/>'
    '<circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="1.8"/>'
    '<circle cx="17.2" cy="6.8" r="1.1" fill="currentColor" stroke="none"/></svg>'
)
ICON_EMAIL = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.2" y="5.2" width="17.6" height="13.6" rx="2.2" fill="none" stroke="currentColor" stroke-width="1.8"/>'
    '<path d="M4.2 7.2L12 13.1 19.8 7.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

_jinja_env = None


class PropertyMarketingError(MarketingError):
    def __init__(self, message="PROPERTY_MARKETING_RENDER_FAILED", status_code=500):
        super().__init__(message, status_code)


def is_property_marketing_template(name):
    raw = str(name or "").strip()
    return raw in PROPERTY_TEMPLATES or raw == TEMPLATE_AUTOMATIC


class PropertyMarketingRouteBlocked(PropertyMarketingError):
    def __init__(self):
        super().__init__(LEGACY_ROUTE_BLOCKED, 500)


def uses_property_marketing_renderer(fmt, options=None):
    """Every listing visual (post, story, flyer, carousel...) renders with property_* templates."""
    del fmt
    return (options or {}).get("property_marketing") is not False


def _fold_design(value):
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.strip().lower().split())


def is_legacy_design(value):
    raw = str(value or "").strip()
    return bool(raw) and (raw in LEGACY_POST_TEMPLATES or raw.startswith("modern_") or raw in LEGACY_RENDERERS)


def normalize_property_design(value):
    """Design selection -> "automatic" or one property_* id. Legacy/unknown names migrate to automatic."""
    raw = str(value or "").strip()
    if raw in PROPERTY_TEMPLATES:
        return raw
    return DESIGN_ALIASES.get(_fold_design(raw), TEMPLATE_AUTOMATIC)


def resolve_property_marketing_template(requested, photos, facts=None):
    """Always returns one of the three property_* ids."""
    return select_property_template(normalize_property_design(requested), photos, facts)


def log_render_route(
    step,
    *,
    conversation_id=None,
    generation_id=None,
    kind=None,
    property_id=None,
    requested_design=None,
    resolved_design=None,
    template_before_render=None,
    renderer_before_render=None,
    legacy_fallback_reason=None,
):
    logger.info(
        "[MARKETING_RENDER_ROUTE] step=%s conversation_id=%s generation_id=%s kind=%s property_id=%s "
        "requested_design=%s resolved_design=%s template_before_render=%s renderer_before_render=%s "
        "legacy_fallback_reason=%s",
        step,
        conversation_id if conversation_id is not None else "",
        generation_id if generation_id is not None else "",
        kind or "",
        property_id if property_id is not None else "",
        requested_design or "",
        resolved_design or "",
        template_before_render or "",
        renderer_before_render or "",
        legacy_fallback_reason or "none",
    )


def assert_property_route(template, *, renderer=None, requested=None, kind=None, property_id=None, callsite=""):
    template = str(template or "").strip()
    renderer = str(renderer or "").strip()
    template_ok = template in PROPERTY_TEMPLATES or template == TEMPLATE_AUTOMATIC
    renderer_ok = not renderer or renderer == HTML_RENDERER
    if template_ok and renderer_ok:
        return template
    logger.error(
        "[%s] requested=%s resolved=%s renderer=%s kind=%s property_id=%s callsite=%s",
        LEGACY_ROUTE_BLOCKED,
        requested or "",
        template,
        renderer,
        kind or "",
        property_id if property_id is not None else "",
        callsite,
    )
    raise PropertyMarketingRouteBlocked()


def _clean(value):
    text = " ".join(str(value or "").split())
    if text.lower() in {"none", "null", "n/a", "undefined", "-"}:
        return ""
    return text


def _as_int(value):
    if value in (None, "", 0, "0"):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _jinja():
    global _jinja_env
    if _jinja_env is None:
        templates = BASE_DIR / "templates" / "marketing" / "render"
        _jinja_env = Environment(
            loader=FileSystemLoader(str(templates)),
            autoescape=select_autoescape(["html"]),
        )
    return _jinja_env


def _read_css(template_id):
    shared = (BASE_DIR / SHARED_CSS).read_text(encoding="utf-8")
    path = BASE_DIR / CSS_RELATIVE[template_id]
    return path.read_text(encoding="utf-8") + "\n" + shared


def _data_uri_from_bytes(payload, mime="image/jpeg"):
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _image_bytes_meta(payload):
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        fmt = (image.format or "JPEG").upper()
        getter = getattr(image, "get_format_mimetype", None)
        mime = getter() if callable(getter) else None
        if not mime:
            mime = {
                "JPEG": "image/jpeg",
                "JPG": "image/jpeg",
                "PNG": "image/png",
                "WEBP": "image/webp",
                "GIF": "image/gif",
            }.get(fmt, "image/jpeg")
        return {
            "width": int(image.size[0]),
            "height": int(image.size[1]),
            "mime": mime,
            "format": fmt,
        }


def _listing_photo_mime(row, meta):
    raw = str((row or {}).get("content_type") or "").split(";")[0].strip().lower()
    if raw.startswith("image/"):
        return raw
    return (meta or {}).get("mime") or "image/jpeg"


def _sha256(payload):
    return hashlib.sha256(payload).hexdigest() if payload else ""


def _source_audit(item, payload, meta):
    """Source file/URL vs bytes handed to the renderer. Must be byte-identical."""
    item = item or {}
    source_url = item.get("original_url") or item.get("remote_url") or ""
    source_path = resolve_media_filesystem_path(item)
    if not source_path:
        explicit = str(item.get("path") or "").strip()
        source_path = Path(explicit) if explicit and Path(explicit).is_file() else None
    source_bytes = source_path.read_bytes() if source_path else payload
    source_size = (None, None)
    if source_bytes:
        with Image.open(io.BytesIO(source_bytes)) as image:
            source_size = image.size
    audit = {
        "source_path": str(source_path) if source_path else "",
        "source_url": source_url,
        "original_width": source_size[0],
        "original_height": source_size[1],
        "byte_size": len(source_bytes or b""),
        "sha256": _sha256(source_bytes),
        "loaded_width": meta["width"],
        "loaded_height": meta["height"],
        "loaded_byte_size": len(payload),
        "loaded_sha256": _sha256(payload),
    }
    audit["match"] = (
        audit["sha256"] == audit["loaded_sha256"]
        and (audit["original_width"], audit["original_height"])
        == (audit["loaded_width"], audit["loaded_height"])
    )
    logger.info(
        "[PROPERTY_MARKETING_SOURCE_AUDIT] id=%s source_path=%s source_url=%s original=%sx%s byte_size=%s sha256=%s loaded=%sx%s loaded_sha256=%s match=%s",
        item.get("id"),
        audit["source_path"],
        audit["source_url"],
        audit["original_width"],
        audit["original_height"],
        audit["byte_size"],
        audit["sha256"],
        audit["loaded_width"],
        audit["loaded_height"],
        audit["loaded_sha256"],
        str(audit["match"]).lower(),
    )
    return audit


def _load_listing_photos(photo_rows):
    """Original bytes only. No Pillow RGB convert, JPEG recompress, or resize."""
    loaded = []
    cache = {}
    for index, item in enumerate(photo_rows or []):
        try:
            payload = load_original_media_bytes(item, cache=cache, cache_dir=True)
            if not payload:
                logger.error(
                    "original_photo_fetch_failed source_type=%s original_url=%s path=%s",
                    (item or {}).get("source_type") or (item or {}).get("source"),
                    (item or {}).get("original_url"),
                    (item or {}).get("storage_key") or (item or {}).get("path"),
                )
                continue
            meta = _image_bytes_meta(payload)
            mime = _listing_photo_mime(item, meta)
            source = (
                (item or {}).get("original_url")
                or (item or {}).get("remote_url")
                or (item or {}).get("storage_key")
                or (item or {}).get("path")
                or ""
            )
            source_type = photo_source_type(item)
            is_original = source_type in ORIGINAL_SOURCE_TYPES
            logger.info(
                "[PROPERTY_MARKETING_PHOTO]\nindex=%s\nwidth=%s\nheight=%s\nbytes=%s\nsource=%s\nmime=%s\nis_original=%s\nurl_kind=%s\nsource_variant=%s\nrecompressed=false",
                index,
                meta["width"],
                meta["height"],
                len(payload),
                source,
                mime,
                str(is_original).lower(),
                (item or {}).get("url_kind") or "",
                source_variant(item),
            )
            audit = _source_audit(item, payload, meta)
            row = dict(item or {})
            row["width"] = meta["width"]
            row["height"] = meta["height"]
            row["content_type"] = mime
            loaded.append(
                (row, payload, {**meta, "mime": mime, "bytes": len(payload), "audit": audit})
            )
        except Exception:
            logger.exception(
                "original_photo_fetch_failed source_type=%s original_url=%s path=%s",
                (item or {}).get("source_type") or (item or {}).get("source"),
                (item or {}).get("original_url"),
                (item or {}).get("storage_key") or (item or {}).get("path"),
            )
    return loaded


def _logo_uri(path):
    if not path:
        return ""
    with Image.open(path) as image:
        image.load()
        if image.mode in {"RGBA", "LA"} or "A" in image.getbands():
            return _data_uri_from_image(image.convert("RGBA"), fmt="PNG", keep_alpha=True)
        return _data_uri_from_image(image.convert("RGB"), fmt="PNG", keep_alpha=False)


def _office_logo_path(facts):
    """(path, source): organization logo first, packaged RE/MAX balloon as fallback."""
    from modules.marketing_renderer import _office_logo

    path = _office_logo(facts)
    if path:
        return path, "organization"
    brand = _clean(facts.get("brand_name") or facts.get("office_name")).casefold()
    if PACKAGED_BALLOON.is_file() and ("re/max" in brand or "data house" in brand):
        return str(PACKAGED_BALLOON), "packaged_balloon_fallback"
    return None, "none"


def _split_brand(name):
    raw = _clean(name)
    if not raw:
        return "", ""
    if "data house" in raw.casefold() and "re/max" in raw.casefold():
        return "RE/MAX", "DATA HOUSE"
    parts = raw.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _zone_line(facts):
    neighborhood = _clean(facts.get("neighborhood"))
    locality = _clean(facts.get("locality") or facts.get("jurisdiction"))
    if neighborhood and locality and neighborhood.casefold() != locality.casefold():
        return f"{neighborhood}, {locality}".upper()
    return (neighborhood or locality).upper()


def _address_line(facts):
    return _clean(facts.get("title") or facts.get("address") or facts.get("location_line"))


def _operation_label(facts, language="es"):
    purpose = _clean(facts.get("purpose") or facts.get("operation_type")).lower()
    kicker = _clean(facts.get("kicker"))
    if kicker:
        return kicker.upper()
    if language != "es":
        if purpose in {"rental", "rent", "alquiler"}:
            return "FOR RENT"
        return "FOR SALE"
    if purpose in {"rental", "rent", "alquiler"}:
        return "EN ALQUILER"
    return "EN VENTA"


def _facts_row(facts, language="es"):
    items = []
    rooms = _as_int(facts.get("rooms"))
    bedrooms = _as_int(facts.get("bedrooms"))
    bathrooms = _as_int(facts.get("bathrooms"))
    area = facts.get("total_m2") or facts.get("covered_m2")
    area_n = _as_int(area)
    if rooms:
        items.append(
            {
                "icon": ICON_ROOMS,
                "value": str(rooms),
                "label": "Ambientes" if language == "es" else "Rooms",
            }
        )
    if bedrooms:
        items.append(
            {
                "icon": ICON_BED,
                "value": str(bedrooms),
                "label": "Dormitorios" if language == "es" else "Bedrooms",
            }
        )
    if bathrooms:
        items.append(
            {
                "icon": ICON_BATH,
                "value": str(bathrooms),
                "label": "Baños" if language == "es" else "Baths",
            }
        )
    if area_n:
        items.append(
            {
                "icon": ICON_AREA,
                "value": f"{area_n} m²",
                "label": "Totales" if language == "es" else "Total",
            }
        )
    return items[:4]


def _price_view(facts, *, show_price=True):
    if not show_price:
        return {"currency": "", "amount": ""}
    label = _clean(facts.get("price_label"))
    if not label:
        return {"currency": "", "amount": ""}
    parts = label.split(" ", 1)
    if len(parts) == 2 and parts[0].isalpha():
        return {"currency": parts[0], "amount": parts[1]}
    return {"currency": "", "amount": label}


def _copy_line(facts, planned, language="es"):
    for candidate in (
        (planned or {}).get("subheadline"),
        (planned or {}).get("short_hook"),
        listing_benefit_line(language, facts),
        facts.get("benefit_line"),
    ):
        line = _clean(candidate)
        if line and len(line) <= 90:
            return line
    return ""


def _is_demo_broker(facts):
    name = _clean(facts.get("legal_broker_name") or facts.get("broker_name"))
    license_no = _clean(facts.get("legal_broker_license") or facts.get("broker_license"))
    demo_name = _clean(DEMO_MARKETING_BRANDING.get("legal_broker_name"))
    demo_license = _clean(DEMO_MARKETING_BRANDING.get("legal_broker_license"))
    if facts.get("used_demo_fallback") and not facts.get("legal_complete"):
        return True
    return bool(name and name == demo_name and license_no == demo_license and facts.get("used_demo_fallback"))


def _broker_view(facts):
    if _is_demo_broker(facts):
        return {"loaded": False, "line": ""}
    name = _clean(facts.get("legal_broker_name") or facts.get("broker_name"))
    license_no = _clean(facts.get("legal_broker_license") or facts.get("broker_license"))
    college = _clean(facts.get("legal_broker_college") or facts.get("broker_college"))
    stored = _clean(facts.get("legal_footer_line") or facts.get("broker_footer_text"))
    if stored and "jrh" not in stored.casefold() and "mauro marvisi" not in stored.casefold():
        line = stored
    else:
        parts = []
        if name:
            parts.append(f"Martillero responsable: {name}")
        if license_no:
            parts.append(
                license_no
                if license_no.lower().startswith(("mat", "c.i", "cucicba", "cmcpsi"))
                else f"Mat. {license_no}"
            )
        if college:
            parts.append(college)
        line = " | ".join(parts)
    if not line:
        return {"loaded": False, "line": ""}
    return {"loaded": True, "line": line, "name": name, "license": license_no, "college": college}


def _agent_view(agent, *, include_agent=True, show_photo=True):
    if not include_agent or not agent:
        return None
    name = _clean(agent.get("name"))
    title = _clean(agent.get("title") or "Asesor Inmobiliario")
    bundled = agent.get("agent_contact") if isinstance(agent.get("agent_contact"), dict) else {}
    whatsapp = _clean(
        bundled.get("whatsapp") if "whatsapp" in bundled else agent.get("whatsapp")
    )
    if agent.get("whatsapp_enabled") is False:
        whatsapp = ""
    instagram = _clean(
        bundled.get("instagram") if "instagram" in bundled else agent.get("instagram")
    )
    if agent.get("instagram_enabled") is False:
        instagram = ""
    email = _clean(bundled.get("email") if "email" in bundled else agent.get("email"))
    photo = ""
    if show_photo and agent.get("photo_path"):
        image = load_agent_photo(agent.get("photo_path"))
        if image is not None:
            photo = _data_uri_from_image(image.convert("RGBA"), fmt="PNG", keep_alpha=True)
    contacts = []
    wa_href = whatsapp_url(whatsapp)
    wa_display = format_whatsapp_display(whatsapp) if wa_href else ""
    if wa_href and wa_display:
        contacts.append(
            {
                "kind": "whatsapp",
                "href": wa_href,
                "label": "WhatsApp",
                "value": wa_display,
                "icon": ICON_WA,
            }
        )
    ig_href = instagram_profile_url(instagram)
    handle = format_instagram_handle(instagram) if ig_href else ""
    if ig_href and handle:
        contacts.append(
            {
                "kind": "instagram",
                "href": ig_href,
                "label": "Instagram",
                "value": handle,
                "icon": ICON_IG,
            }
        )
    mail_href = email_mailto(email)
    if mail_href:
        contacts.append(
            {
                "kind": "email",
                "href": mail_href,
                "label": "Email",
                "value": mail_href[len("mailto:"):],
                "icon": ICON_EMAIL,
            }
        )
    if not any((name, photo, contacts)):
        return None
    return {
        "name": name,
        "title": title if name else "",
        "photo": photo,
        "whatsapp": bool(wa_href),
        "instagram": bool(ig_href),
        "email": bool(mail_href),
        "instagram_handle": handle,
        "contacts": contacts,
        "loaded": True,
    }


def _photo_payload(row, payload, meta, frame_size, *, hero=False):
    width = int((meta or {}).get("width") or 0)
    height = int((meta or {}).get("height") or 0)
    mime = (meta or {}).get("mime") or "image/jpeg"
    src = _data_uri_from_bytes(payload, mime) if payload else ""
    data_uri_sha256 = _sha256(base64.b64decode(src.split(",", 1)[1])) if src else ""
    audit = dict((meta or {}).get("audit") or {})
    audit["data_uri_sha256"] = data_uri_sha256
    audit["match"] = bool(audit.get("match")) and data_uri_sha256 == audit.get("loaded_sha256")
    plan = plan_original_photo((width, height), frame_size)
    variant = source_variant(row)
    logger.info(
        "[PROPERTY_MARKETING_PHOTO_FIT] id=%s source=%sx%s max_available=%sx%s fit=original crop=0 scale_factor=%.3f display=%sx%s visual_transform=%s blur=false background_from_photo=false",
        (row or {}).get("id"),
        width,
        height,
        frame_size[0],
        frame_size[1],
        plan["scale_factor"],
        plan["display_width"],
        plan["display_height"],
        plan["visual_transform"],
    )
    if hero:
        logger.info(
            "[PROPERTY_MARKETING_HERO_SOURCE] source_path=%s source_url=%s original=%sx%s byte_size=%s sha256=%s loaded=%sx%s loaded_sha256=%s data_uri_sha256=%s match=%s",
            audit.get("source_path"),
            audit.get("source_url"),
            audit.get("original_width"),
            audit.get("original_height"),
            audit.get("byte_size"),
            audit.get("sha256"),
            audit.get("loaded_width"),
            audit.get("loaded_height"),
            audit.get("loaded_sha256"),
            data_uri_sha256,
            str(audit["match"]).lower(),
        )
        logger.info(
            "[PROPERTY_MARKETING_HERO_QUALITY] original_dimensions=%sx%s rendered_dimensions=%sx%s scale_factor=%.3f crop_percent=0.0 visual_transform=%s source_variant=%s recompressed=false",
            width,
            height,
            plan["display_width"],
            plan["display_height"],
            plan["scale_factor"],
            plan["visual_transform"],
            variant,
        )
    return {
        "src": src,
        "id": (row or {}).get("id"),
        "fit": plan["fit"],
        "crop": 0.0,
        "crop_percent": 0.0,
        "scale_factor": plan["scale_factor"],
        "display_width": plan["display_width"],
        "display_height": plan["display_height"],
        "visual_transform": plan["visual_transform"],
        "max_width": int(frame_size[0]),
        "max_height": int(frame_size[1]),
        "upscale": False,
        "source_audit": audit,
        "width": width,
        "height": height,
        "bytes": (meta or {}).get("bytes") or (len(payload) if payload else 0),
        "mime": mime,
        "source_variant": variant,
        "is_original": photo_source_type(row) in ORIGINAL_SOURCE_TYPES,
        "recompressed": False,
    }


DEMO_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", ".test", ".invalid", "mock")
QA_FIXTURE_PATH_MARKERS = ("property_marketing_qa", "_qa_", "\\tmp\\", "/tmp/", "fixtures")


def _contact_value(agent, kind):
    for item in (agent or {}).get("contacts") or []:
        if item.get("kind") == kind:
            return item.get("value") or ""
    return ""


def _provenance(facts, hero, secondaries, agent, raw_agent, broker, logo_source):
    """Where each rendered datum came from, plus every fallback/demo marker found."""
    fallbacks = []
    demo = []
    photos = [hero] + list(secondaries or [])
    for photo in photos:
        audit = photo.get("source_audit") or {}
        location = f"{audit.get('source_path') or ''} {audit.get('source_url') or ''}".casefold()
        if any(marker in location for marker in QA_FIXTURE_PATH_MARKERS):
            demo.append(f"photo_qa_fixture:{photo.get('id')}")
        if not photo.get("is_original"):
            fallbacks.append(f"photo_not_original:{photo.get('id')}:{photo.get('source_variant')}")
        if not audit.get("match"):
            fallbacks.append(f"photo_bytes_mismatch:{photo.get('id')}")
    email = _contact_value(agent, "email")
    if email and any(email.casefold().endswith(domain) or domain in email.casefold() for domain in DEMO_EMAIL_DOMAINS):
        demo.append(f"agent_email:{email}")
    agent_name = _clean((agent or {}).get("name")).casefold()
    if any(token in agent_name for token in ("mock", "demo", "test")):
        demo.append(f"agent_name:{agent_name}")
    if agent and not agent.get("photo"):
        fallbacks.append("agent_photo_missing")
    if not agent:
        fallbacks.append("agent_missing")
    if facts.get("used_demo_fallback"):
        demo.append("branding_used_demo_fallback")
    if _is_demo_broker(facts):
        demo.append("broker_demo")
    if not broker.get("loaded"):
        fallbacks.append("broker_missing")
    if logo_source != "organization":
        fallbacks.append(f"logo:{logo_source}")
    for key in ("property_id", "organization_id"):
        if not facts.get(key):
            fallbacks.append(f"{key}_missing")
    audit = hero.get("source_audit") or {}
    return {
        "property_id": facts.get("property_id"),
        "organization_id": facts.get("organization_id"),
        "photo_source": audit.get("source_url") or audit.get("source_path") or "",
        "photo_variant": hero.get("source_variant"),
        "photo_is_original": bool(hero.get("is_original")),
        "photo_dimensions": f"{hero.get('width')}x{hero.get('height')}",
        "photo_sha256": audit.get("sha256"),
        "photos_rendered": len(photos),
        "agent": _clean((agent or {}).get("name")),
        "agent_photo": bool((agent or {}).get("photo")),
        "agent_photo_path": _clean((raw_agent or {}).get("photo_path")),
        "whatsapp": _contact_value(agent, "whatsapp"),
        "instagram": _contact_value(agent, "instagram"),
        "email": email,
        "broker": broker.get("line") or "",
        "logo_source": logo_source,
        "fallbacks": fallbacks,
        "demo_data": demo,
    }


def _log_generation(view):
    info = view.get("provenance") or {}
    logger.info(
        "[PROPERTY_MARKETING] property_id=%s organization_id=%s template=%s photo_source=%s photo_dimensions=%s agent=%s whatsapp=%s instagram=%s email=%s broker=%s renderer=%s",
        info.get("property_id"),
        info.get("organization_id"),
        view.get("template_id"),
        info.get("photo_source"),
        info.get("photo_dimensions"),
        info.get("agent"),
        info.get("whatsapp"),
        info.get("instagram"),
        info.get("email"),
        info.get("broker"),
        HTML_RENDERER,
    )
    logger.info(
        "[PROPERTY_MARKETING_PROVENANCE] template=%s photo_variant=%s photo_is_original=%s photos_rendered=%s agent_photo=%s logo_source=%s fallbacks=%s demo_data=%s",
        view.get("template_id"),
        info.get("photo_variant"),
        str(bool(info.get("photo_is_original"))).lower(),
        info.get("photos_rendered"),
        str(bool(info.get("agent_photo"))).lower(),
        info.get("logo_source"),
        ",".join(info.get("fallbacks") or []) or "none",
        ",".join(info.get("demo_data") or []) or "none",
    )


CTA_BUTTON_MAX_CHARS = 22
CTA_BUTTON_FALLBACK = {"es": "Coordiná tu visita", "en": "Book a visit"}


def normalize_button_cta(text, language="es"):
    """Button label only: free marketing copy can be longer, the button cannot."""
    label = _clean(text)
    if label and len(label) <= CTA_BUTTON_MAX_CHARS:
        return label
    fallback = CTA_BUTTON_FALLBACK.get(language) or CTA_BUTTON_FALLBACK["es"]
    if label:
        logger.info(
            "[PROPERTY_MARKETING_CTA] normalized length=%s max=%s fallback=%s",
            len(label),
            CTA_BUTTON_MAX_CHARS,
            fallback,
        )
    return fallback


def _contact_text_scale(contacts, template_id):
    longest = max((len(item.get("value") or "") for item in contacts or []), default=0)
    capacity = CONTACT_TEXT_CAPACITY.get(template_id, 24)
    if longest <= capacity:
        return 1.0
    return round(max(0.72, capacity / float(longest)), 3)


def _quote_lines(quote):
    words = _clean(quote).split(" ")
    if len(words) < 4:
        return [" ".join(words)] if words and words[0] else []
    return words[:-2] + [" ".join(words[-2:])]


def _legal_parts(broker):
    line = _clean((broker or {}).get("line"))
    if ": " in line:
        label, rest = line.split(": ", 1)
        return {"label": f"{label}:", "rest": rest}
    return {"label": "", "rest": line}


def select_property_template(requested, photos, facts=None):
    del facts
    name = str(requested or "").strip() or TEMPLATE_AUTOMATIC
    if name in PROPERTY_TEMPLATES:
        return name
    ranked = select_photos_for_item(photos, fmt="post", index=0, limit=4)
    hero = ranked[0] if ranked else None
    scenes = unique_scene_keys(ranked)
    if len(scenes) >= 3:
        return TEMPLATE_CLEAN_GRID
    if hero and is_powerful_hero(hero) and len(scenes) <= 1:
        return TEMPLATE_PREMIUM_HERO
    if hero and is_lifestyle_strong(hero) and len(scenes) <= 2:
        return TEMPLATE_LIFESTYLE_DARK
    return TEMPLATE_CLEAN_GRID


def resolve_property_template(options=None):
    """Requested design before photos are known: "automatic" or a property_* id."""
    options = options or {}
    return normalize_property_design(
        options.get("design_requested")
        or options.get("layout_template")
        or options.get("template")
        or options.get("template_used")
    )


def _log_media(property_id, organization_id, records, hero, secondaries):
    logger.info(
        "[PROPERTY_MARKETING] property_id=%s organization_id=%s photos_found=%s photos_valid=%s hero_photo=%s secondaries=%s",
        property_id,
        organization_id,
        len(records or []),
        sum(1 for item in records or [] if item.get("url") or item.get("original_url") or item.get("path")),
        (hero or {}).get("id"),
        [item.get("id") for item in secondaries or []],
    )


def _box(left, top, photo):
    return {
        "left": round(left, 2),
        "top": round(top, 2),
        "width": photo["display_width"],
        "height": photo["display_height"],
    }


def _photo_layout(template_id, master, hero, secondaries, hero_max):
    """Boxes are the rendered photo size; content below the photo moves by `shift` px."""
    canvas_w, canvas_h = master
    hero_w = hero["display_width"]
    hero_h = hero["display_height"]
    thumbs = []
    hero_shift = 0.0
    card_height = None
    if template_id == TEMPLATE_CLEAN_GRID:
        hero_shift = hero_h - CLEAN_GRID_PHOTO_AREA
        card_height = CLEAN_GRID_CARD_HEIGHT + hero_shift
        top = CLEAN_GRID_CARD_TOP
        column_w = CLEAN_GRID_THUMB_MAX[0]
        for photo in secondaries:
            left = CLEAN_GRID_THUMB_LEFT + (column_w - photo["display_width"]) / 2.0
            thumbs.append(_box(left, top, photo))
            top += photo["display_height"] + CLEAN_GRID_THUMB_GAP
        column_h = top - CLEAN_GRID_THUMB_GAP - CLEAN_GRID_CARD_TOP if thumbs else 0
        card_height = max(card_height, column_h)
        shift = card_height - CLEAN_GRID_CARD_HEIGHT
        hero_box = _box((hero_max[0] - hero_w) / 2.0, 0, hero)
    elif template_id == TEMPLATE_PREMIUM_HERO:
        band = PREMIUM_BELOW_TOP - PHOTO_GAP - PREMIUM_PHOTO_TOP
        shift = max(0.0, hero_h - band)
        top = PREMIUM_PHOTO_TOP + max(0.0, (band - hero_h) / 2.0)
        hero_box = _box((canvas_w - hero_w) / 2.0, top, hero)
    else:
        shift = LIFESTYLE_PHOTO_TOP + hero_h + PHOTO_GAP - LIFESTYLE_BELOW_TOP
        hero_box = _box((canvas_w - hero_w) / 2.0, LIFESTYLE_PHOTO_TOP, hero)
    return {
        "hero": hero_box,
        "thumbs": thumbs,
        "hero_shift": round(hero_shift, 2),
        "card_height": round(card_height, 2) if card_height is not None else None,
        "shift": round(shift, 2),
        "height": int(round(canvas_h + shift)),
    }


def build_property_marketing_view(context, *, fmt="post", options=None, art=None, language="es"):
    options = options or {}
    art = art or {}
    facts = (context or {}).get("facts") or {}
    language = language or "es"
    include_agent = options.get("include_agent") is not False
    show_price = options.get("show_price") is not False
    requested = resolve_property_template(options)
    photo_rows = list((context or {}).get("photos") or [])
    loaded = _load_listing_photos(photo_rows)
    if not loaded:
        raise PropertyMarketingError("marketing_ia_visual_no_photos", 400)
    loaded_rows = [row for row, _payload, _meta in loaded]
    selected_rows = select_photos_for_item(loaded_rows, fmt="post", index=0, limit=4)
    by_key = {photo_key(row): (row, payload, meta) for row, payload, meta in loaded}
    triples = []
    for row in selected_rows:
        item = by_key.get(photo_key(row))
        if item:
            triples.append(item)
    if not triples:
        triples = loaded[:4]
    template_id = select_property_template(requested, [row for row, _payload, _meta in triples], facts)
    hero_row, hero_payload, hero_meta = triples[0]
    secondaries = []
    if template_id == TEMPLATE_CLEAN_GRID:
        for row, payload, meta in triples[1:4]:
            secondaries.append(_photo_payload(row, payload, meta, CLEAN_GRID_THUMB_MAX))
    hero_max = HERO_PHOTO_MAX[template_id]
    if template_id == TEMPLATE_CLEAN_GRID and not secondaries:
        hero_max = CLEAN_GRID_HERO_WIDE_MAX
    hero = _photo_payload(hero_row, hero_payload, hero_meta, hero_max, hero=True)
    layout = _photo_layout(template_id, MASTER_SIZES[template_id], hero, secondaries, hero_max)
    canvas = (MASTER_SIZES[template_id][0], layout["height"])
    logger.info(
        "[PROPERTY_MARKETING_LAYOUT] template=%s hero_box=%s thumbs=%s shift=%s canvas=%sx%s",
        template_id,
        layout["hero"],
        layout["thumbs"],
        layout["shift"],
        canvas[0],
        canvas[1],
    )
    logger.info(
        "[PROPERTY_MARKETING_PHOTO_SELECT] property_id=%s hero_id=%s hero_scene=%s hero_score=%s powerful=%s lifestyle=%s secondaries=%s scenes=%s",
        facts.get("property_id"),
        hero_row.get("id"),
        photo_scene_label(hero_row) or "",
        score_listing_photo(hero_row, hero=True),
        str(is_powerful_hero(hero_row)).lower(),
        str(is_lifestyle_strong(hero_row)).lower(),
        [item.get("id") for item in secondaries],
        unique_scene_keys([hero_row] + [row for row, _payload, _meta in triples[1:4]]),
    )
    agent = _agent_view(
        (context or {}).get("agent"),
        include_agent=include_agent,
        show_photo=options.get("show_agent_photo", True),
    )
    if agent:
        agent["contact_scale"] = _contact_text_scale(agent.get("contacts"), template_id)
    planned = summarize_listing_copy(
        facts,
        (context or {}).get("agent") if include_agent else None,
        headline=art.get("headline") or options.get("headline") or "",
        cta=art.get("cta") or options.get("cta") or "",
        language=language,
        style=options.get("style") or "commercial",
    )
    if _clean(art.get("subheadline")):
        planned["subheadline"] = _clean(art.get("subheadline"))
    if template_id == TEMPLATE_CLEAN_GRID:
        fallback_cta = "Escribime ahora" if language == "es" else "Message me now"
    else:
        fallback_cta = "Coordiná tu visita" if language == "es" else "Book a visit"
    cta = normalize_button_cta(
        _clean(planned.get("cta") or default_cta(language, facts) or fallback_cta), language
    )
    if template_id != TEMPLATE_CLEAN_GRID:
        cta = cta.upper()
    broker = _broker_view(facts)
    mark, submark = _split_brand(facts.get("brand_name") or facts.get("office_name"))
    logo_path, logo_source = _office_logo_path(facts)
    logo_uri = _logo_uri(logo_path) if logo_path else ""
    if logo_path and not logo_uri:
        logo_source = "none"
    _log_media(
        facts.get("property_id"),
        facts.get("organization_id"),
        photo_rows,
        hero_row,
        [row for row, _payload, _meta in triples[1:4]],
    )
    logger.info(
        "[PROPERTY_MARKETING_SELECT] property_id=%s organization_id=%s template_requested=%s template_selected=%s photos_found=%s photos_valid=%s hero_photo=%s hero_src=%s renderer=%s broker_loaded=%s agent_loaded=%s",
        facts.get("property_id"),
        facts.get("organization_id"),
        requested,
        template_id,
        len(photo_rows),
        len(triples),
        hero_row.get("id"),
        _src_kind(hero.get("src")),
        HTML_RENDERER,
        str(broker.get("loaded")).lower(),
        str(bool(agent)).lower(),
    )
    quote = "Tu próxima historia empieza acá" if language == "es" else "Your next chapter starts here"
    return {
        "language": language,
        "template_id": template_id,
        "canvas": {"width": canvas[0], "height": canvas[1]},
        "canvas_size": canvas,
        "operation": _operation_label(facts, language),
        "address": _address_line(facts),
        "zone": _zone_line(facts),
        "copy": _copy_line(facts, planned, language),
        "quote": quote,
        "quote_lines": _quote_lines(quote),
        "legal": _legal_parts(broker),
        "aside": "Ciudades · Personas · Historias" if language == "es" else "Cities · People · Stories",
        "price": _price_view(facts, show_price=show_price),
        "facts": _facts_row(facts, language),
        "hero": hero,
        "secondary": secondaries,
        "layout": layout,
        "gallery_count": len(secondaries),
        "agent": agent,
        "cta": cta,
        "branding": {
            "logo": logo_uri,
            "mark": mark,
            "submark": submark,
            "name": _clean(facts.get("brand_name") or facts.get("office_name")),
            "tagline": "Viví · Invertí · Disfrutá" if language == "es" else "Live · Invest · Enjoy",
        },
        "broker": broker,
        "provenance": _provenance(
            facts, hero, secondaries, agent, (context or {}).get("agent"), broker, logo_source
        ),
        "icons": {"whatsapp": ICON_WA, "instagram": ICON_IG, "email": ICON_EMAIL},
        "css": _read_css(template_id),
        "font_css": _font_face_css(EDITORIAL_FONT_MAPPING),
        "modifiers": " ".join(
            part
            for part in (
                f"poster--gallery-{len(secondaries)}" if template_id == TEMPLATE_CLEAN_GRID else "",
                f"poster--fit-{hero.get('fit') or 'cover'}",
                "poster--no-agent" if not agent else "",
                "poster--no-agent-photo" if agent and not agent.get("photo") else "",
                "poster--no-price" if not _price_view(facts, show_price=show_price).get("amount") else "",
                "poster--no-broker" if not broker.get("loaded") else "",
            )
            if part
        ),
        "template_used": template_id,
        "renderer_used": HTML_RENDERER,
    }


def render_property_marketing_html(view):
    template = _jinja().get_template(TEMPLATE_FILES[view["template_id"]])
    return template.render(**view)


def _src_kind(uri):
    text = str(uri or "")
    if text.startswith("data:"):
        cut = text.find(";")
        return text[: cut if cut > 0 else 32]
    if text.startswith(("http://", "https://")):
        return "https"
    if text.startswith("file:"):
        return "file"
    if text:
        return "path"
    return "empty"


def _assert_poster_images_loaded(view, metrics):
    images = list((metrics or {}).get("images") or [])
    for item in images:
        logger.info(
            "[PROPERTY_MARKETING_IMAGE_LOAD] src=%s loaded=%s natural_width=%s natural_height=%s class=%s",
            item.get("src_kind"),
            str(bool(item.get("loaded"))).lower(),
            item.get("natural_width"),
            item.get("natural_height"),
            item.get("className"),
        )
    required = []
    if (view.get("hero") or {}).get("src"):
        required.append("hero__photo")
    if (view.get("branding") or {}).get("logo"):
        required.append("brand__logo")
    for class_name in required:
        matches = [item for item in images if class_name in str(item.get("className") or "")]
        if not matches or not all(item.get("loaded") for item in matches):
            logger.error(
                "PROPERTY_MARKETING_RENDER_FAILED required image missing class=%s loaded=%s",
                class_name,
                [item.get("loaded") for item in matches],
            )
            raise PropertyMarketingError("PROPERTY_MARKETING_RENDER_FAILED", 500)


def render_property_marketing(context, fmt="post", *, options=None, art=None, language="es"):
    started = time.perf_counter()
    options = options or {}
    facts = (context or {}).get("facts") or {}
    try:
        view = build_property_marketing_view(
            context,
            fmt=fmt,
            options=options,
            art=art,
            language=language,
        )
        html = render_property_marketing_html(view)
        expected = tuple(view["canvas_size"])
        png_bytes, metrics = screenshot_poster(html, size=expected)
        _assert_poster_images_loaded(view, metrics)
        image = Image.open(io.BytesIO(png_bytes))
        if image.size != expected:
            raise PropertyMarketingError("marketing_err_html_render_failed", 500)
        logger.info(
            "[PROPERTY_MARKETING_PNG] template=%s size=%sx%s expected=%sx%s canvas=master resize=false",
            view["template_id"],
            image.size[0],
            image.size[1],
            expected[0],
            expected[1],
        )
        _log_generation(view)
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "[PROPERTY_MARKETING_RENDER_OK] template=%s renderer=%s output=png duration_ms=%s",
            view["template_id"],
            HTML_RENDERER,
            duration_ms,
        )
        return {
            "png_bytes": png_bytes,
            "html": html,
            "metrics": metrics,
            "render_context": view,
            "agent_photo_composited": bool(view.get("agent") and view["agent"].get("photo")),
            "logo_stamped": bool(view.get("branding", {}).get("logo")),
            "post_process": "html_playwright",
            "post_process_fn": "modules.property_marketing.render_property_marketing",
            "layout": view["template_id"],
            "template_used": view["template_id"],
            "renderer_used": HTML_RENDERER,
            "layout_version": view["template_id"],
            "listing_photos": 1 + len(view.get("secondary") or []),
            "provenance": view.get("provenance") or {},
        }
    except MarketingError:
        logger.exception(
            "PROPERTY_MARKETING_RENDER_FAILED template=%s property_id=%s organization_id=%s",
            resolve_property_template(options),
            facts.get("property_id"),
            facts.get("organization_id"),
        )
        raise
    except Exception as exc:
        logger.exception(
            "PROPERTY_MARKETING_RENDER_FAILED template=%s property_id=%s organization_id=%s error=%s",
            resolve_property_template(options),
            facts.get("property_id"),
            facts.get("organization_id"),
            type(exc).__name__,
        )
        raise PropertyMarketingError("PROPERTY_MARKETING_RENDER_FAILED", 500) from exc


def property_media_inventory(property_id, organization_id):
    rows = list_property_media(organization_id, property_id) if organization_id and property_id else []
    records = get_property_original_media(property_id, organization_id)
    valid = []
    for record in records:
        item = record.get("media") or record
        url = get_property_media_url(item, property_id) or record.get("url") or record.get("original_url")
        if url or record.get("path"):
            valid.append({**record, "url": url, "source_type": photo_source_type(item)})
    return {
        "property_id": property_id,
        "organization_id": organization_id,
        "photos_found": len(rows),
        "photos_valid": len(valid),
        "records": valid,
    }
