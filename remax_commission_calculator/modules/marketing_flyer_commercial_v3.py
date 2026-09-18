"""modern_commercial_v3 — Instagram feed 1080×1350.

Architecture:
  build_marketing_v3_context()
  select_visual_photos()
  compose_photo_base()     # Pillow: cover-crop / paste photos only
  build_marketing_svg()    # SVG chrome: type, overlays, CTA (no legacy V1/V2)
  render_svg_to_png()      # PyMuPDF raster
  composite               # photos under chrome

Does not call render_modern_premium_v1 / modern_commercial_v2 / editorial helpers.
"""

from __future__ import annotations

import html
import io
import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw

from modules.marketing_context import MarketingError
from modules.marketing_language import (
    default_kicker,
    is_placeholder_copy,
    listing_benefit_line,
    marketing_label,
    sellable_headline_lines,
)
from modules.marketing_photo_selector import photo_scene_label, select_photos_for_item
from modules.marketing_renderer import (
    fit_cover,
    load_agent_photo,
    prepare_agent_cutout,
    _office_brand,
    _office_logo,
    _open_image,
)

logger = logging.getLogger(__name__)

MODERN_COMMERCIAL_V3 = "modern_commercial_v3"
RENDERER_USED = "svg_commercial_v3"
LAYOUT_VERSION = MODERN_COMMERCIAL_V3

CANVAS_W = 1080
CANVAS_H = 1350
HERO_H = 650
GALLERY_H = 230
LOCATION_H = 130
AGENT_H = 270
FOOTER_H = CANVAS_H - HERO_H - GALLERY_H - LOCATION_H - AGENT_H

FONT = "Arial, Helvetica, DejaVu Sans, sans-serif"
NAVY = "#0e1c38"
INK = "#111c33"
MUTED = "#5b6b7c"
SOFT = "#e8ecf2"
PAGE = "#edf0f4"
WHITE = "#ffffff"
WA = "#25d366"
IG = "#c13584"

SCENE_LABELS = {
    "living": "LIVING",
    "cocina": "COCINA",
    "jardin": "JARDÍN",
    "fachada": "FACHADA",
    "dormitorio": "DORMITORIO",
}
DEFAULT_SCENES = ("living", "cocina", "jardin")


def render_modern_commercial_v3(
    size,
    photos,
    facts,
    copy,
    agent,
    options,
    language="es",
    *,
    fallback_hero=None,
    photo_rows=None,
):
    width, height = size
    ctx = build_marketing_v3_context(
        facts=facts,
        copy=copy,
        agent=agent,
        options=options,
        language=language,
        photo_rows=photo_rows,
        photos=photos,
        fallback_hero=fallback_hero,
    )
    base = compose_photo_base(ctx)
    svg = build_marketing_svg(ctx)
    chrome_png = render_svg_to_png(svg, width=CANVAS_W, height=CANVAS_H)
    chrome = Image.open(io.BytesIO(chrome_png)).convert("RGBA")
    if chrome.size != (CANVAS_W, CANVAS_H):
        chrome = chrome.resize((CANVAS_W, CANVAS_H), Image.Resampling.LANCZOS)
    chrome = _knockout_svg_paper(chrome)
    out = Image.alpha_composite(base.convert("RGBA"), chrome)
    agent = ctx.get("agent")
    if agent and agent.get("cutout") is not None:
        cut = agent["cutout"]
        ax = 32
        ay = HERO_H + GALLERY_H + LOCATION_H + AGENT_H - cut.height - 10
        out.paste(cut, (ax, ay), cut)
    out = out.convert("RGB")
    if out.size != (width, height):
        out = out.resize((width, height), Image.Resampling.LANCZOS)
    return out


def build_marketing_v3_context(
    *,
    facts,
    copy,
    agent,
    options,
    language="es",
    photo_rows=None,
    photos=None,
    fallback_hero=None,
):
    facts = facts or {}
    copy = copy or {}
    options = options or {}
    language = language or "es"

    visual = select_visual_photos(
        photo_rows=photo_rows,
        photos=photos,
        fallback_hero=fallback_hero,
        options=options,
    )
    headline_lines = [_hero_case(line) for line in _headline_lines(copy, facts, language)]
    bajada = _short_benefit(copy, facts, language)
    currency, amount = _split_price(
        facts.get("price_label") if options.get("show_price", True) else ""
    )
    facts_items = _fact_items(facts, copy) if options.get("show_features", True) else []

    street = " ".join(str(facts.get("title") or facts.get("address") or "").split())
    zone_parts = [
        facts.get("neighborhood") or facts.get("locality"),
        facts.get("jurisdiction"),
    ]
    zone = " · ".join(str(part) for part in zone_parts if part)
    if not zone:
        zone = " ".join(str(facts.get("location_line") or "").split())

    logo_img = _load_logo_image(facts)
    brand = facts.get("wordmark_text") or _office_brand(facts) or ""

    agent_block = None
    if options.get("include_agent") and agent:
        agent_block = _prepare_agent(agent, language)

    location_benefit = _clip(
        copy.get("short_benefit")
        or "Espacios amplios y diseño para disfrutar todos los días.",
        64,
    )
    if is_placeholder_copy(location_benefit):
        location_benefit = _clip(listing_benefit_line(language, facts), 64)

    return {
        "language": language,
        "kicker": default_kicker(language, facts),
        "headline_lines": headline_lines,
        "bajada": bajada,
        "facts": facts_items,
        "currency": currency or "USD",
        "amount": amount,
        "hero_image": visual["hero_image"],
        "gallery": visual["gallery"],
        "street": _clip(street, 42),
        "zone": _clip(zone, 48),
        "location_benefit": location_benefit,
        "logo_image": logo_img,
        "brand": _clip(brand, 28),
        "agent": agent_block,
        "cta": _clip(copy.get("cta") or "Consultame para visitarla", 40),
        "office": _clip(
            " ".join(str(facts.get("brand_name") or facts.get("office_name") or brand or "").split()),
            36,
        ),
        "legal": _clip(
            " ".join(
                str(facts.get("broker_footer_text") or facts.get("legal_footer_line") or "").split()
            ),
            78,
        ),
        "show_price": bool(amount),
    }


def select_visual_photos(*, photo_rows=None, photos=None, fallback_hero=None, options=None):
    options = options or {}
    rows = list(photo_rows or [])
    images = list(photos or [])
    selected_images = []

    if rows:
        chosen_rows = select_photos_for_item(
            rows, fmt="post", index=int(options.get("photo_index") or 0), limit=4
        )
        by_id = {}
        for img, row in zip(images, rows):
            key = row.get("id") or row.get("storage_key")
            if key is not None:
                by_id[key] = img
        for row in chosen_rows:
            key = row.get("id") or row.get("storage_key")
            img = by_id.get(key)
            if img is None and images:
                idx = rows.index(row) if row in rows else -1
                img = images[idx] if 0 <= idx < len(images) else None
            if img is None:
                img = _open_row_image(row)
            if img is not None:
                selected_images.append((img, row))
    else:
        selected_images = [(img, {}) for img in images[:4] if img is not None]

    if not selected_images and fallback_hero is not None:
        selected_images = [(fallback_hero, {})]
    if not selected_images:
        raise MarketingError("marketing_err_v3_no_photos", 400)

    hero_img, _ = selected_images[0]
    pad = 28
    gap = 12
    cell_w = (CANVAS_W - pad * 2 - gap * 2) // 3
    cell_h = GALLERY_H - 24

    gallery = []
    used = set()
    for index, (img, row) in enumerate(selected_images[1:4]):
        scene = photo_scene_label(row) or DEFAULT_SCENES[min(index, 2)]
        if scene in used and len(selected_images) > 4:
            continue
        used.add(scene)
        gallery.append(
            {
                "image": fit_cover(img, cell_w, cell_h, focus=(0.5, 0.42)),
                "label": SCENE_LABELS.get(scene, DEFAULT_SCENES[index].upper()),
                "w": cell_w,
                "h": cell_h,
            }
        )
    while len(gallery) < min(3, max(0, len(selected_images) - 1)):
        src = selected_images[min(len(gallery) + 1, len(selected_images) - 1)][0]
        scene = DEFAULT_SCENES[len(gallery) % 3]
        gallery.append(
            {
                "image": fit_cover(src, cell_w, cell_h, focus=(0.5, 0.42)),
                "label": SCENE_LABELS[scene],
                "w": cell_w,
                "h": cell_h,
            }
        )

    return {
        "hero_image": fit_cover(hero_img, CANVAS_W, HERO_H, focus=(0.48, 0.34)),
        "gallery": gallery[:3],
    }


def compose_photo_base(ctx):
    """Pillow photo plane: cover crops, hero shade, gallery scrims, agent cutout."""
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (*_hex_rgb(PAGE), 255))
    hero = ctx.get("hero_image")
    if hero is not None:
        canvas.paste(hero.convert("RGB"), (0, 0))
    else:
        ImageDraw.Draw(canvas).rectangle((0, 0, CANVAS_W, HERO_H), fill=_hex_rgb(NAVY))
    shade = _hero_shade_rgba(CANVAS_W, HERO_H)
    canvas.paste(shade, (0, 0), shade)

    pad = 28
    gap = 12
    y = HERO_H + 12
    x = pad
    for item in ctx.get("gallery") or []:
        thumb = item["image"].convert("RGB")
        rounded = _round_corners(thumb, 14)
        # Bottom label scrim baked into the thumb asset
        scrim = _thumb_scrim(item["w"], item["h"])
        rounded = Image.alpha_composite(rounded, scrim)
        canvas.paste(rounded, (x, y), rounded)
        x += item["w"] + gap

    # Agent cutout is composited after SVG chrome so it sits above the card.
    logo = ctx.get("logo_image")
    if logo is not None:
        mark = logo.convert("RGBA")
        mark.thumbnail((56, 56), Image.Resampling.LANCZOS)
        canvas.paste(mark, (36, 18), mark)

    return canvas


def build_marketing_svg(ctx):
    # Opaque SVG chrome only (PyMuPDF flattens translucent fills against white).
    # Hero shade + gallery scrims are prepared on the photo plane.
    return "".join(
        [
            f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{CANVAS_H}"
     viewBox="0 0 {CANVAS_W} {CANVAS_H}">
''',
            render_svg_hero(ctx),
            render_svg_gallery(ctx),
            render_svg_location(ctx),
            render_svg_agent_cta(ctx),
            render_svg_footer(ctx),
            "</svg>",
        ]
    )


def render_svg_hero(ctx):
    pad = 44
    chunks = ['  <g id="hero">\n']
    brand = ctx.get("brand") or ""
    if brand:
        chunks.append(
            f'    <text x="104" y="52" font-family="{FONT}" font-size="18" font-weight="700" '
            f'fill="{WHITE}" fill-opacity="0.95">{_esc(brand)}</text>\n'
        )

    y = 118
    kicker = ctx.get("kicker") or ""
    if kicker:
        kw = max(118, len(kicker) * 10 + 40)
        chunks.append(
            f'    <rect x="{pad}" y="{y}" width="{kw}" height="34" rx="17" fill="#121820"/>\n'
            f'    <text x="{pad + 18}" y="{y + 23}" font-family="{FONT}" font-size="15" '
            f'font-weight="700" fill="{WHITE}">{_esc(kicker)}</text>\n'
        )
        y += 58

    lines = ctx.get("headline_lines") or []
    title_px = _headline_px(lines)
    for line in lines[:3]:
        chunks.append(
            f'    <text x="{pad}" y="{y}" font-family="{FONT}" font-size="{title_px}" '
            f'font-weight="700" fill="{WHITE}">{_esc(line)}</text>\n'
        )
        y += int(title_px * 0.96)
    y += 8

    bajada = ctx.get("bajada") or ""
    if bajada:
        for line in _wrap_chars(bajada, 40)[:2]:
            chunks.append(
                f'    <text x="{pad}" y="{y}" font-family="{FONT}" font-size="22" '
                f'fill="{SOFT}">{_esc(line)}</text>\n'
            )
            y += 28

    chunks.append(render_svg_facts(ctx, pad=pad, bottom=HERO_H - 40))
    if ctx.get("show_price"):
        chunks.append(render_svg_price(ctx, right=CANVAS_W - 36, bottom=HERO_H - 36))
    chunks.append("  </g>\n")
    return "".join(chunks)


def render_svg_facts(ctx, *, pad, bottom):
    items = ctx.get("facts") or []
    if not items:
        return ""
    x = pad
    y = bottom
    chunks = ['    <g id="facts">\n']
    for number, label in items[:4]:
        top = f"{number}".strip()
        low = f"{label}".strip()
        if top:
            chunks.append(
                f'      <text x="{x}" y="{y - 22}" font-family="{FONT}" font-size="26" '
                f'font-weight="700" fill="{WHITE}">{_esc(top)}</text>\n'
                f'      <text x="{x}" y="{y}" font-family="{FONT}" font-size="15" '
                f'fill="{SOFT}">{_esc(low)}</text>\n'
            )
            x += max(150, max(len(top), len(low)) * 11 + 36)
        else:
            chunks.append(
                f'      <text x="{x}" y="{y - 8}" font-family="{FONT}" font-size="18" '
                f'font-weight="700" fill="{WHITE}">{_esc(low)}</text>\n'
            )
            x += len(low) * 10 + 36
    chunks.append("    </g>\n")
    return "".join(chunks)


def render_svg_price(ctx, *, right, bottom):
    currency = ctx.get("currency") or "USD"
    amount = ctx.get("amount") or ""
    if not amount:
        return ""
    card_w = max(240, len(amount) * 34 + 70)
    card_h = 120
    x0 = right - card_w
    y0 = bottom - card_h
    return (
        f'    <g id="price">\n'
        f'      <rect x="{x0}" y="{y0}" width="{card_w}" height="{card_h}" rx="22" fill="{NAVY}"/>\n'
        f'      <text x="{x0 + 30}" y="{y0 + 40}" font-family="{FONT}" font-size="20" '
        f'font-weight="700" fill="{SOFT}">{_esc(currency)}</text>\n'
        f'      <text x="{x0 + 30}" y="{y0 + 94}" font-family="{FONT}" font-size="54" '
        f'font-weight="700" fill="{WHITE}">{_esc(amount)}</text>\n'
        f'    </g>\n'
    )


def render_svg_gallery(ctx):
    top = HERO_H
    pad = 28
    gap = 12
    chunks = [f'  <g id="gallery" transform="translate(0,{top})">\n']
    x = pad
    y = 12
    for item in ctx.get("gallery") or []:
        w, h = item["w"], item["h"]
        chunks.append(
            f'    <text x="{x + 14}" y="{y + h - 18}" font-family="{FONT}" font-size="16" '
            f'font-weight="700" fill="{WHITE}">{_esc(item.get("label") or "")}</text>\n'
        )
        x += w + gap
    chunks.append("  </g>\n")
    return "".join(chunks)


def render_svg_location(ctx):
    top = HERO_H + GALLERY_H
    pad = 36
    chunks = [
        f'  <g id="location" transform="translate(0,{top})">\n',
        f'    <rect x="0" y="0" width="{CANVAS_W}" height="{LOCATION_H}" fill="#e2e7ee"/>\n',
        f'    <circle cx="{pad + 22}" cy="64" r="22" fill="{NAVY}"/>\n',
        f'    <circle cx="{pad + 22}" cy="60" r="7" fill="none" stroke="{WHITE}" stroke-width="2.5"/>\n',
        f'    <path d="M{pad + 22} 67 l0 10" stroke="{WHITE}" stroke-width="2.5" '
        f'stroke-linecap="round"/>\n',
        f'    <text x="{pad + 58}" y="56" font-family="{FONT}" font-size="26" font-weight="700" '
        f'fill="{INK}">{_esc(ctx.get("street") or "")}</text>\n',
        f'    <text x="{pad + 58}" y="86" font-family="{FONT}" font-size="17" '
        f'fill="{MUTED}">{_esc(ctx.get("zone") or "")}</text>\n',
    ]
    benefit = ctx.get("location_benefit") or ""
    bx = 560
    for i, line in enumerate(_wrap_chars(f"“{benefit}”" if benefit else "", 32)[:2]):
        chunks.append(
            f'    <text x="{bx}" y="{56 + i * 26}" font-family="{FONT}" font-size="18" '
            f'fill="{INK}">{_esc(line)}</text>\n'
        )
    chunks.append("  </g>\n")
    return "".join(chunks)


def render_svg_agent_cta(ctx):
    top = HERO_H + GALLERY_H + LOCATION_H
    pad = 28
    chunks = [
        f'  <g id="agent" transform="translate(0,{top})">\n',
        f'    <rect x="{pad // 2}" y="10" width="{CANVAS_W - pad}" height="{AGENT_H - 20}" '
        f'rx="22" fill="#f4f6f9"/>\n',
    ]
    agent = ctx.get("agent")
    text_x = pad + 210
    if agent and agent.get("cutout") is not None:
        text_x = pad + max(200, int(agent["cutout"].width * 0.58) + 24)
    elif agent:
        text_x = pad + 36

    if agent:
        chunks.append(
            f'    <text x="{text_x}" y="78" font-family="{FONT}" font-size="28" font-weight="700" '
            f'fill="{INK}">{_esc(agent.get("name") or "")}</text>\n'
            f'    <text x="{text_x}" y="110" font-family="{FONT}" font-size="17" '
            f'fill="{MUTED}">{_esc(agent.get("title") or "")}</text>\n'
        )
        cy = 148
        if agent.get("whatsapp"):
            chunks.append(_contact_row(text_x, cy, "wa", agent["whatsapp"]))
            cy += 36
        if agent.get("instagram"):
            chunks.append(_contact_row(text_x, cy, "ig", agent["instagram"]))
    else:
        chunks.append(
            f'    <text x="{pad + 36}" y="100" font-family="{FONT}" font-size="26" '
            f'font-weight="700" fill="{INK}">Consultá con la oficina</text>\n'
        )

    chunks.append(render_svg_cta(ctx))
    chunks.append("  </g>\n")
    return "".join(chunks)


def render_svg_cta(ctx):
    lines = _cta_lines(ctx.get("cta") or "Consultame para visitarla")
    btn_w = 390
    btn_h = 108
    bx = CANVAS_W - 42 - btn_w
    by = 68
    chunks = [
        f'    <g id="cta">\n',
        f'      <rect x="{bx}" y="{by}" width="{btn_w}" height="{btn_h}" rx="54" fill="{NAVY}"/>\n',
        f'      <circle cx="{bx + 46}" cy="{by + btn_h // 2}" r="24" fill="{WA}"/>\n',
        f'      <text x="{bx + 46}" y="{by + btn_h // 2 + 7}" text-anchor="middle" '
        f'font-family="{FONT}" font-size="18" font-weight="700" fill="{WHITE}">WA</text>\n',
    ]
    ty = by + (42 if len(lines) == 1 else 40)
    for line in lines[:2]:
        chunks.append(
            f'      <text x="{bx + 86}" y="{ty}" font-family="{FONT}" font-size="21" '
            f'font-weight="700" fill="{WHITE}">{_esc(line)}</text>\n'
        )
        ty += 30
    chunks.append("    </g>\n")
    return "".join(chunks)


def render_svg_footer(ctx):
    top = HERO_H + GALLERY_H + LOCATION_H + AGENT_H
    pad = 28
    return (
        f'  <g id="footer" transform="translate(0,{top})">\n'
        f'    <line x1="{pad}" y1="10" x2="{CANVAS_W - pad}" y2="10" stroke="#c5cbd6" '
        f'stroke-width="1"/>\n'
        f'    <text x="{pad}" y="40" font-family="{FONT}" font-size="13" font-weight="700" '
        f'fill="{INK}">{_esc(ctx.get("office") or "")}</text>\n'
        f'    <text x="{CANVAS_W / 2}" y="40" text-anchor="middle" font-family="{FONT}" '
        f'font-size="11" fill="{MUTED}">{_esc(ctx.get("legal") or "")}</text>\n'
        f'    <text x="{CANVAS_W - pad}" y="40" text-anchor="end" font-family="{FONT}" '
        f'font-size="13" font-weight="700" fill="{INK}">JRH One</text>\n'
        f'  </g>\n'
    )


def render_svg_to_png(svg_markup, *, width=CANVAS_W, height=CANVAS_H):
    payload = svg_markup.encode("utf-8") if isinstance(svg_markup, str) else svg_markup
    try:
        import fitz
    except ImportError as exc:
        raise MarketingError("marketing_err_v3_rasterizer", 500) from exc
    try:
        doc = fitz.open(stream=payload, filetype="svg")
        try:
            page = doc[0]
            pix = page.get_pixmap(alpha=True)
            if pix.width != width or pix.height != height:
                mat = fitz.Matrix(width / max(1, pix.width), height / max(1, pix.height))
                pix = page.get_pixmap(matrix=mat, alpha=True)
            return pix.tobytes("png")
        finally:
            doc.close()
    except MarketingError:
        raise
    except Exception as exc:
        logger.exception("v3 svg raster failed")
        try:
            import cairosvg

            return cairosvg.svg2png(
                bytestring=payload,
                output_width=width,
                output_height=height,
            )
        except Exception as cairo_fail:
            raise MarketingError("marketing_err_v3_render_failed", 500) from cairo_fail


# --- local helpers -----------------------------------------------------------------


def _knockout_svg_paper(image):
    """PyMuPDF paints an opaque white page; knock it out so photos show through."""
    rgba = image.convert("RGBA")
    datas = list(rgba.getdata())
    cleaned = [
        (r, g, b, 0) if (a and r >= 252 and g >= 252 and b >= 252) else (r, g, b, a)
        for r, g, b, a in datas
    ]
    rgba.putdata(cleaned)
    return rgba


def _hero_shade_rgba(width, height):
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = shade.load()
    band = int(width * 0.62)
    for x in range(band):
        t = x / float(max(1, band - 1))
        if t < 0.20:
            fade = 0.92
        elif t < 0.55:
            fade = 0.92 - (t - 0.20) * 1.35
        else:
            fade = max(0.0, (1.0 - t) * 0.75)
        alpha = int(200 * max(0.0, min(1.0, fade)))
        for y in range(height):
            pixels[x, y] = (5, 10, 18, alpha)
    return shade


def _thumb_scrim(width, height):
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = layer.load()
    start = int(height * 0.55)
    for y in range(start, height):
        t = (y - start) / float(max(1, height - start - 1))
        alpha = int(190 * (t ** 0.85))
        for x in range(width):
            pixels[x, y] = (5, 10, 18, alpha)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), 14, fill=255)
    layer.putalpha(Image.composite(layer.split()[-1], Image.new("L", (width, height), 0), mask))
    return layer


def _esc(value):
    return html.escape(str(value or ""), quote=True)


def _hex_rgb(value):
    text = value.lstrip("#")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))


def _clip(text, limit):
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    clipped = value[: limit - 1].rsplit(" ", 1)[0].strip()
    return clipped or value[:limit]


def _wrap_chars(text, width):
    words = " ".join(str(text or "").split()).split(" ")
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _hero_case(line):
    text = " ".join(str(line or "").split())
    # Keep short connectors readable: EN / IN stay upper with the stack.
    return text.upper()


def _headline_lines(copy, facts, language):
    raw = " ".join(str((copy or {}).get("headline") or "").split())
    if raw and not is_placeholder_copy(raw):
        return _stack_headline(raw, language)[:3]
    return sellable_headline_lines(language, facts)[:3]


def _stack_headline(text, language):
    text = " ".join(str(text or "").split())
    if not text:
        return []
    connector = " en " if language != "en" else " in "
    lower = text.lower()
    token = connector.strip()
    idx = lower.rfind(token)
    if idx > 0:
        left = text[:idx].strip()
        right = text[idx:].strip()
        left_words = left.split()
        if left_words and left_words[0][:1].isdigit():
            return [left, right]
        if 1 <= len(left_words) <= 3:
            return [left, right]
        mid = (len(left_words) + 1) // 2
        return [" ".join(left_words[:mid]), " ".join(left_words[mid:]), right]
    words = text.split()
    if len(words) <= 3:
        return [text] if len(words) <= 2 else [" ".join(words[:-1]), words[-1]]
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _headline_px(lines):
    n = max((len(line) for line in (lines or ["x"])), default=1)
    if n <= 12 and len(lines or []) <= 2:
        return 84
    if n <= 16:
        return 74
    if n <= 20:
        return 68
    return 64


def _short_benefit(copy, facts, language):
    raw = " ".join(
        str(
            (copy or {}).get("subheadline")
            or (copy or {}).get("short_benefit")
            or (facts or {}).get("benefit_line")
            or ""
        ).split()
    )
    if raw and not is_placeholder_copy(raw):
        return _clip(raw, 90)
    return _clip(listing_benefit_line(language, facts), 90)


def _fact_items(facts, copy):
    chips = (facts or {}).get("chips") or (copy or {}).get("attributes") or []
    items = []
    for chip in chips[:4]:
        number, label = _parse_chip(chip)
        items.append((number or "", label or str(chip)))
    return items


def _parse_chip(chip):
    text = " ".join(str(chip or "").split())
    match = re.match(r"^(\d+[.,]?\d*)\s*(.*)$", text)
    if match:
        return match.group(1), match.group(2).strip()
    return "", text


def _split_price(price_label):
    text = " ".join(str(price_label or "").split())
    if not text:
        return "", ""
    match = re.match(
        r"^(USD|U\$S|US\$|ARS|\$)?\s*([0-9][0-9\.\,\s]*)$",
        text,
        flags=re.I,
    )
    if match:
        currency = (match.group(1) or "USD").upper().replace("U$S", "USD").replace("US$", "USD")
        if currency == "$":
            currency = "USD"
        return currency, match.group(2).strip()
    parts = text.split(" ", 1)
    if len(parts) == 2 and parts[0].upper() in {"USD", "ARS", "U$S", "$"}:
        return parts[0].upper().replace("U$S", "USD"), parts[1]
    return "", text


def _cta_lines(cta):
    text = " ".join(str(cta or "").split())
    if not text:
        return ["CONSULTAME"]
    upper = text.upper()
    if "PARA " in upper:
        left, right = upper.split("PARA ", 1)
        return [(left.strip() or "CONSULTAME"), f"PARA {right.strip()} →"]
    words = upper.split()
    if len(words) <= 2:
        return [f"{upper} →"]
    mid = max(1, len(words) // 2)
    return [" ".join(words[:mid]), " ".join(words[mid:]) + " →"]


def _contact_row(x, y, kind, label):
    if kind == "wa":
        icon = (
            f'<circle cx="{x + 12}" cy="{y}" r="12" fill="{WA}"/>'
            f'<text x="{x + 12}" y="{y + 5}" text-anchor="middle" font-size="11" '
            f'font-weight="700" fill="{WHITE}">W</text>'
        )
    else:
        icon = (
            f'<rect x="{x}" y="{y - 12}" width="24" height="24" rx="6" fill="{IG}"/>'
            f'<circle cx="{x + 12}" cy="{y}" r="6" fill="none" stroke="{WHITE}" stroke-width="2"/>'
        )
    return (
        f'    <g>{icon}<text x="{x + 34}" y="{y + 5}" font-family="{FONT}" font-size="17" '
        f'fill="{INK}">{_esc(label)}</text></g>\n'
    )


def _round_corners(image, radius):
    rgba = image.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, rgba.size[0] - 1, rgba.size[1] - 1), radius, fill=255)
    rgba.putalpha(mask)
    return rgba


def _load_logo_image(facts):
    path = _office_logo(facts)
    if not path:
        return None
    try:
        return _open_image(path)
    except Exception:
        return None


def _open_row_image(row):
    try:
        from modules.property_sync.media import resolve_media_filesystem_path

        path = resolve_media_filesystem_path(row)
        if path and Path(path).is_file():
            return _open_image(path)
    except Exception:
        return None
    return None


def _prepare_agent(agent, language):
    name = " ".join(str(agent.get("name") or "").split())
    title = " ".join(
        str(
            agent.get("title")
            or marketing_label("agent_role", language)
            or "Agente inmobiliario"
        ).split()
    )
    whatsapp = " ".join(str(agent.get("whatsapp") or "").split())
    instagram = " ".join(str(agent.get("instagram") or "").split())
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram.lstrip('@')}"
    cutout = None
    raw = load_agent_photo(agent.get("photo_path")) if agent.get("photo_path") else None
    if raw is not None:
        prepared = prepare_agent_cutout(raw) or raw.convert("RGBA")
        target_h = 248
        scale = target_h / float(prepared.height or 1)
        size = (max(1, int(prepared.width * scale)), target_h)
        cutout = prepared.resize(size, Image.Resampling.LANCZOS)
    return {
        "name": name,
        "title": title,
        "whatsapp": whatsapp,
        "instagram": instagram,
        "cutout": cutout,
    }
