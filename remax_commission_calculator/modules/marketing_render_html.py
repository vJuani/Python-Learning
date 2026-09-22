"""HTML/CSS → Playwright Chromium → PNG renderer for Marketing IA.

Isolated 1080×1350 document. No app chrome. No silent Pillow fallback.
"""

from __future__ import annotations

import atexit
import base64
import io
import json
import logging
import os
import re
import threading
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from modules.config import BASE_DIR, is_development
from modules.marketing_context import MarketingError
from modules.marketing_copy import summarize_listing_copy
from modules.marketing_flyer_commercial import _split_price
from modules.marketing_language import (
    default_cta,
    default_kicker,
    is_placeholder_copy,
    listing_benefit_line,
    marketing_zone_line,
)
from modules.listing_photo_origin import (
    eligible_listing_photos,
    is_generated_marketing_asset,
    photo_origin_log,
)
from modules.marketing_photo_selector import photo_scene_label, select_photos_for_item
from modules.marketing_renderer import (
    FORMAT_SIZES,
    _office_logo,
    fit_cover,
    load_agent_photo,
    load_property_photo_pairs,
)
from modules.property_sync.media import get_property_original_media

logger = logging.getLogger(__name__)

HTML_TEMPLATE = "modern_commercial_v3"
HTML_RENDERER = "html_playwright"
HTML_FORMATS = frozenset({"post"})
POSTER_SIZE = FORMAT_SIZES["post"]
TEMPLATE_FILE = "modern_commercial_v3.html"
CSS_RELATIVE = Path("static") / "css" / "marketing-render" / "modern_commercial_v3.css"
DEBUG_DIR = BASE_DIR / "tmp" / "marketing_html_render"

SCENE_CAPTIONS = {
    "living": ("LIVING", "Luz y amplitud en cada espacio"),
    "cocina": ("COCINA", "Diseño y funcionalidad"),
    "jardin": ("JARDÍN", "Ideal para disfrutar en familia"),
    "fachada": ("FACHADA", "Primera impresión"),
    "dormitorio": ("DORMITORIO", "Descanso y confort"),
    "bano": ("BAÑO", ""),
    "balcon": ("BALCÓN", ""),
}

ICON_ROOMS = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<rect x="4" y="13" width="16" height="4.2" rx="0.8"/>'
    '<path d="M7 13V9.2h10V13"/>'
    '<path d="M5 9.2V8h3M16 8h3v1.2"/>'
    '<path d="M6.5 17.2V19.5M17.5 17.2V19.5"/></svg>'
)
ICON_BED = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M4 17V12h16v5"/>'
    '<path d="M4 14.2h16"/>'
    '<path d="M7 12V8.8h6.5V12"/>'
    '<path d="M3 17h18"/></svg>'
)
ICON_BATH = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M16.5 4.5v5"/>'
    '<path d="M15 6.2h3M14.2 8h4.6"/>'
    '<path d="M5 13.5h14v2.8A2.7 2.7 0 0 1 16.3 19H7.7A2.7 2.7 0 0 1 5 16.3v-2.8z"/>'
    '<path d="M4 20.2h16"/></svg>'
)
ICON_AREA = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M4.5 11.2L12 5.2l7.5 6V19.5h-15z"/>'
    '<path d="M9.5 19.5v-6h5v6"/></svg>'
)
ICON_PIN = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z"/>'
    '<circle cx="12" cy="10" r="2.4"/></svg>'
)
ICON_TREE = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21v-6"/><path d="M7 15c-2-3 0-7 5-8 5 1 7 5 5 8z"/>'
    '<path d="M9 11c-1-3 1-6 3-7 2 1 4 4 3 7"/></svg>'
)
ICON_SCHOOL = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10l9-5 9 5-9 5-9-5z"/>'
    '<path d="M7 12v5l5 3 5-3v-5"/></svg>'
)
ICON_WIFI = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12a10 10 0 0 1 14 0"/>'
    '<path d="M8.5 15a6 6 0 0 1 7 0"/><circle cx="12" cy="18.5" r="1.2" fill="currentColor" stroke="none"/></svg>'
)
ICON_WA = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#25D366" stroke="none" d="M12 2a10 10 0 0 0-8.5 15.3L2 22l4.9-1.4A10 10 0 1 0 12 2z"/>'
    '<path fill="#fff" stroke="none" d="M16.6 14.3c-.2-.1-1.3-.6-1.5-.7s-.4-.1-.5.1-.6.7-.7.9-.3.2-.5.1a6.5 6.5 0 0 1-1.9-1.2 7.2 7.2 0 0 1-1.3-1.6c-.1-.2 0-.4.1-.5l.4-.4.2-.3c.1-.1 0-.3 0-.4l-.7-1.7c-.2-.4-.4-.4-.5-.4h-.4c-.1 0-.4.1-.6.3s-.8.8-.8 1.9.8 2.2.9 2.3a10.5 10.5 0 0 0 4 3.4c1.5.6 1.8.5 2.2.4s1.3-.5 1.4-1 .2-.9.1-1z"/></svg>'
)
ICON_WA_LIGHT = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#25D366" stroke="none" d="M12 2a10 10 0 0 0-8.5 15.3L2 22l4.9-1.4A10 10 0 1 0 12 2z"/>'
    '<path fill="#fff" stroke="none" d="M16.6 14.3c-.2-.1-1.3-.6-1.5-.7s-.4-.1-.5.1-.6.7-.7.9-.3.2-.5.1a6.5 6.5 0 0 1-1.9-1.2 7.2 7.2 0 0 1-1.3-1.6c-.1-.2 0-.4.1-.5l.4-.4.2-.3c.1-.1 0-.3 0-.4l-.7-1.7c-.2-.4-.4-.4-.5-.4h-.4c-.1 0-.4.1-.6.3s-.8.8-.8 1.9.8 2.2.9 2.3a10.5 10.5 0 0 0 4 3.4c1.5.6 1.8.5 2.2.4s1.3-.5 1.4-1 .2-.9.1-1z"/></svg>'
)
ICON_IG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="17" height="17" rx="5" fill="none" stroke="#c13584" stroke-width="1.8"/>'
    '<circle cx="12" cy="12" r="4" fill="none" stroke="#c13584" stroke-width="1.8"/>'
    '<circle cx="17.2" cy="6.8" r="1" fill="#c13584" stroke="none"/></svg>'
)
ICON_EMAIL = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="#1a2a40" stroke-width="1.7"/>'
    '<path d="M4 7l8 6 8-6" fill="none" stroke="#1a2a40" stroke-width="1.7"/></svg>'
)

_FONT_FILES = {
    "sans": (
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ),
    "sans-bold": (
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ),
    "serif-italic": (
        Path(r"C:\Windows\Fonts\georgiai.ttf"),
        Path(r"C:\Windows\Fonts\timesi.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"),
    ),
}

_lock = threading.Lock()
_playwright = None
_browser = None
_jinja_env = None


def uses_html_renderer(fmt, options=None):
    del options
    return str(fmt or "").strip().lower() in HTML_FORMATS


def resolve_html_layout_template(fmt, options=None):
    if uses_html_renderer(fmt, options):
        return HTML_TEMPLATE
    from modules.marketing_flyer_modern import resolve_layout_template

    return resolve_layout_template(fmt, options)


def select_html_render_photos(photos, *, secondary=3):
    """Hero + diverse secondaries. Ready to swap in an IA selector later."""
    chosen = select_photos_for_item(photos, fmt="post", index=0, limit=1 + max(0, secondary))
    if not chosen:
        return {"hero": None, "secondary": []}
    return {"hero": chosen[0], "secondary": chosen[1:]}


def _package_root():
    return Path(__file__).resolve().parent.parent


def _jinja():
    global _jinja_env
    if _jinja_env is None:
        templates = _package_root() / "templates" / "marketing" / "render"
        _jinja_env = Environment(
            loader=FileSystemLoader(str(templates)),
            autoescape=select_autoescape(["html"]),
        )
    return _jinja_env


def _read_css():
    path = _package_root() / CSS_RELATIVE
    return path.read_text(encoding="utf-8")


def _font_face_css():
    rules = []
    mapping = (
        ("Marketing Sans", "sans", "normal", 400),
        ("Marketing Sans", "sans-bold", "normal", 700),
        ("Marketing Sans", "sans-bold", "normal", 800),
        ("Marketing Serif", "serif-italic", "italic", 600),
    )
    for family, key, style, weight in mapping:
        path = next((item for item in _FONT_FILES[key] if item.is_file()), None)
        if path is None:
            continue
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        ext = path.suffix.lower().lstrip(".") or "ttf"
        fmt = "truetype" if ext in {"ttf", "otf"} else ext
        rules.append(
            f"@font-face{{font-family:'{family}';src:url(data:font/{ext};base64,{payload}) "
            f"format('{fmt}');font-style:{style};font-weight:{weight};font-display:block;}}"
        )
    return "\n".join(rules)


def _qa_mode():
    return os.environ.get("MARKETING_HTML_QA", "").strip().lower() in {"1", "true", "yes", "on"}


def _flat_placeholder(image):
    if image is None:
        return True
    sample = image.convert("RGBA").resize((48, 48))
    colors = sample.getcolors(maxcolors=64)
    return bool(colors is not None and len(colors) <= 14)


def _qa_asset_status(name, image, *, required=True):
    if image is None:
        status = "failed" if required else "missing"
    elif _flat_placeholder(image):
        status = "failed"
    else:
        status = "ok"
    logger.info("%s_asset_status=%s", name, status)
    return status


def _data_uri_from_image(image, *, fmt="JPEG", quality=88, keep_alpha=False):
    buffer = io.BytesIO()
    if keep_alpha or fmt == "PNG":
        image.save(buffer, format="PNG", optimize=True)
        mime = "image/png"
    else:
        image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def _path_data_uri(path, *, keep_alpha=False, cover=None):
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    image = Image.open(file_path)
    image.load()
    if cover:
        image = fit_cover(image, cover[0], cover[1])
        return _data_uri_from_image(image, fmt="JPEG", keep_alpha=False)
    if keep_alpha or image.mode in {"RGBA", "LA"}:
        return _data_uri_from_image(image.convert("RGBA"), fmt="PNG", keep_alpha=True)
    return _data_uri_from_image(image.convert("RGB"), fmt="JPEG")


def _photo_uri(image, size):
    if image is None:
        return ""
    cropped = fit_cover(image, size[0], size[1])
    return _data_uri_from_image(cropped, fmt="JPEG")


def _hero_uri(image):
    if image is None:
        return ""
    rgb = image.convert("RGB")
    longest = max(rgb.size)
    if longest > 1800:
        scale = 1800 / float(longest)
        rgb = rgb.resize(
            (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return _data_uri_from_image(rgb, fmt="JPEG", quality=90)


UNVERIFIED_CLAIM_RE = re.compile(
    r"cerca de colegios|excelente conectividad|entorno residencial|"
    r"comercios y servicios|close to schools|excellent connectivity|"
    r"quiet residential",
    re.I,
)


def _parse_focus(raw, default=(58.0, 52.0)):
    x, y = default
    try:
        if isinstance(raw, dict):
            x = float(raw.get("x", x))
            y = float(raw.get("y", y))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            x = float(raw[0])
            y = float(raw[1])
        elif isinstance(raw, str) and "," in raw:
            left, right = raw.split(",", 1)
            x = float(left.strip())
            y = float(right.strip())
    except (TypeError, ValueError):
        return default
    return (max(0.0, min(100.0, x)), max(0.0, min(100.0, y)))


def _hero_focus(hero_row, options):
    options = options or {}
    row = hero_row or {}
    return _parse_focus(
        options.get("hero_focus")
        or row.get("focal_point")
        or row.get("focus")
        or ((options.get("photo_focus") or {}).get("hero") if isinstance(options.get("photo_focus"), dict) else None)
    )


def _too_similar(left, right, *, threshold=26):
    if left is None or right is None:
        return False
    sample_a = left.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
    sample_b = right.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
    pixels_a = list(sample_a.getdata())
    pixels_b = list(sample_b.getdata())
    acc = 0.0
    for pa, pb in zip(pixels_a, pixels_b):
        acc += abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2])
    return (acc / (32 * 32 * 3)) < threshold


def _format_int(value):
    if value in (None, ""):
        return ""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,}".replace(",", ".")


def _headline_lines(facts, planned, language):
    zone = " ".join(str(facts.get("locality") or facts.get("neighborhood") or "").split())
    raw = " ".join(str((planned or {}).get("headline") or "").split())
    if raw and not is_placeholder_copy(raw):
        connector = " en " if language == "es" else " in "
        if zone and raw.casefold().endswith(zone.casefold()):
            left = raw[: -len(zone)].rstrip()
            if left.casefold().endswith(connector.strip()):
                left = left[: -len(connector.strip())].rstrip()
            lead = [part for part in _wrap_two(left) if part]
            return (lead + [zone])[:3] if zone else lead[:3]
        return _wrap_three(raw)
    rental = str(facts.get("purpose") or facts.get("operation_type") or "").lower() in {
        "rental",
        "rent",
        "alquiler",
    }
    if language != "es":
        lead = ["Live comfortably", "in"] if rental else ["Live the space", "and comfort in"]
        return (lead + ([zone] if zone else []))[:3]
    lead = ["Viví cómodo", "y bien ubicado en"] if rental else ["Viví la amplitud", "y el confort en"]
    if zone:
        lead.append(zone)
    return lead[:3]


def _wrap_two(text):
    words = [part for part in str(text or "").split() if part]
    if len(words) <= 3:
        return [" ".join(words)] if words else []
    mid = max(1, len(words) // 2)
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _wrap_three(text):
    words = [part for part in str(text or "").split() if part]
    if len(words) <= 3:
        return [" ".join(words)] if words else []
    if len(words) <= 6:
        return _wrap_two(text)
    n = len(words)
    a = max(1, n // 3)
    b = max(a + 1, (2 * n) // 3)
    return [" ".join(words[:a]), " ".join(words[a:b]), " ".join(words[b:])]


def _headline_size(lines):
    chars = max((len(line) for line in lines), default=0)
    if chars >= 22:
        return "sm"
    if chars >= 16:
        return "md"
    return "lg"


def _grounded_copy(text):
    line = " ".join(str(text or "").split())
    if not line or is_placeholder_copy(line) or UNVERIFIED_CLAIM_RE.search(line):
        return ""
    return line


def _subheadline(facts, planned, language):
    for candidate in (
        (planned or {}).get("subheadline"),
        listing_benefit_line(language, facts),
        facts.get("benefit_line"),
    ):
        line = _grounded_copy(candidate)
        if line and len(line) <= 110:
            return line
    return ""


def _street_line(facts):
    title = " ".join(str(facts.get("title") or "").split())
    postal = " ".join(str(facts.get("postal_code") or "").split())
    if title and postal and postal not in title:
        return f"{title} ({postal})"
    return title


def _location_chips(facts, language):
    blob = " ".join(str(item).casefold() for item in (facts.get("amenities") or []))
    es = language == "es"
    chips = []
    catalog = (
        (("pileta", "piscina", "pool"), "Pileta" if es else "Pool", ICON_TREE),
        (("cochera", "garage"), "Cochera" if es else "Garage", ICON_TREE),
        (("balcon", "balcón", "balcony"), "Balcón" if es else "Balcony", ICON_TREE),
        (("jard", "garden", "patio", "parque"), "Jardín" if es else "Garden", ICON_TREE),
    )
    used = set()
    for needles, label, icon in catalog:
        if label in used:
            continue
        if any(token in blob for token in needles):
            chips.append({"label": label, "icon": icon})
            used.add(label)
    return chips[:3]


def _quote_line(facts, language):
    del facts
    if language == "es":
        return "Tu próximo capítulo te espera"
    return "Your next chapter starts here"


def _branding_view(facts):
    name = " ".join(str(facts.get("brand_name") or facts.get("office_name") or "").split())
    logo_path = _office_logo(facts)
    logo = _path_data_uri(logo_path, keep_alpha=True) if logo_path else ""
    stacked = False
    mark = name
    submark = ""
    if name.upper().startswith("RE/MAX"):
        rest = name[6:].strip(" /")
        if rest:
            stacked = True
            mark = "RE/MAX"
            submark = rest.upper()
    tagline = facts.get("brand_tagline") or "Confianza\nExperiencia\nResultados"
    return {
        "name": name or "RE/MAX",
        "logo": logo,
        "logo_loaded": bool(logo),
        "stacked": stacked,
        "mark": mark,
        "submark": submark,
        "tagline": tagline.replace("\\n", "\n"),
    }


def _facts_view(facts, language):
    rooms = _format_int(facts.get("rooms"))
    bedrooms = _format_int(facts.get("bedrooms"))
    bathrooms = _format_int(facts.get("bathrooms"))
    area = facts.get("covered_m2") if facts.get("covered_m2") not in (None, "") else facts.get("total_m2")
    area_label = _format_int(area)
    es = language == "es"
    return [
        {"value": rooms or "—", "label": "Ambientes" if es else "Rooms", "icon": ICON_ROOMS},
        {"value": bedrooms or "—", "label": "Dormitorios" if es else "Bedrooms", "icon": ICON_BED},
        {"value": bathrooms or "—", "label": "Baños" if es else "Baths", "icon": ICON_BATH},
        {
            "value": f"{area_label} m²" if area_label else "—",
            "label": "Superficie" if es else "Area",
            "icon": ICON_AREA,
        },
    ]


def _cta_view(facts, planned, language):
    label = " ".join(str((planned or {}).get("cta") or "").split())
    if not label or is_placeholder_copy(label):
        label = default_cta(language, facts, style="commercial")
    if "→" not in label:
        label = f"{label.rstrip('.')} →"
    rental = str(facts.get("purpose") or "").lower() in {"rental", "rent", "alquiler"}
    support = (
        "Consultá disponibilidad"
        if rental and language == "es"
        else "Tu proyecto, en manos expertas"
        if language == "es"
        else "Your project, in expert hands"
    )
    return {"label": label, "support": support}


def _debug_enabled():
    raw = os.environ.get("MARKETING_HTML_DEBUG", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return is_development()


def _dump_debug(html, png_bytes, context):
    if not _debug_enabled():
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / "render.html").write_text(html, encoding="utf-8")
    (DEBUG_DIR / "render.png").write_bytes(png_bytes)
    slim = dict(context)
    slim.pop("css", None)
    slim.pop("font_css", None)
    slim.pop("icons", None)
    if slim.get("hero"):
        slim["hero"] = {**slim["hero"], "src": bool(slim["hero"].get("src"))}
    slim["secondary"] = [
        {**item, "src": bool(item.get("src"))} for item in (slim.get("secondary") or [])
    ]
    if slim.get("agent"):
        slim["agent"] = {**slim["agent"], "photo": bool(slim["agent"].get("photo"))}
    if slim.get("branding"):
        slim["branding"] = {**slim["branding"], "logo": bool(slim["branding"].get("logo"))}
    slim["facts"] = [
        {"value": item.get("value"), "label": item.get("label")} for item in (slim.get("facts") or [])
    ]
    (DEBUG_DIR / "render_context.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _listing_photo_rows(context):
    rows = eligible_listing_photos((context or {}).get("photos") or [])
    if rows:
        return rows
    facts = (context or {}).get("facts") or {}
    property_id = facts.get("property_id") or (context or {}).get("property_id")
    organization_id = facts.get("organization_id") or (context or {}).get("organization_id")
    if not property_id:
        return []
    return [
        record["media"]
        for record in get_property_original_media(property_id, organization_id)
    ]


def build_marketing_render_context(context, *, fmt="post", options=None, art=None, language="es"):
    options = options or {}
    art = art or {}
    facts = (context or {}).get("facts") or {}
    language = language or "es"
    include_agent = options.get("include_agent") is not False
    show_price = options.get("show_price") is not False
    agent = (context or {}).get("agent") if include_agent else None
    planned = summarize_listing_copy(
        facts,
        agent,
        headline=art.get("headline") or options.get("headline") or "",
        cta=art.get("cta") or options.get("cta") or "",
        language=language,
        style=options.get("style") or options.get("visual_direction"),
    )
    if art.get("short_hook") and _grounded_copy(art.get("short_hook")):
        planned["subheadline"] = art.get("short_hook")
    photo_rows = _listing_photo_rows(context)
    selected = select_html_render_photos(photo_rows, secondary=3)
    by_id = {}
    for row, image in load_property_photo_pairs(photo_rows):
        key = row.get("id") or row.get("storage_key") or row.get("original_url")
        if key is not None:
            by_id[key] = image
    hero_row = selected.get("hero")
    hero_image = None
    if hero_row is not None:
        hero_image = by_id.get(
            hero_row.get("id") or hero_row.get("storage_key") or hero_row.get("original_url")
        )
    hero_generated = bool(hero_row) and is_generated_marketing_asset(hero_row)
    logger.info(
        "resolved_property_id=%s organization_id=%s",
        facts.get("property_id"),
        facts.get("organization_id"),
    )
    if hero_image is None or hero_generated:
        logger.error(
            "original_photo_fetch_failed slot=hero hero_is_generated_marketing_asset=%s hero_loaded=%s original_url=%s path=%s",
            hero_generated,
            hero_image is not None,
            (hero_row or {}).get("original_url"),
            (hero_row or {}).get("storage_key") or (hero_row or {}).get("path"),
        )
        raise MarketingError("marketing_ia_visual_no_photos", 400)
    secondary_views = []
    secondary_images = []
    used_labels = set()
    preferred = ("living", "cocina", "jardin", "dormitorio", "fachada", "bano", "balcon", "patio")
    for row in selected.get("secondary") or []:
        image = by_id.get(row.get("id") or row.get("storage_key") or row.get("original_url"))
        if image is None:
            logger.error(
                "original_photo_fetch_failed slot=secondary source_type=%s original_url=%s path=%s",
                photo_origin_log(row).get("source_type"),
                row.get("original_url"),
                row.get("storage_key") or row.get("path"),
            )
            continue
        if _too_similar(image, hero_image) or any(_too_similar(image, other) for other in secondary_images):
            continue
        label_key = photo_scene_label(row)
        if label_key and label_key in used_labels:
            continue
        secondary_images.append(image)
        if label_key:
            used_labels.add(label_key)
        caption = SCENE_CAPTIONS.get(label_key) if label_key else None
        secondary_views.append(
            {
                "src": _photo_uri(image, (360, 248)),
                "label": caption[0] if caption else "",
                "caption": caption[1] if caption else "",
                "loaded": True,
                "scene": label_key or "",
                "_origin": photo_origin_log(row, image),
            }
        )
    secondary_views.sort(
        key=lambda item: preferred.index(item["scene"]) if item.get("scene") in preferred else 99
    )
    secondary_origins = [item.pop("_origin") for item in secondary_views]
    currency, amount = _split_price(facts.get("price_label") if show_price else "")
    price = {"currency": currency or "USD", "amount": amount if show_price else ""}
    if not amount:
        price = {"currency": "", "amount": ""}
    agent_view = None
    agent_photo_image = None
    if include_agent and agent:
        photo = None
        if options.get("show_agent_photo", True) and agent.get("photo_path"):
            agent_photo_image = load_agent_photo(agent.get("photo_path"))
            if agent_photo_image is not None:
                photo = _data_uri_from_image(agent_photo_image.convert("RGBA"), fmt="PNG", keep_alpha=True)
        handle = " ".join(str(agent.get("instagram") or "").split())
        if handle and not handle.startswith("@"):
            handle = f"@{handle}"
        agent_view = {
            "name": " ".join(str(agent.get("name") or planned.get("agent_name") or "").split()),
            "title": " ".join(
                str(agent.get("title") or planned.get("agent_title") or "Agente inmobiliario").split()
            ),
            "whatsapp": " ".join(str(agent.get("whatsapp") or "").split()),
            "instagram": handle,
            "email": " ".join(str(agent.get("email") or "").split()),
            "photo": photo or "",
        }
        if not any(agent_view[key] for key in ("name", "whatsapp", "instagram", "email", "photo")):
            agent_view = None
    branding = _branding_view(facts)
    kicker = facts.get("kicker") or default_kicker(language, facts)
    modifiers = []
    if not price.get("amount"):
        modifiers.append("poster--no-price")
    if not agent_view:
        modifiers.append("poster--no-agent")
    elif not agent_view.get("photo"):
        modifiers.append("poster--no-agent-photo")
    chips = _location_chips(facts, language)
    if not chips:
        modifiers.append("poster--no-chips")
    lines = _headline_lines(
        facts,
        {"headline": art.get("headline") or options.get("headline") or ""},
        language,
    )
    street = _street_line(facts)
    city = marketing_zone_line(facts)
    qa_required = _qa_mode()
    extra = max(0, len(photo_rows) - 1)
    asset_status = {
        "hero_photo": _qa_asset_status("hero_photo", hero_image, required=True),
        "secondary_photos[0]": _qa_asset_status(
            "secondary_photos[0]",
            secondary_images[0] if len(secondary_images) > 0 else None,
            required=qa_required and extra >= 1,
        ),
        "secondary_photos[1]": _qa_asset_status(
            "secondary_photos[1]",
            secondary_images[1] if len(secondary_images) > 1 else None,
            required=qa_required and extra >= 2,
        ),
        "secondary_photos[2]": _qa_asset_status(
            "secondary_photos[2]",
            secondary_images[2] if len(secondary_images) > 2 else None,
            required=qa_required and extra >= 3,
        ),
        "agent.photo": _qa_asset_status(
            "agent.photo",
            agent_photo_image,
            required=qa_required and include_agent and options.get("show_agent_photo", True),
        ),
    }
    hero_origin = photo_origin_log(hero_row, hero_image)
    while len(secondary_origins) < 3:
        secondary_origins.append(photo_origin_log(None, None))
    logger.info(
        "hero_photo source_type=%s asset_id=%s original_url/path=%s width=%s height=%s",
        hero_origin.get("source_type"),
        hero_origin.get("asset_id"),
        hero_origin.get("original_url") or hero_origin.get("path"),
        hero_origin.get("width"),
        hero_origin.get("height"),
    )
    for index, origin in enumerate(secondary_origins[:3]):
        logger.info(
            "secondary_photos[%s] source_type=%s asset_id=%s original_url/path=%s width=%s height=%s",
            index,
            origin.get("source_type"),
            origin.get("asset_id"),
            origin.get("original_url") or origin.get("path"),
            origin.get("width"),
            origin.get("height"),
        )
    logger.info("hero_is_generated_marketing_asset=%s", bool(hero_generated))
    logger.info(
        "template_used=%s renderer_used=%s visual_status=complete",
        HTML_TEMPLATE,
        HTML_RENDERER,
    )
    return {
        "language": language,
        "body_class": "marketing-html-render",
        "modifiers": " ".join(modifiers),
        "operation": kicker,
        "headline_lines": lines,
        "headline_size": _headline_size(lines),
        "headline_accent": bool(lines) and bool(facts.get("locality") or facts.get("neighborhood")),
        "subheadline": _subheadline(facts, planned, language),
        "price": price,
        "facts": _facts_view(facts, language),
        "hero": {
            "src": _hero_uri(hero_image) if hero_image is not None else "",
            "loaded": hero_image is not None,
            "focus_x": _hero_focus(hero_row, options)[0],
            "focus_y": _hero_focus(hero_row, options)[1],
        },
        "secondary": secondary_views,
        "location": {
            "street": street,
            "city": city,
            "chips": chips,
            "quote": _quote_line(facts, language),
        },
        "agent": agent_view,
        "cta": _cta_view(
            facts,
            {"cta": art.get("cta") or options.get("cta") or ""},
            language,
        ),
        "branding": branding,
        "legal": " ".join(
            str(facts.get("legal_footer_line") or planned.get("legal_footer") or "").split()
        ),
        "icons": {
            "pin": ICON_PIN,
            "whatsapp": ICON_WA,
            "whatsapp_light": ICON_WA_LIGHT,
            "instagram": ICON_IG,
            "email": ICON_EMAIL,
        },
        "css": _read_css(),
        "font_css": _font_face_css(),
        "template_used": HTML_TEMPLATE,
        "renderer_used": HTML_RENDERER,
        "photo_count": (1 if hero_image is not None else 0) + len(secondary_views),
        "logo_loaded": branding.get("logo_loaded"),
        "agent_photo_loaded": bool(agent_view and agent_view.get("photo")),
        "asset_status": asset_status,
        "photo_origins": {
            "hero_photo": hero_origin,
            "secondary_photos": secondary_origins[:3],
        },
        "hero_is_generated_marketing_asset": bool(hero_generated),
    }


def render_marketing_html(render_context):
    template = _jinja().get_template(TEMPLATE_FILE)
    return template.render(**render_context)


def _launch_browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MarketingError("marketing_err_html_render_failed", 500) from exc
    root = _package_root()
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (root / "ms-playwright"))
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    playwright = sync_playwright().start()
    launch_kwargs = {
        "headless": True,
        "args": [
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            "--font-render-hinting=none",
            "--hide-scrollbars",
        ],
    }
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "").strip()
    if executable:
        launch_kwargs["executable_path"] = executable
    try:
        browser = playwright.chromium.launch(**launch_kwargs)
    except Exception as exc:
        playwright.stop()
        logger.exception("playwright chromium launch failed")
        raise MarketingError("marketing_err_html_render_failed", 500) from exc
    return playwright, browser


def _get_browser():
    global _playwright, _browser
    with _lock:
        if _browser is not None:
            try:
                if _browser.is_connected():
                    return _browser
            except Exception:
                _browser = None
        _playwright, _browser = _launch_browser()
        return _browser


def close_html_renderer():
    global _playwright, _browser
    with _lock:
        browser = _browser
        playwright = _playwright
        _browser = None
        _playwright = None
    if browser is not None:
        try:
            browser.close()
        except Exception:
            logger.exception("playwright browser close failed")
    if playwright is not None:
        try:
            playwright.stop()
        except Exception:
            logger.exception("playwright stop failed")


atexit.register(close_html_renderer)


def screenshot_poster(html, *, timeout_ms=30000):
    width, height = POSTER_SIZE
    browser = _get_browser()
    with _lock:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            java_script_enabled=True,
        )
        try:
            page = context.new_page()
            page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.url.startswith(("http://", "https://"))
                else route.continue_(),
            )
            page.set_content(html, wait_until="load", timeout=timeout_ms)
            page.evaluate("() => document.fonts.ready")
            poster = page.locator("#poster")
            box = poster.bounding_box()
            if not box:
                raise MarketingError("marketing_err_html_render_failed", 500)
            png = poster.screenshot(type="png", omit_background=False, timeout=timeout_ms)
            metrics = page.evaluate(
                """() => {
                    const el = document.getElementById('poster');
                    const overflow = [];
                    const nodes = el.querySelectorAll('h1, .subhead, .cta__btn em, .agent__meta h2, .price__amount, .place__addr strong');
                    nodes.forEach((node) => {
                      if (node.scrollWidth > node.clientWidth + 6) {
                        overflow.push(node.className || node.tagName);
                      }
                    });
                    return {
                      width: el.offsetWidth,
                      height: el.offsetHeight,
                      scrollWidth: el.scrollWidth,
                      scrollHeight: el.scrollHeight,
                      clientWidth: el.clientWidth,
                      clientHeight: el.clientHeight,
                      overflow,
                      hasHero: Boolean(el.querySelector('.hero__photo')),
                      hasAgent: Boolean(el.querySelector('.agent__photo img')),
                      hasLogo: Boolean(el.querySelector('.brand__logo')),
                    };
                }"""
            )
        except MarketingError:
            raise
        except Exception as exc:
            logger.exception("playwright screenshot failed")
            raise MarketingError("marketing_err_html_render_failed", 500) from exc
        finally:
            context.close()
    return png, metrics


def render_html_visual(context, fmt, *, options=None, art=None, language="es"):
    if not uses_html_renderer(fmt, options):
        raise MarketingError("marketing_err_html_render_failed", 500)
    render_context = build_marketing_render_context(
        context,
        fmt=fmt,
        options=options,
        art=art,
        language=language,
    )
    if not render_context["hero"].get("loaded"):
        raise MarketingError("marketing_ia_visual_no_photos", 400)
    if _qa_mode():
        failed = [
            name
            for name, status in (render_context.get("asset_status") or {}).items()
            if status == "failed"
        ]
        if failed:
            logger.error("marketing_html_qa failed assets=%s", ",".join(failed))
            raise MarketingError("marketing_err_html_render_failed", 500)
    html = render_marketing_html(render_context)
    png_bytes, metrics = screenshot_poster(html)
    image = Image.open(io.BytesIO(png_bytes))
    if image.size != POSTER_SIZE:
        logger.error("html render size mismatch size=%s", image.size)
        raise MarketingError("marketing_err_html_render_failed", 500)
    _dump_debug(html, png_bytes, render_context)
    logger.info(
        "template_used=%s renderer_used=%s photos=%s",
        HTML_TEMPLATE,
        HTML_RENDERER,
        render_context.get("photo_count"),
    )
    return {
        "png_bytes": png_bytes,
        "html": html,
        "metrics": metrics,
        "render_context": render_context,
        "agent_photo_composited": bool(render_context.get("agent_photo_loaded")),
        "logo_stamped": bool(render_context.get("logo_loaded")),
        "post_process": "html_playwright",
        "post_process_fn": "modules.marketing_render_html.render_html_visual",
        "layout": HTML_TEMPLATE,
        "template_used": HTML_TEMPLATE,
        "renderer_used": HTML_RENDERER,
        "layout_version": HTML_TEMPLATE,
        "listing_photos": render_context.get("photo_count") or 0,
    }
