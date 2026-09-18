"""modern_commercial_v3 — locked to the marketing_modern_reference layout.

Architecture:
  build_marketing_v3_context()
  select_visual_photos()
  compose_photo_base()     # Pillow: cover-crop / paste photos only
  build_marketing_svg()    # SVG chrome: type, overlays, CTA
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

FONT = "Segoe UI, Arial, Helvetica, DejaVu Sans, sans-serif"
NAVY = "#0e1c38"
INK = "#111c33"
MUTED = "#5b6b7c"
SOFT = "#8a96a4"
PAGE = "#f4f5f7"
WHITE = "#ffffff"
WA = "#25d366"
IG = "#c13584"

SCENE_CAPTIONS = {
    "living": ("LIVING", "Luz y amplitud"),
    "cocina": ("COCINA", "Diseño y funcionalidad"),
    "jardin": ("JARDÍN", "Ideal para disfrutar en familia"),
    "fachada": ("FACHADA", "Primera impresión"),
    "dormitorio": ("DORMITORIO", "Descanso y confort"),
}
SCENE_CAPTIONS_EN = {
    "living": ("LIVING", "Light and space"),
    "cocina": ("KITCHEN", "Design and function"),
    "jardin": ("GARDEN", "Made to enjoy outdoors"),
    "fachada": ("FACADE", "First impression"),
    "dormitorio": ("BEDROOM", "Rest and comfort"),
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
        canvas_size=size,
    )
    base = compose_photo_base(ctx)
    svg = build_marketing_svg(ctx)
    chrome_png = render_svg_to_png(svg, width=ctx["width"], height=ctx["height"])
    chrome = Image.open(io.BytesIO(chrome_png)).convert("RGBA")
    if chrome.size != (ctx["width"], ctx["height"]):
        chrome = chrome.resize((ctx["width"], ctx["height"]), Image.Resampling.LANCZOS)
    chrome = _knockout_svg_paper(chrome, ctx["hero_h"])
    out = Image.alpha_composite(base.convert("RGBA"), chrome)
    agent_block = ctx.get("agent")
    if agent_block and agent_block.get("cutout") is not None:
        cut = agent_block["cutout"]
        ax = ctx["u"](20)
        ay = ctx["agent_top"] + ctx["agent_h"] - cut.height + ctx["u"](4)
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
    canvas_size=None,
):
    facts = facts or {}
    copy = copy or {}
    options = options or {}
    language = language or "es"
    width, height = canvas_size or (CANVAS_W, CANVAS_H)
    bands = _bands(width, height)

    visual = select_visual_photos(
        photo_rows=photo_rows,
        photos=photos,
        fallback_hero=fallback_hero,
        options=options,
        canvas_size=(width, height),
        bands=bands,
        language=language,
    )
    headline_lines = _headline_lines(copy, facts, language)
    bajada = _short_benefit(copy, facts, language)
    currency, amount = _split_price(
        facts.get("price_label") if options.get("show_price", True) else ""
    )
    facts_items = _fact_items(facts, copy) if options.get("show_features", True) else []

    street = _street_line(facts, copy)
    zone = _zone_line(facts, copy)

    logo_img = _load_logo_image(facts)
    brand = facts.get("wordmark_text") or _office_brand(facts) or ""

    agent_block = None
    if options.get("include_agent") and agent:
        agent_block = _prepare_agent(agent, language, bands)

    location_benefit = _location_blurb(copy, facts, language)
    tagline = marketing_label("tagline", language) or ""
    tag_words = [part.strip() for part in re.split(r"[·|]", tagline) if part.strip()]

    return {
        "language": language,
        "width": width,
        "height": height,
        "u": bands["u"],
        "hero_h": bands["hero"],
        "gallery_h": bands["gallery"],
        "location_h": bands["location"],
        "agent_h": bands["agent"],
        "footer_h": bands["footer"],
        "gallery_top": bands["gallery_top"],
        "location_top": bands["location_top"],
        "agent_top": bands["agent_top"],
        "footer_top": bands["footer_top"],
        "kicker": default_kicker(language, facts),
        "headline_lines": headline_lines,
        "bajada": bajada,
        "facts": facts_items,
        "currency": currency or "USD",
        "amount": amount,
        "hero_image": visual["hero_image"],
        "gallery": visual["gallery"],
        "street": street,
        "zone": zone,
        "location_benefit": location_benefit,
        "logo_image": logo_img,
        "brand": _clip(brand, 32),
        "tag_words": [word.upper() for word in tag_words[:3]],
        "agent": agent_block,
        "cta": _clip(
            copy.get("cta")
            or ("Consultame para visitarla" if language != "en" else "Message me to visit"),
            42,
        ),
        "whisper": marketing_label("whisper", language) or "Hablemos de tu próximo hogar",
        "trust": (
            marketing_label("trust_advice", language),
            marketing_label("trust_support", language),
            marketing_label("trust_project", language),
        ),
        "office": _clip(
            " ".join(str(facts.get("brand_name") or facts.get("office_name") or brand or "").split()),
            36,
        ),
        "legal": _clip(
            " ".join(
                str(facts.get("broker_footer_text") or facts.get("legal_footer_line") or "").split()
            ),
            86,
        ),
        "show_price": bool(amount),
    }


def select_visual_photos(
    *,
    photo_rows=None,
    photos=None,
    fallback_hero=None,
    options=None,
    canvas_size=None,
    bands=None,
    language="es",
):
    options = options or {}
    width, height = canvas_size or (CANVAS_W, CANVAS_H)
    bands = bands or _bands(width, height)
    u = bands["u"]
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
    pad = u(40)
    gap = u(14)
    caption_h = u(56)
    cell_w = (width - pad * 2 - gap * 2) // 3
    cell_h = max(u(120), bands["gallery"] - caption_h - u(16))
    captions = SCENE_CAPTIONS_EN if language == "en" else SCENE_CAPTIONS

    gallery = []
    used = set()
    for index, (img, row) in enumerate(selected_images[1:4]):
        scene = photo_scene_label(row) or DEFAULT_SCENES[min(index, 2)]
        if scene in used and len(selected_images) > 4:
            continue
        used.add(scene)
        title, subtitle = captions.get(scene, captions[DEFAULT_SCENES[min(index, 2)]])
        gallery.append(
            {
                "image": fit_cover(img, cell_w, cell_h, focus=(0.5, 0.42)),
                "label": title,
                "subtitle": subtitle,
                "w": cell_w,
                "h": cell_h,
            }
        )
    while len(gallery) < min(3, max(0, len(selected_images) - 1)):
        src = selected_images[min(len(gallery) + 1, len(selected_images) - 1)][0]
        scene = DEFAULT_SCENES[len(gallery) % 3]
        title, subtitle = captions[scene]
        gallery.append(
            {
                "image": fit_cover(src, cell_w, cell_h, focus=(0.5, 0.42)),
                "label": title,
                "subtitle": subtitle,
                "w": cell_w,
                "h": cell_h,
            }
        )

    return {
        "hero_image": fit_cover(hero_img, width, bands["hero"], focus=(0.52, 0.32)),
        "gallery": gallery[:3],
    }


def compose_photo_base(ctx):
    """Pillow photo plane: cover crops, light wash, gallery thumbs, logo."""
    width, height = ctx["width"], ctx["height"]
    canvas = Image.new("RGBA", (width, height), (*_hex_rgb(PAGE), 255))
    hero = ctx.get("hero_image")
    if hero is not None:
        canvas.paste(hero.convert("RGB"), (0, 0))
    else:
        ImageDraw.Draw(canvas).rectangle((0, 0, width, ctx["hero_h"]), fill=_hex_rgb(WHITE))
    wash = _hero_light_wash(width, ctx["hero_h"])
    canvas.paste(wash, (0, 0), wash)

    pad = ctx["u"](40)
    gap = ctx["u"](14)
    y = ctx["gallery_top"] + ctx["u"](10)
    x = pad
    radius = ctx["u"](16)
    for item in ctx.get("gallery") or []:
        thumb = item["image"].convert("RGB")
        rounded = _round_corners(thumb, radius)
        canvas.paste(rounded, (x, y), rounded)
        x += item["w"] + gap

    logo = ctx.get("logo_image")
    if logo is not None:
        mark = logo.convert("RGBA")
        mark.thumbnail((ctx["u"](52), ctx["u"](52)), Image.Resampling.LANCZOS)
        canvas.paste(mark, (ctx["u"](36), ctx["u"](16)), mark)

    return canvas


def build_marketing_svg(ctx):
    width, height = ctx["width"], ctx["height"]
    return "".join(
        [
            f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}">
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
    u = ctx["u"]
    pad = u(44)
    chunks = ['  <g id="hero">\n']
    brand = ctx.get("brand") or ""
    if brand:
        chunks.append(
            f'    <text x="{u(100)}" y="{u(50)}" font-family="{FONT}" font-size="{u(17)}" '
            f'font-weight="700" fill="{INK}" letter-spacing="0.6">{_esc(brand.upper())}</text>\n'
        )

    words = ctx.get("tag_words") or []
    if words:
        ty = u(28)
        for word in words:
            tw = int(len(word) * u(11) * 0.62 + 1.6 * max(0, len(word) - 1))
            chunks.append(
                f'    <text x="{ctx["width"] - u(40) - tw}" y="{ty}" font-family="{FONT}" '
                f'font-size="{u(11)}" font-weight="700" fill="{MUTED}" '
                f'letter-spacing="1.6">{_esc(word)}</text>\n'
            )
            ty += u(16)

    y = u(118)
    kicker = ctx.get("kicker") or ""
    if kicker:
        chunks.append(
            f'    <text x="{pad}" y="{y}" font-family="{FONT}" font-size="{u(15)}" '
            f'font-weight="700" fill="{INK}" letter-spacing="3.4">{_esc(kicker)}</text>\n'
        )
        y += u(54)

    lines = ctx.get("headline_lines") or []
    title_px = _headline_px(lines, u)
    for line in lines[:3]:
        chunks.append(
            f'    <text x="{pad}" y="{y}" font-family="{FONT}" font-size="{title_px}" '
            f'font-weight="800" fill="{INK}">{_esc(line)}</text>\n'
        )
        y += int(title_px * 0.98)
    y += u(10)

    bajada = ctx.get("bajada") or ""
    if bajada:
        for line in _wrap_chars(bajada, 38)[:2]:
            chunks.append(
                f'    <text x="{pad}" y="{y}" font-family="{FONT}" font-size="{u(20)}" '
                f'fill="{MUTED}">{_esc(line)}</text>\n'
            )
            y += u(28)

    chunks.append(render_svg_facts(ctx, pad=pad, bottom=ctx["hero_h"] - u(36)))
    if ctx.get("show_price"):
        chunks.append(render_svg_price(ctx, right=ctx["width"] - u(40), bottom=ctx["hero_h"] - u(36)))
    chunks.append("  </g>\n")
    return "".join(chunks)


def render_svg_facts(ctx, *, pad, bottom):
    items = ctx.get("facts") or []
    if not items:
        return ""
    u = ctx["u"]
    x = pad
    y = bottom
    icon = u(28)
    chunks = ['    <g id="facts">\n']
    for number, label, kind in items[:4]:
        chunks.append(_svg_fact_icon(kind, x, y - icon - u(52), icon, INK))
        top = f"{number}".strip()
        low = _title_label(label)
        if top:
            chunks.append(
                f'      <text x="{x}" y="{y - 20}" font-family="{FONT}" font-size="{u(26)}" '
                f'font-weight="700" fill="{INK}">{_esc(top)}</text>\n'
                f'      <text x="{x}" y="{y}" font-family="{FONT}" font-size="{u(14)}" '
                f'fill="{MUTED}">{_esc(low)}</text>\n'
            )
            x += max(u(148), max(len(top), len(low)) * u(9) + u(28))
        else:
            chunks.append(
                f'      <text x="{x}" y="{y - 8}" font-family="{FONT}" font-size="{u(16)}" '
                f'font-weight="700" fill="{INK}">{_esc(low)}</text>\n'
            )
            x += len(low) * u(9) + u(28)
    chunks.append("    </g>\n")
    return "".join(chunks)


def render_svg_price(ctx, *, right, bottom):
    currency = ctx.get("currency") or "USD"
    amount = ctx.get("amount") or ""
    if not amount:
        return ""
    u = ctx["u"]
    amount_px = u(64)
    # PyMuPDF can ignore text-anchor; place from the left of the block.
    block_w = max(u(160), int(len(amount) * amount_px * 0.58))
    x = right - block_w
    return (
        f'    <g id="price">\n'
        f'      <text x="{x}" y="{bottom - u(62)}" font-family="{FONT}" font-size="{u(18)}" '
        f'font-weight="700" fill="{MUTED}" letter-spacing="2">{_esc(currency)}</text>\n'
        f'      <text x="{x}" y="{bottom}" font-family="{FONT}" font-size="{amount_px}" '
        f'font-weight="800" fill="{INK}">{_esc(amount)}</text>\n'
        f'    </g>\n'
    )


def render_svg_gallery(ctx):
    top = ctx["gallery_top"]
    u = ctx["u"]
    pad = u(40)
    gap = u(14)
    chunks = [
        f'  <g id="gallery" transform="translate(0,{top})">\n',
    ]
    x = pad
    y = u(10)
    for item in ctx.get("gallery") or []:
        w, h = item["w"], item["h"]
        chunks.append(
            f'    <text x="{x}" y="{y + h + u(22)}" font-family="{FONT}" font-size="{u(15)}" '
            f'font-weight="700" fill="{INK}" letter-spacing="0.8">{_esc(item.get("label") or "")}</text>\n'
            f'    <text x="{x}" y="{y + h + u(42)}" font-family="{FONT}" font-size="{u(13)}" '
            f'fill="{MUTED}">{_esc(item.get("subtitle") or "")}</text>\n'
        )
        x += w + gap
    chunks.append("  </g>\n")
    return "".join(chunks)


def render_svg_location(ctx):
    top = ctx["location_top"]
    u = ctx["u"]
    pad = u(40)
    chunks = [
        f'  <g id="location" transform="translate(0,{top})">\n',
        f'    <rect x="0" y="0" width="{ctx["width"]}" height="{ctx["location_h"]}" fill="{WHITE}"/>\n',
        _svg_pin(pad + u(18), u(48), u(36)),
        f'    <text x="{pad + u(58)}" y="{u(50)}" font-family="{FONT}" font-size="{u(24)}" '
        f'font-weight="700" fill="{INK}">{_esc(ctx.get("street") or "")}</text>\n',
        f'    <text x="{pad + u(58)}" y="{u(78)}" font-family="{FONT}" font-size="{u(16)}" '
        f'fill="{MUTED}">{_esc(ctx.get("zone") or "")}</text>\n',
        _svg_tree(u(560), u(36), u(28)),
    ]
    benefit = ctx.get("location_benefit") or ""
    bx = u(600)
    for i, line in enumerate(_wrap_chars(benefit, 34)[:4]):
        chunks.append(
            f'    <text x="{bx}" y="{u(48) + i * u(22)}" font-family="{FONT}" font-size="{u(15)}" '
            f'fill="{INK}">{_esc(line)}</text>\n'
        )
    chunks.append("  </g>\n")
    return "".join(chunks)


def render_svg_agent_cta(ctx):
    top = ctx["agent_top"]
    u = ctx["u"]
    pad = u(28)
    chunks = [
        f'  <g id="agent" transform="translate(0,{top})">\n',
        f'    <rect x="0" y="0" width="{ctx["width"]}" height="{ctx["agent_h"]}" fill="{WHITE}"/>\n',
    ]
    agent = ctx.get("agent")
    whisper = ctx.get("whisper") or ""
    if whisper:
        wy = u(36)
        for line in _wrap_chars(whisper, 16)[:3]:
            chunks.append(
                f'    <text x="{u(36)}" y="{wy}" font-family="{FONT}" font-size="{u(20)}" '
                f'font-style="italic" fill="#c5ced8">{_esc(line)}</text>\n'
            )
            wy += u(26)

    text_x = pad + u(220)
    if agent and agent.get("cutout") is not None:
        text_x = pad + max(u(220), int(agent["cutout"].width * 0.58) + u(16))
    elif agent:
        text_x = u(220)

    if agent:
        chunks.append(
            f'    <text x="{text_x}" y="{u(72)}" font-family="{FONT}" font-size="{u(26)}" '
            f'font-weight="700" fill="{INK}">{_esc(agent.get("name") or "")}</text>\n'
            f'    <text x="{text_x}" y="{u(100)}" font-family="{FONT}" font-size="{u(16)}" '
            f'fill="{MUTED}">{_esc(agent.get("title") or "")}</text>\n'
        )
        cy = u(136)
        if agent.get("whatsapp"):
            chunks.append(_contact_row(text_x, cy, "wa", agent["whatsapp"], u))
            cy += u(32)
        if agent.get("instagram"):
            chunks.append(_contact_row(text_x, cy, "ig", agent["instagram"], u))
            cy += u(32)
        if agent.get("email"):
            chunks.append(_contact_row(text_x, cy, "mail", agent["email"], u))
    else:
        chunks.append(
            f'    <text x="{pad + u(36)}" y="{u(96)}" font-family="{FONT}" font-size="{u(24)}" '
            f'font-weight="700" fill="{INK}">Consultá con la oficina</text>\n'
        )

    chunks.append(render_svg_cta(ctx))
    chunks.append("  </g>\n")
    return "".join(chunks)


def render_svg_cta(ctx):
    u = ctx["u"]
    label = " ".join(str(ctx.get("cta") or "Consultame para visitarla").split())
    if not label.endswith("→"):
        label = f"{label} →"
    btn_w = u(400)
    btn_h = u(78)
    bx = ctx["width"] - u(40) - btn_w
    by = u(48)
    icon_cx = bx + u(40)
    icon_cy = by + btn_h // 2
    chunks = [
        '    <g id="cta">\n',
        f'      <rect x="{bx}" y="{by}" width="{btn_w}" height="{btn_h}" rx="{btn_h // 2}" '
        f'fill="{NAVY}"/>\n',
        f'      <circle cx="{icon_cx}" cy="{icon_cy}" r="{u(18)}" fill="none" stroke="{WHITE}" '
        f'stroke-width="2"/>\n',
        _svg_phone(icon_cx, icon_cy, u(10), WHITE),
        f'      <text x="{bx + u(70)}" y="{by + btn_h // 2 + u(7)}" font-family="{FONT}" '
        f'font-size="{u(18)}" font-weight="700" fill="{WHITE}">{_esc(label)}</text>\n',
    ]
    trust = [item for item in (ctx.get("trust") or ()) if item]
    col_w = btn_w // max(1, len(trust[:3]))
    ty = by + btn_h + u(28)
    for index, line in enumerate(trust[:3]):
        cx = bx + col_w * index + col_w // 2
        chunks.append(_svg_trust_icon(index, cx, ty - u(4), u(16)))
        wrapped = _wrap_chars(line, 16)[:2]
        for i, part in enumerate(wrapped):
            chunks.append(
                f'      <text x="{cx}" y="{ty + u(18) + i * u(14)}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="{u(11)}" fill="{MUTED}">{_esc(part)}</text>\n'
            )
    chunks.append("    </g>\n")
    return "".join(chunks)


def render_svg_footer(ctx):
    top = ctx["footer_top"]
    u = ctx["u"]
    pad = u(32)
    return (
        f'  <g id="footer" transform="translate(0,{top})">\n'
        f'    <rect x="0" y="0" width="{ctx["width"]}" height="{ctx["footer_h"]}" fill="{WHITE}"/>\n'
        f'    <text x="{pad}" y="{u(36)}" font-family="{FONT}" font-size="{u(12)}" font-weight="700" '
        f'fill="{INK}">{_esc(ctx.get("office") or "")}</text>\n'
        f'    <text x="{ctx["width"] / 2}" y="{u(36)}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="{u(10)}" fill="{MUTED}">{_esc(ctx.get("legal") or "")}</text>\n'
        f'    <text x="{ctx["width"] - pad}" y="{u(36)}" text-anchor="end" font-family="{FONT}" '
        f'font-size="{u(12)}" font-weight="700" fill="{INK}">JRH One</text>\n'
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


def _bands(width, height):
    def u(value):
        return max(1, int(round(value * width / 1080.0)))

    hero = int(height * 0.42)
    gallery = int(height * 0.175)
    location = int(height * 0.10)
    agent = int(height * 0.225)
    footer = height - hero - gallery - location - agent
    if footer < u(48):
        steal = u(48) - footer
        hero = max(u(420), hero - steal)
        footer = height - hero - gallery - location - agent
    return {
        "u": u,
        "hero": hero,
        "gallery": gallery,
        "location": location,
        "agent": agent,
        "footer": footer,
        "gallery_top": hero,
        "location_top": hero + gallery,
        "agent_top": hero + gallery + location,
        "footer_top": hero + gallery + location + agent,
    }


def _knockout_svg_paper(image, hero_h):
    """Knock out the white SVG page in the hero so the photo shows through."""
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    limit = min(height, max(1, int(hero_h)))
    for y in range(limit):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a and r >= 252 and g >= 252 and b >= 252:
                pixels[x, y] = (r, g, b, 0)
    return rgba


def _hero_light_wash(width, height):
    """Soft white column so dark editorial type stays readable on any photo."""
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = shade.load()
    band = int(width * 0.58)
    for x in range(band):
        t = x / float(max(1, band - 1))
        if t < 0.16:
            fade = 0.48
        elif t < 0.50:
            fade = 0.48 - (t - 0.16) * 1.15
        else:
            fade = max(0.0, (1.0 - t) * 0.28)
        alpha = int(175 * max(0.0, min(1.0, fade)))
        for y in range(height):
            pixels[x, y] = (247, 248, 250, alpha)
    top_band = int(height * 0.10)
    for y in range(top_band):
        t = 1.0 - y / float(max(1, top_band - 1))
        alpha = int(90 * t)
        for x in range(width):
            r, g, b, a = pixels[x, y]
            pixels[x, y] = (247, 248, 250, min(220, a + alpha))
    return shade


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


def _title_label(label):
    text = " ".join(str(label or "").split())
    if not text:
        return ""
    if text.casefold() in {"m2", "m²"} or "m²" in text:
        return "m²"
    if text.casefold().endswith("m2"):
        return "m²"
    return " ".join(word[:1].upper() + word[1:] if word else "" for word in text.split())


def _headline_lines(copy, facts, language):
    raw = " ".join(str((copy or {}).get("headline") or "").split())
    if raw and not is_placeholder_copy(raw):
        return _stack_headline(raw, language)[:3]
    return _stack_headline(" ".join(sellable_headline_lines(language, facts)), language)[:3]


def _stack_headline(text, language):
    """Editorial hero stack: Casa / moderna / en Martínez."""
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
            return [*left_words, right]
        mid = (len(left_words) + 1) // 2
        return [" ".join(left_words[:mid]), " ".join(left_words[mid:]), right]
    words = text.split()
    if len(words) <= 3:
        return words
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _headline_px(lines, u):
    n = max((len(line) for line in (lines or ["x"])), default=1)
    count = len(lines or [])
    if n <= 12 and count <= 3:
        return u(78)
    if n <= 16:
        return u(68)
    if n <= 20:
        return u(60)
    return u(54)


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
        return _clip(raw, 96)
    return _clip(listing_benefit_line(language, facts), 96)


def _location_blurb(copy, facts, language):
    raw = " ".join(
        str(
            (copy or {}).get("location_benefit")
            or (facts or {}).get("location_blurb")
            or ""
        ).split()
    )
    if raw and not is_placeholder_copy(raw):
        return _clip(raw, 140)
    return _clip(marketing_label("location_blurb", language), 140)


def _street_line(facts, copy):
    street = " ".join(
        str((copy or {}).get("street") or facts.get("title") or facts.get("address") or "").split()
    )
    postal = " ".join(str(facts.get("postal_code") or "").split())
    if postal and postal.casefold() not in street.casefold():
        street = f"{street} ({postal})" if street else postal
    return _clip(street, 44)


def _zone_line(facts, copy):
    raw = " ".join(str((copy or {}).get("zone") or "").split())
    if raw:
        return _clip(raw.replace(" · ", ", "), 56)
    parts = []
    for key in ("neighborhood", "locality", "jurisdiction", "administrative_area"):
        value = " ".join(str(facts.get(key) or "").split())
        if value and value not in parts:
            parts.append(value)
    if not parts:
        line = " ".join(str(facts.get("location_line") or facts.get("zone_line") or "").split())
        return _clip(line.replace(" · ", ", "), 56)
    return _clip(", ".join(parts), 56)


def _fact_items(facts, copy):
    chips = (facts or {}).get("chips") or (copy or {}).get("attributes") or []
    items = []
    used = set()
    for chip in chips[:4]:
        number, label = _parse_chip(chip)
        kind = _fact_icon_kind(label or str(chip), used)
        used.add(kind)
        items.append((number or "", label or str(chip), kind))
    return items


def _fact_icon_kind(label, used):
    folded = str(label or "").casefold()
    if any(token in folded for token in ("dorm", "bed", "cuarto")) and "bed" not in used:
        return "bed"
    if any(token in folded for token in ("baño", "bano", "bath")) and "bath" not in used:
        return "bath"
    if any(token in folded for token in ("m²", "m2", "superficie")) and "area" not in used:
        return "area"
    if any(token in folded for token in ("amb", "room", "ambiente")) and "sofa" not in used:
        return "sofa"
    for kind in ("bed", "sofa", "bath", "area"):
        if kind not in used:
            return kind
    return "area"


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


def _contact_row(x, y, kind, label, u):
    if kind == "wa":
        icon = (
            f'<circle cx="{x + u(11)}" cy="{y}" r="{u(11)}" fill="{WA}"/>'
            f'<text x="{x + u(11)}" y="{y + u(4)}" text-anchor="middle" font-size="{u(10)}" '
            f'font-weight="700" fill="{WHITE}">W</text>'
        )
    elif kind == "ig":
        icon = (
            f'<rect x="{x}" y="{y - u(11)}" width="{u(22)}" height="{u(22)}" rx="{u(6)}" fill="{IG}"/>'
            f'<circle cx="{x + u(11)}" cy="{y}" r="{u(5)}" fill="none" stroke="{WHITE}" stroke-width="2"/>'
        )
    else:
        icon = (
            f'<rect x="{x}" y="{y - u(9)}" width="{u(22)}" height="{u(16)}" rx="{u(3)}" fill="none" '
            f'stroke="{INK}" stroke-width="1.8"/>'
            f'<path d="M{x + 1} {y - u(8)} L{x + u(11)} {y} L{x + u(21)} {y - u(8)}" '
            f'fill="none" stroke="{INK}" stroke-width="1.8"/>'
        )
    return (
        f'    <g>{icon}<text x="{x + u(32)}" y="{y + u(5)}" font-family="{FONT}" font-size="{u(16)}" '
        f'fill="{INK}">{_esc(label)}</text></g>\n'
    )


def _svg_fact_icon(kind, x, y, size, color):
    s = size
    if kind == "bed":
        return (
            f'      <g fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round">\n'
            f'        <path d="M{x + s * 0.08:.1f} {y + s * 0.72:.1f} H{x + s * 0.92:.1f}"/>\n'
            f'        <path d="M{x + s * 0.12:.1f} {y + s * 0.72:.1f} V{y + s * 0.42:.1f} '
            f'q{s * 0.16:.1f} -{s * 0.22:.1f} {s * 0.32:.1f} 0 V{y + s * 0.72:.1f}"/>\n'
            f'        <rect x="{x + s * 0.12:.1f}" y="{y + s * 0.48:.1f}" width="{s * 0.76:.1f}" '
            f'height="{s * 0.24:.1f}" rx="3"/>\n'
            f"      </g>\n"
        )
    if kind == "sofa":
        return (
            f'      <g fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round">\n'
            f'        <rect x="{x + s * 0.08:.1f}" y="{y + s * 0.38:.1f}" width="{s * 0.84:.1f}" '
            f'height="{s * 0.36:.1f}" rx="4"/>\n'
            f'        <path d="M{x + s * 0.08:.1f} {y + s * 0.78:.1f} H{x + s * 0.92:.1f}"/>\n'
            f'        <path d="M{x + s * 0.50:.1f} {y + s * 0.38:.1f} V{y + s * 0.74:.1f}"/>\n'
            f"      </g>\n"
        )
    if kind == "bath":
        return (
            f'      <g fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round">\n'
            f'        <path d="M{x + s * 0.12:.1f} {y + s * 0.52:.1f} H{x + s * 0.88:.1f} '
            f'A{s * 0.22:.1f} {s * 0.22:.1f} 0 0 1 {x + s * 0.12:.1f} {y + s * 0.52:.1f}"/>\n'
            f'        <path d="M{x + s * 0.78:.1f} {y + s * 0.52:.1f} V{y + s * 0.22:.1f}"/>\n'
            f'        <circle cx="{x + s * 0.78:.1f}" cy="{y + s * 0.18:.1f}" r="{s * 0.06:.1f}"/>\n'
            f"      </g>\n"
        )
    return (
        f'      <g fill="none" stroke="{color}" stroke-width="2">\n'
        f'        <rect x="{x + s * 0.16:.1f}" y="{y + s * 0.16:.1f}" width="{s * 0.68:.1f}" '
        f'height="{s * 0.68:.1f}"/>\n'
        f"      </g>\n"
    )


def _svg_pin(cx, cy, size):
    return (
        f'    <circle cx="{cx}" cy="{cy}" r="{size * 0.48:.1f}" fill="{NAVY}"/>\n'
        f'    <circle cx="{cx}" cy="{cy - size * 0.06:.1f}" r="{size * 0.14:.1f}" fill="none" '
        f'stroke="{WHITE}" stroke-width="2.2"/>\n'
        f'    <path d="M{cx} {cy + size * 0.02:.1f} L{cx} {cy + size * 0.22:.1f}" stroke="{WHITE}" '
        f'stroke-width="2.2" stroke-linecap="round"/>\n'
    )


def _svg_tree(x, y, size):
    cx = x + size * 0.5
    return (
        f'    <g fill="none" stroke="{NAVY}" stroke-width="2.2" stroke-linecap="round" '
        f'stroke-linejoin="round">\n'
        f'      <path d="M{cx:.1f} {y + size:.1f} V{y + size * 0.58:.1f}"/>\n'
        f'      <path d="M{cx:.1f} {y + size * 0.22:.1f} L{x + size * 0.12:.1f} {y + size * 0.58:.1f} '
        f'H{x + size * 0.88:.1f} Z"/>\n'
        f'      <path d="M{cx:.1f} {y:.1f} L{x + size * 0.22:.1f} {y + size * 0.36:.1f} '
        f'H{x + size * 0.78:.1f} Z"/>\n'
        f"    </g>\n"
    )


def _svg_phone(cx, cy, r, color):
    return (
        f'      <path d="M{cx - r * 0.45:.1f} {cy - r * 0.15:.1f} '
        f'q{r * 0.15:.1f} -{r * 0.55:.1f} {r * 0.7:.1f} -{r * 0.2:.1f} '
        f'l-{r * 0.18:.1f} {r * 0.28:.1f} q{r * 0.35:.1f} {r * 0.35:.1f} {r * 0.7:.1f} {r * 0.08:.1f} '
        f'l{r * 0.2:.1f} {r * 0.28:.1f} q-{r * 0.55:.1f} {r * 0.35:.1f} -{r * 1.05:.1f} -{r * 0.15:.1f} '
        f'q-{r * 0.45:.1f} -{r * 0.4:.1f} -{r * 0.37:.1f} -{r * 1.14:.1f} z" fill="{color}"/>\n'
    )


def _svg_trust_icon(index, cx, cy, size):
    if index == 0:
        return (
            f'      <g fill="none" stroke="{NAVY}" stroke-width="1.7" stroke-linecap="round">\n'
            f'        <path d="M{cx - size:.1f} {cy} q{size * 0.4:.1f} -{size * 0.55:.1f} '
            f'{size:.1f} 0 q{size * 0.4:.1f} {size * 0.55:.1f} {size:.1f} 0"/>\n'
            f"      </g>\n"
        )
    if index == 1:
        return (
            f'      <g fill="none" stroke="{NAVY}" stroke-width="1.7">\n'
            f'        <path d="M{cx} {cy + size * 0.7:.1f} L{cx - size:.1f} {cy} '
            f'L{cx} {cy - size * 0.55:.1f} L{cx + size:.1f} {cy} Z"/>\n'
            f"      </g>\n"
        )
    return (
        f'      <g fill="none" stroke="{NAVY}" stroke-width="1.7">\n'
        f'        <circle cx="{cx}" cy="{cy - size * 0.25:.1f}" r="{size * 0.28:.1f}"/>\n'
        f'        <path d="M{cx - size * 0.7:.1f} {cy + size * 0.65:.1f} '
        f'q{size * 0.7:.1f} -{size * 0.7:.1f} {size * 1.4:.1f} 0"/>\n'
        f"      </g>\n"
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


def _prepare_agent(agent, language, bands):
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
    email = " ".join(str(agent.get("email") or "").split())
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram.lstrip('@')}"
    cutout = None
    raw = load_agent_photo(agent.get("photo_path")) if agent.get("photo_path") else None
    if raw is not None:
        prepared = prepare_agent_cutout(raw) or raw.convert("RGBA")
        target_h = max(bands["u"](200), int(bands["agent"] * 0.92))
        scale = target_h / float(prepared.height or 1)
        size = (max(1, int(prepared.width * scale)), target_h)
        cutout = prepared.resize(size, Image.Resampling.LANCZOS)
    return {
        "name": name,
        "title": title,
        "whatsapp": whatsapp,
        "instagram": instagram,
        "email": email,
        "cutout": cutout,
    }
