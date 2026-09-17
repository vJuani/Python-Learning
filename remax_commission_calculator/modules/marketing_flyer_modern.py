"""Shared Marketing flyer helpers + V2-only layout resolver.

V1 templates (modern_premium_v1 / modern-editorial-v1) are retired.
New renders must use modern_commercial_v2 / premium_editorial_v2 / social_punch_v2.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from modules.marketing_context import MarketingError
from modules.marketing_renderer import (
    IG_PINK,
    WA_GREEN,
    WHITE,
)

MODERN_COMMERCIAL_V2 = "modern_commercial_v2"
PREMIUM_EDITORIAL_V2 = "premium_editorial_v2"
SOCIAL_PUNCH_V2 = "social_punch_v2"
V2_TEMPLATES = frozenset(
    {MODERN_COMMERCIAL_V2, PREMIUM_EDITORIAL_V2, SOCIAL_PUNCH_V2}
)

# Retired V1 / legacy names → migrate to V2 (never re-render with V1).
RETIRED_TEMPLATE_TO_V2 = {
    "modern_premium_v1": PREMIUM_EDITORIAL_V2,
    "modern_premium": PREMIUM_EDITORIAL_V2,
    "modern-premium-v1": PREMIUM_EDITORIAL_V2,
    "modern-editorial-v1": MODERN_COMMERCIAL_V2,
    "modern_editorial_v1": MODERN_COMMERCIAL_V2,
    "modern_editorial": MODERN_COMMERCIAL_V2,
    "modern-editorial": MODERN_COMMERCIAL_V2,
    "legacy": MODERN_COMMERCIAL_V2,
    "editorial": MODERN_COMMERCIAL_V2,
    "minimal_v1": PREMIUM_EDITORIAL_V2,
    "minimal": PREMIUM_EDITORIAL_V2,
    "light_premium": MODERN_COMMERCIAL_V2,
    "light": MODERN_COMMERCIAL_V2,
}

STYLE_TO_V2 = {
    "premium": PREMIUM_EDITORIAL_V2,
    "elegant": PREMIUM_EDITORIAL_V2,
    "minimal": PREMIUM_EDITORIAL_V2,
    "corporate": PREMIUM_EDITORIAL_V2,
    # Legacy visual buckets from normalize_style → commercial default
    "light_premium": MODERN_COMMERCIAL_V2,
    "light": MODERN_COMMERCIAL_V2,
    "blue_premium": MODERN_COMMERCIAL_V2,
    "commercial": MODERN_COMMERCIAL_V2,
    "modern": MODERN_COMMERCIAL_V2,
    "dynamic": SOCIAL_PUNCH_V2,
    "punch": SOCIAL_PUNCH_V2,
}

# Kept for import compatibility; always V2.
MODERN_PREMIUM_V1 = "modern_premium_v1"  # retired id — resolve migrates it
LEGACY_TEMPLATES = frozenset(RETIRED_TEMPLATE_TO_V2)
MODERN_FORMATS = frozenset({"flyer", "post", "story", "status"})
RENDERER_USED = "pillow_commercial_v2"
LAYOUT_VERSION = MODERN_COMMERCIAL_V2
DEFAULT_LAYOUT_TEMPLATE = MODERN_COMMERCIAL_V2


def explicit_layout_choice(options=None):
    options = options or {}
    return str(
        options.get("template")
        or options.get("layout_template")
        or options.get("template_used")
        or ""
    ).strip()


def resolve_layout_template(fmt, options=None):
    """Resolve a V2 template only. Retired V1 names migrate; never return V1."""
    del fmt  # all formats use the same V2 family
    options = options or {}
    template = explicit_layout_choice(options)
    if template in V2_TEMPLATES:
        return template
    if template in RETIRED_TEMPLATE_TO_V2:
        return RETIRED_TEMPLATE_TO_V2[template]
    if template:
        raise MarketingError("marketing_err_no_v2_template", 400)
    style = str(
        options.get("creative_style")
        or options.get("style")
        or options.get("visual_direction")
        or ""
    ).strip().lower()
    if style in STYLE_TO_V2:
        return STYLE_TO_V2[style]
    return MODERN_COMMERCIAL_V2


def require_v2_template(template):
    name = str(template or "").strip()
    if name in V2_TEMPLATES:
        return name
    if name in RETIRED_TEMPLATE_TO_V2:
        return RETIRED_TEMPLATE_TO_V2[name]
    raise MarketingError("marketing_err_no_v2_template", 400)


def uses_modern_premium(fmt, options=None):
    """Retired helper — always False. Kept so old imports do not crash."""
    del fmt, options
    return False


def _clip(text, limit):
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    clipped = value[: limit - 1].rsplit(" ", 1)[0].strip()
    return clipped or value[:limit]


def _hero_shade(width, height):
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = shade.load()
    band = max(1, int(width * 0.54))
    for x in range(band):
        t = x / float(band)
        if t < 0.20:
            fade = 0.96
        else:
            fade = (1.0 - (t - 0.20) / 0.80) ** 1.7
        alpha = int(198 * max(0.0, min(1.0, fade)))
        for y in range(height):
            pixels[x, y] = (8, 12, 22, alpha)
    return shade.filter(ImageFilter.GaussianBlur(radius=7))


def _icon_wa(draw, xy, size):
    x, y = xy
    draw.ellipse((x, y, x + size, y + size), fill=WHITE)
    inner = max(2, size // 8)
    draw.ellipse((x + inner, y + inner, x + size - inner, y + size - inner), fill=WA_GREEN)
    draw.polygon(
        (
            (x + size * 0.28, y + size * 0.70),
            (x + size * 0.16, y + size * 0.90),
            (x + size * 0.46, y + size * 0.74),
        ),
        fill=WA_GREEN,
    )
    draw.arc(
        (x + size * 0.28, y + size * 0.30, x + size * 0.72, y + size * 0.70),
        200,
        40,
        fill=WHITE,
        width=max(2, size // 10),
    )


def _icon_ig(draw, xy, size, *, fill=IG_PINK, stroke=WHITE):
    x, y = xy
    draw.rounded_rectangle((x, y, x + size, y + size), max(4, size // 4), fill=fill)
    cx, cy = x + size / 2, y + size / 2
    r = size * 0.20
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=stroke, width=max(2, size // 10))
    dot = max(2, size // 8)
    draw.ellipse((x + size * 0.68, y + size * 0.18, x + size * 0.68 + dot, y + size * 0.18 + dot), fill=stroke)


def _icon_sofa(draw, box, color):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 2, y0 + 10, x1 - 2, y1 - 6), 5, outline=color, width=2)
    draw.line((x0 + 2, y1 - 3, x1 - 2, y1 - 3), fill=color, width=2)
    mid = (x0 + x1) // 2
    draw.line((mid, y0 + 10, mid, y1 - 6), fill=color, width=2)


def _icon_bed(draw, box, color):
    x0, y0, x1, y1 = box
    draw.line((x0 + 2, y1 - 3, x1 - 2, y1 - 3), fill=color, width=2)
    draw.rounded_rectangle((x0 + 2, y0 + 12, x1 - 2, y1 - 6), 4, outline=color, width=2)
    draw.arc((x0 + 3, y0 + 2, x0 + (x1 - x0) * 0.58, y0 + 16), 200, 360, fill=color, width=2)


def _icon_bath(draw, box, color):
    x0, y0, x1, y1 = box
    draw.arc((x0 + 3, y0 + 8, x1 - 3, y1 - 1), 0, 180, fill=color, width=2)
    draw.line((x0 + 3, y0 + (y1 - y0) // 2 + 2, x1 - 3, y0 + (y1 - y0) // 2 + 2), fill=color, width=2)
    draw.line((x1 - 8, y0 + 3, x1 - 8, y0 + 11), fill=color, width=2)
    draw.ellipse((x1 - 12, y0 + 2, x1 - 4, y0 + 9), outline=color, width=2)


def _icon_area(draw, box, color):
    x0, y0, x1, y1 = box
    draw.rectangle((x0 + 3, y0 + 3, x1 - 3, y1 - 3), outline=color, width=2)
    draw.line((x0 + 3, y0 + 3, x1 - 3, y1 - 3), fill=color, width=1)
    draw.line((x1 - 3, y0 + 3, x0 + 3, y1 - 3), fill=color, width=1)


FEATURE_ICONS = (_icon_sofa, _icon_bed, _icon_bath, _icon_area)
