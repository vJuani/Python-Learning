"""JRH_MARKETING_VISUAL_SPEC — approved Creative AI direction of art.

The approved story is a STYLE QUALITY reference, not a pixel template.
Property, agent and facts are always live data.
"""

from __future__ import annotations

from pathlib import Path

SPEC_ID = "JRH_MARKETING_VISUAL_SPEC"
SPEC_VERSION = "2.0"
QUALITY_TARGET = "approved_jrh_story"

STYLE_DIR = Path(__file__).resolve().parent.parent / "marketing_style_references"
APPROVED_STYLE_NAME = "jrh-approved-story.jpg"

PALETTE = {
    "navy": (10, 22, 51),
    "navy_deep": (6, 14, 32),
    "electric": (13, 71, 255),
    "white": (255, 255, 255),
    "muted": (176, 190, 214),
    "ink": (17, 28, 51),
}

HIERARCHY = (
    "logo",
    "headline",
    "property_hero",
    "secondary_photos",
    "key_facts",
    "price",
    "cta",
    "agent",
)

COPY_DENSITY = "very_low"
MAX_CREATIVE_WORDS = 8
MAX_SECONDARY_PHOTOS = 2
MAX_STORY_ATTRIBUTES = 4
HERO_ATTENTION = (0.40, 0.60)
AGENT_ATTENTION = (0.12, 0.22)

STORY_ALLOWED_ELEMENTS = (
    "logo",
    "short_headline",
    "short_address_or_zone",
    "attributes_3_to_4",
    "price",
    "cta",
    "optional_agent",
)

SAFE_AREA = {
    "story": {"left": 80, "right": 80, "top": 120, "bottom": 180},
    "status": {"left": 80, "right": 80, "top": 120, "bottom": 180},
    "post": {"left": 70, "right": 70, "top": 70, "bottom": 70},
    "flyer": {"left": 80, "right": 80, "top": 80, "bottom": 80},
}

SAFE_INSET = {
    "story": {"x": 80, "y": 120},
    "status": {"x": 80, "y": 120},
    "post": {"x": 70, "y": 70},
    "flyer": {"x": 80, "y": 80},
}

EDITORIAL_PREMIUM = "editorial_premium"
MODERN_COMMERCIAL = "modern_commercial"
LUXURY_MINIMAL = "luxury_minimal"

STYLE_ALIASES = {
    "premium": EDITORIAL_PREMIUM,
    "elegant": EDITORIAL_PREMIUM,
    "elegante": EDITORIAL_PREMIUM,
    "editorial": EDITORIAL_PREMIUM,
    "editorial_premium": EDITORIAL_PREMIUM,
    "editorial_navy": EDITORIAL_PREMIUM,
    "luxury_editorial": EDITORIAL_PREMIUM,
    "modern": MODERN_COMMERCIAL,
    "moderno": MODERN_COMMERCIAL,
    "commercial": MODERN_COMMERCIAL,
    "moderno_comercial": MODERN_COMMERCIAL,
    "modern_commercial": MODERN_COMMERCIAL,
    "white_architectural": MODERN_COMMERCIAL,
    "clean_collage": MODERN_COMMERCIAL,
    "bright_architectural": MODERN_COMMERCIAL,
    "bright_geometric": MODERN_COMMERCIAL,
    "price_led": MODERN_COMMERCIAL,
    "minimal": LUXURY_MINIMAL,
    "minimalista": LUXURY_MINIMAL,
    "luxury": LUXURY_MINIMAL,
    "luxury_minimal": LUXURY_MINIMAL,
    "photo_led_luxury": LUXURY_MINIMAL,
    "property_hero": LUXURY_MINIMAL,
    "luxury_minimal_photo": LUXURY_MINIMAL,
}

COMPOSITIONS = {
    EDITORIAL_PREMIUM: {
        "theme": "dark",
        "hero": "upper_editorial",
        "thumbs": "pair_support",
        "agent": "lower_right_small",
        "mood": "editorial, quiet luxury, magazine cover",
        "brief": (
            "Editorial Premium: one dominant hero photograph, generous negative space, "
            "very little type. At most one or two small supporting photos. "
            "Elegant navy/white. No collage, no icon rows, no competing headlines."
        ),
    },
    MODERN_COMMERCIAL: {
        "theme": "light",
        "hero": "hero_plus_offer",
        "thumbs": "one_or_two",
        "agent": "footer_compact",
        "mood": "modern, commercial, high-clarity selling",
        "brief": (
            "Modern Commercial: sales-first layout. Hero photo, clearly readable price, "
            "one strong CTA, one or two supporting photos. Clean geometry, high contrast, "
            "still premium — never a coupon flyer or PowerPoint."
        ),
    },
    LUXURY_MINIMAL: {
        "theme": "dark",
        "hero": "full_bleed_inset",
        "thumbs": "none_or_one",
        "agent": "tiny_footer",
        "mood": "sober, minimal, image-led luxury",
        "brief": (
            "Luxury Minimal: almost all image, almost no type. Fine branding, "
            "a short address or zone, optional price. One hero, optional one secondary. "
            "No frames, no icons, no decorative slogans."
        ),
    },
}

STYLE_BRIEFS = {
    EDITORIAL_PREMIUM: (
        "EDITORIAL PREMIUM, not commercial poster. Magazine-cover real-estate. "
        "Huge hero, lots of air, short serif-like headline, tiny logo. "
        "Supporting photos stay small and secondary. Mood: quiet, elegant, expensive."
    ),
    MODERN_COMMERCIAL: (
        "MODERN COMMERCIAL, not editorial quiet. Selling layout. "
        "Price is a primary fact. CTA is unmistakable. One or two extras help the sale. "
        "Mood: crisp, architectural, high-clarity, still refined."
    ),
    LUXURY_MINIMAL: (
        "LUXURY MINIMAL, not a busy listing card. Image is 80% of the frame. "
        "Copy is tiny and sparse. Branding is a fine wordmark only. "
        "Mood: sober, dark, gallery-like, almost no decoration."
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
)


def approved_style_path():
    path = STYLE_DIR / APPROVED_STYLE_NAME
    return path if path.is_file() else None


def normalize_style(style):
    key = str(style or EDITORIAL_PREMIUM).strip().lower()
    return STYLE_ALIASES.get(key, EDITORIAL_PREMIUM)


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
        "price": "prominent" if key_is_commercial(direction) else "quiet_or_prominent",
        "branding": "navy + electric blue + white",
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
    return composition_key(direction) == MODERN_COMMERCIAL


def safe_area_prompt(fmt):
    area = safe_area(fmt)
    return (
        f"STRICT SAFE AREA for {fmt}: keep EVERY word, logo, price, CTA and agent "
        f"name inside the inner rectangle. Minimum margins: left {area['left']}px, "
        f"right {area['right']}px, top {area['top']}px, bottom {area['bottom']}px. "
        "Nothing may touch or bleed past those margins. Leave real empty air there."
    )
