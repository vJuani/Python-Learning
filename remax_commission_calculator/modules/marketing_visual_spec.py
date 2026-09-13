"""JRH_MARKETING_VISUAL_SPEC — approved Creative AI direction of art.

The approved story is a STYLE QUALITY reference, not a pixel template.
Property, agent and facts are always live data.
"""

from __future__ import annotations

from pathlib import Path

SPEC_ID = "JRH_MARKETING_VISUAL_SPEC"
SPEC_VERSION = "3.0"
QUALITY_TARGET = "approved_jrh_story"

STYLE_DIR = Path(__file__).resolve().parent.parent / "marketing_style_references"
APPROVED_STYLE_NAME = "jrh-approved-story.jpg"

PALETTE = {
    "navy": (10, 22, 51),
    "ivory": (250, 247, 241),
    "electric": (13, 71, 255),
    "white": (255, 255, 255),
    "muted": (91, 107, 124),
    "ink": (17, 28, 51),
}

HIERARCHY = (
    "agency_logo",
    "office_name",
    "kicker",
    "property_hero",
    "operation_headline",
    "street",
    "locality",
    "key_facts",
    "price",
    "cta",
    "agent",
    "contact",
    "legal_broker",
)

COPY_DENSITY = "very_low"
MAX_CREATIVE_WORDS = 8
MAX_SECONDARY_PHOTOS = 2
MAX_STORY_ATTRIBUTES = 4
HERO_ATTENTION = (0.40, 0.60)
AGENT_ATTENTION = (0.12, 0.22)

STORY_ALLOWED_ELEMENTS = (
    "office_logo",
    "office_name",
    "tiny_kicker",
    "operation_headline",
    "street",
    "locality",
    "attributes_3_to_4",
    "price",
    "cta",
    "optional_agent",
    "legal_footer",
)

SAFE_AREA = {
    "story": {"left": 48, "right": 48, "top": 64, "bottom": 80},
    "status": {"left": 48, "right": 48, "top": 64, "bottom": 80},
    "post": {"left": 40, "right": 40, "top": 44, "bottom": 48},
    "flyer": {"left": 48, "right": 48, "top": 52, "bottom": 56},
}

SAFE_INSET = {
    "story": {"x": 48, "y": 64},
    "status": {"x": 48, "y": 64},
    "post": {"x": 40, "y": 44},
    "flyer": {"x": 48, "y": 52},
}

LIGHT_PREMIUM = "light_premium"
BLUE_PREMIUM = "blue_premium"
EDITORIAL_PREMIUM = LIGHT_PREMIUM
MODERN_COMMERCIAL = BLUE_PREMIUM
LUXURY_MINIMAL = LIGHT_PREMIUM

SHARED_LAYOUT = {
    "hero": "upper_editorial",
    "thumbs": "pair_support",
    "agent": "lower_right_small",
}

STYLE_ALIASES = {
    "light": LIGHT_PREMIUM,
    "light_premium": LIGHT_PREMIUM,
    "template_light": LIGHT_PREMIUM,
    "premium": LIGHT_PREMIUM,
    "elegant": LIGHT_PREMIUM,
    "elegante": LIGHT_PREMIUM,
    "editorial": LIGHT_PREMIUM,
    "editorial_premium": LIGHT_PREMIUM,
    "editorial_navy": LIGHT_PREMIUM,
    "luxury_editorial": LIGHT_PREMIUM,
    "minimal": LIGHT_PREMIUM,
    "minimalista": LIGHT_PREMIUM,
    "luxury": LIGHT_PREMIUM,
    "luxury_minimal": LIGHT_PREMIUM,
    "photo_led_luxury": LIGHT_PREMIUM,
    "property_hero": LIGHT_PREMIUM,
    "luxury_minimal_photo": LIGHT_PREMIUM,
    "blue": BLUE_PREMIUM,
    "blue_premium": BLUE_PREMIUM,
    "template_blue": BLUE_PREMIUM,
    "navy": BLUE_PREMIUM,
    "modern": BLUE_PREMIUM,
    "moderno": BLUE_PREMIUM,
    "commercial": BLUE_PREMIUM,
    "moderno_comercial": BLUE_PREMIUM,
    "modern_commercial": BLUE_PREMIUM,
    "white_architectural": BLUE_PREMIUM,
    "clean_collage": BLUE_PREMIUM,
    "bright_architectural": BLUE_PREMIUM,
    "bright_geometric": BLUE_PREMIUM,
    "price_led": BLUE_PREMIUM,
}

COMPOSITIONS = {
    LIGHT_PREMIUM: {
        **SHARED_LAYOUT,
        "theme": "light",
        "mood": "editorial, quiet luxury, magazine cover, ivory paper",
        "brief": (
            "Light Premium: one locked listing layout on an ivory field. "
            "Office logo + full office name top-left, short kicker top-right, "
            "one large hero, two equal secondary photos, commercial title, "
            "street + locality, four attribute icons, price, slim CTA, "
            "agent block, legal hairline. Navy ink, electric blue only on the CTA. "
            "No navy outer frame. No PowerPoint. Do not invent another composition."
        ),
    },
    BLUE_PREMIUM: {
        **SHARED_LAYOUT,
        "theme": "blue",
        "mood": "modern premium, deep navy field, cream type, electric accent",
        "brief": (
            "Blue Premium: the SAME locked listing layout as Light Premium. "
            "Only the palette changes: deep navy canvas, cream or white type, "
            "electric blue as a slim CTA accent. Keep it elegant and light, "
            "not a heavy PowerPoint slab. Do not invent another composition."
        ),
    },
}

STYLE_BRIEFS = {
    LIGHT_PREMIUM: (
        "LIGHT PREMIUM. Ivory or soft off-white canvas, never a navy plate. "
        "Same locked layout every time: logo+name, kicker, hero, two thumbs, "
        "operation title, address, facts, price, slim CTA, agent, legal. "
        "Navy ink, electric blue only on the CTA. Quiet, elegant, expensive."
    ),
    BLUE_PREMIUM: (
        "BLUE PREMIUM. Same locked layout as Light Premium. Deep navy canvas, "
        "cream or white type, electric blue only as a slim accent. "
        "Modern and a little bolder, still editorial and clean. "
        "Not PowerPoint, not a giant blue block wrapping a smaller card."
    ),
}

STYLE_REFERENCE_LABEL = (
    "This image is a VISUAL STYLE REFERENCE ONLY. "
    "Copy its level of polish and photographic quality. "
    "Do not copy its collage layout, side curve, thumbnail grid, "
    "vertical captions, edge-hugging type, or exact composition."
)

AVOID = (
    "SaaS UI",
    "generic template",
    "navy frame around a smaller card",
    "electric-blue enclosing slab",
    "PowerPoint navy slab",
    "PowerPoint collage",
    "amateur flyer",
    "tiny text",
    "empty canvas",
    "duplicate logos",
    "invented property imagery",
    "invented agent face",
    "giant HTML buttons",
    "dashboard cards",
    "icon rows",
    "too many frames",
    "decorative slogans",
    "vertical captions",
    "floating corner phrases",
    "four competing headlines",
    "text clipped outside the safe area",
    "logo flush to the edge",
    "agent name flush to the edge",
    "JRH One",
    "Inmobiliaria Principal",
    "generic office placeholders",
    "system product branding",
    "confusing the agent with the legal broker",
    "invented office logos",
)


def approved_style_path():
    path = STYLE_DIR / APPROVED_STYLE_NAME
    return path if path.is_file() else None


def normalize_style(style):
    key = str(style or LIGHT_PREMIUM).strip().lower()
    return STYLE_ALIASES.get(key, LIGHT_PREMIUM)


def is_blue_template(direction):
    return composition_key(direction) == BLUE_PREMIUM


def theme_palette(direction):
    if is_blue_template(direction):
        return {
            "theme": "blue",
            "field": PALETTE["navy"],
            "ink": PALETTE["ivory"],
            "mute": (168, 180, 198),
            "accent": PALETTE["electric"],
            "line": (48, 68, 110),
            "title": PALETTE["white"],
        }
    return {
        "theme": "light",
        "field": PALETTE["ivory"],
        "ink": PALETTE["ink"],
        "mute": PALETTE["muted"],
        "accent": PALETTE["electric"],
        "line": (226, 228, 222),
        "title": PALETTE["navy"],
    }


def composition_key(direction):
    return normalize_style(direction)


def safe_area(fmt):
    return dict(SAFE_AREA.get(fmt) or SAFE_AREA["story"])


def safe_inset(fmt):
    area = safe_area(fmt)
    return {
        "x": area["left"],
        "y": area["top"],
        "left": area["left"],
        "right": area["right"],
        "top": area["top"],
        "bottom": area["bottom"],
    }


def safe_rect(fmt, size):
    width, height = size
    area = safe_area(fmt)
    return (
        area["left"],
        area["top"],
        width - area["right"],
        height - area["bottom"],
    )


def format_from_size(size):
    width, height = size
    if (width, height) == (1080, 1920):
        return "story"
    if (width, height) == (1080, 1350):
        return "post"
    if (width, height) == (1240, 1754):
        return "flyer"
    ratio = float(height) / float(width or 1)
    if ratio >= 1.55:
        return "story"
    if ratio >= 1.30:
        return "flyer"
    return "post"


def composition_spec(direction):
    key = composition_key(direction)
    return COMPOSITIONS[key]


def build_visual_brief(fmt, direction, *, show_agent_photo=True):
    spec = composition_spec(direction)
    area = safe_area(fmt)
    if not show_agent_photo:
        agent = "no agent portrait"
    elif spec["agent"] == "lower_right_small":
        agent = (
            "small-to-medium real cutout, lower-right inside the safe area, "
            "never covering headline/price/CTA, never cropped at the edge"
        )
    elif spec["agent"] == "footer_compact":
        agent = "compact real cutout in the footer, name and short title fully visible"
    else:
        agent = "tiny real cutout in the footer, name fully visible, shrink if needed"
    return {
        "format": f"{fmt} vertical",
        "quality_target": QUALITY_TARGET,
        "composition": spec["brief"],
        "agent": agent,
        "text_density": COPY_DENSITY,
        "price": "prominent",
        "branding": (
            "navy field + cream type + electric blue accents"
            if spec["theme"] == "blue"
            else "ivory field + navy ink + electric blue accents"
        ),
        "mood": spec["mood"],
        "avoid": list(AVOID),
        "theme": spec["theme"],
        "hero": spec["hero"],
        "thumbs": spec["thumbs"],
        "agent_slot": spec["agent"],
        "safe_area": area,
        "hierarchy": list(HIERARCHY),
    }


def key_is_commercial(direction):
    return True


def safe_area_prompt(fmt):
    area = safe_area(fmt)
    return (
        f"STRICT SAFE AREA for {fmt}: keep EVERY word, office logo, price, CTA, agent "
        f"name and legal footer inside the inner rectangle. Minimum margins: left {area['left']}px, "
        f"right {area['right']}px, top {area['top']}px, bottom {area['bottom']}px. "
        "Nothing may touch or bleed past those margins. Keep those margins slim so the "
        "listing fills the canvas. Do not leave a wide empty frame around the ad."
    )
